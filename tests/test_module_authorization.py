"""Automated entitlement & module authorization tests for Entregable 5A (Bloque 2).

Covers:
1. AST purity of services/modules.py and object identity preservation of dependencies/auth.py in main.py.
2. Entitlement graph, is_assignable, mandatory CORE protection, and strict lending_entity_name resolution (no fallback).
3. Fail-closed 403 enforcement on all 12 Lending/Collections endpoints for a CORE-only tenant with zero mutations
   and MODULE_ACCESS_DENIED (scope="TENANT") audit logging, plus RBAC preservation when LENDING is enabled.
4. Immediate active-session revocation when LENDING is disabled and full historical data preservation on reactivation.
5. Auth contracts (/api/auth/login and /api/auth/me) exposing enabled_modules and conditional lending_entity_name
   for default-org (CORE+LENDING), CORE-only tenant, and SUPERADMIN.
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["ENVIRONMENT"] = "development"
os.environ["RENDER"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"

import main
import dependencies.auth as dep_auth
import dependencies.db as dep_db
from models import (
    AuditLogModel,
    InteractionModel,
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentModel,
    PlatformModuleModel,
    TenantModel,
    TenantModuleModel,
    TenantSequenceModel,
    UserModel,
)
from services.modules import (
    ModuleNotFoundError,
    ModuleValidationError,
    get_enabled_module_codes,
    get_lending_entity_name,
    get_tenant_module_config,
    is_module_enabled,
    set_tenant_module_state,
    validate_module_disable,
    validate_module_enable,
    validate_module_set_for_tenant,
)


def _clean_env(extra: dict | None = None) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "RENDER", "ENVIRONMENT")
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["RENDER"] = "false"
    env["ENVIRONMENT"] = "development"
    env["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"
    if extra:
        env.update(extra)
    return env


def _make_sqlite_engine(db_path: Path):
    eng = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.close()

    return eng


@pytest.fixture(scope="session")
def migrated_template_db(tmp_path_factory) -> Path:
    tmpl_dir = tmp_path_factory.mktemp("e5a_b2_template")
    tmpl_db = tmpl_dir / "template_013.db"
    env = _clean_env(
        {
            "MIGRATION_DATABASE_URL": f"sqlite:///{tmpl_db.as_posix()}",
            "DATABASE_URL": f"sqlite:///{tmpl_db.as_posix()}",
        }
    )
    res = subprocess.run(
        [sys.executable, "-B", "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Alembic upgrade failed:\n{res.stdout}\n{res.stderr}"
    return tmpl_db


@pytest.fixture()
def mod_env(migrated_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "mod_auth_case.db"
    shutil.copy2(migrated_template_db, db_file)

    eng = _make_sqlite_engine(db_file)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)

    def _override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[main.get_db] = _override_get_db
    try:
        yield {"db_path": db_file, "engine": eng, "SessionLocal": SessionLocal}
    finally:
        main.app.dependency_overrides.clear()
        eng.dispose()


def _seed_actors(SessionLocal) -> dict:
    db = SessionLocal()
    try:
        platform_t = db.query(TenantModel).filter(TenantModel.slug == "platform").first()
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        default_t.legal_name = "Default Legal Entity S.A. de C.V."

        core_only_t = TenantModel(
            name="Broker Core Org",
            slug="broker-core-org",
            legal_name="Broker Core SAPI de CV",
            is_active=True,
        )
        db.add(core_only_t)
        db.flush()

        for et in ("customers", "leads", "loans", "loan_payments"):
            db.add(TenantSequenceModel(tenant_id=core_only_t.id, entity_type=et, last_value=0))

        # CORE-only tenant gets strictly CORE enabled (no LENDING row)
        db.add(
            TenantModuleModel(
                tenant_id=core_only_t.id,
                module_code="CORE",
                is_enabled=True,
                config_json=None,
            )
        )

        pw = "Password123!"
        pw_hash = main.pwd_context.hash(pw)

        superadmin = UserModel(
            tenant_id=platform_t.id,
            email="super@nura.nura.io",
            full_name="Platform Root",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=True,
            is_active=True,
        )
        default_owner = UserModel(
            tenant_id=default_t.id,
            email="owner@default.nura.io",
            full_name="Default Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        default_sales = UserModel(
            tenant_id=default_t.id,
            email="sales@default.nura.io",
            full_name="Default Sales",
            password_hash=pw_hash,
            role="SALES_AGENT",
            is_superadmin=False,
            is_active=True,
        )
        core_owner = UserModel(
            tenant_id=core_only_t.id,
            email="owner@broker.nura.io",
            full_name="Broker Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        db.add_all([superadmin, default_owner, default_sales, core_owner])
        db.commit()
        return {
            "platform_id": platform_t.id,
            "default_tenant_id": default_t.id,
            "core_tenant_id": core_only_t.id,
            "superadmin_id": superadmin.id,
            "default_owner_id": default_owner.id,
            "default_sales_id": default_sales.id,
            "core_owner_id": core_owner.id,
            "password": pw,
        }
    finally:
        db.close()


def _login_client(email: str, password: str) -> tuple[TestClient, dict, dict]:
    client = TestClient(main.app, base_url="http://localhost")
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    csrf = client.cookies.get("csrf_token")
    assert csrf
    return client, {"Origin": "http://localhost", "X-CSRF-Token": csrf}, r.json()


def test_01_services_modules_ast_purity_and_dependency_identities():
    modules_src = (REPO_ROOT / "services" / "modules.py").read_text(encoding="utf-8")
    tree = ast.parse(modules_src)
    forbidden_roots = {"fastapi", "starlette", "routers", "dependencies", "main"}
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
    assert imported_roots.isdisjoint(forbidden_roots), f"Forbidden imports in services/modules.py: {imported_roots & forbidden_roots}"

    # Verify all existing auth/db dependency symbols preserve exact object identity in main.py
    assert main.get_db is dep_db.get_db
    assert main.verify_csrf is dep_auth.verify_csrf
    assert main.resolve_tenant_owner_user_id is dep_auth.resolve_tenant_owner_user_id
    assert main.get_current_user is dep_auth.get_current_user
    assert main.require_commercial_tenant is dep_auth.require_commercial_tenant
    assert main.require_roles is dep_auth.require_roles
    assert main.require_superadmin is dep_auth.require_superadmin


def test_02_module_service_rules_dependencies_assignability_and_lender_identity(mod_env):
    ids = _seed_actors(mod_env["SessionLocal"])
    db = mod_env["SessionLocal"]()
    try:
        # Backfilled default-org has CORE + LENDING; CORE-only has CORE; platform has []
        assert get_enabled_module_codes(db, ids["default_tenant_id"]) == ["CORE", "LENDING"]
        assert get_enabled_module_codes(db, ids["core_tenant_id"]) == ["CORE"]
        assert get_enabled_module_codes(db, ids["platform_id"]) == []
        assert get_enabled_module_codes(db, ids["default_tenant_id"], is_superadmin=True) == []

        assert is_module_enabled(db, ids["default_tenant_id"], "LENDING") is True
        assert is_module_enabled(db, ids["core_tenant_id"], "LENDING") is False
        assert is_module_enabled(db, ids["core_tenant_id"], "CORE") is True
        assert is_module_enabled(db, ids["platform_id"], "CORE") is False

        # Non-assignable future modules rejected (BROKER, COMMERCE, FINANCING)
        for non_assignable in ("BROKER", "COMMERCE", "FINANCING"):
            with pytest.raises(ModuleValidationError) as exc_info:
                validate_module_enable(db, ids["core_tenant_id"], non_assignable)
            assert exc_info.value.error_code == "MODULE_NOT_ASSIGNABLE"

            with pytest.raises(ModuleValidationError) as exc_set:
                validate_module_set_for_tenant(db, ["CORE", non_assignable])
            assert exc_set.value.error_code == "MODULE_NOT_ASSIGNABLE"

        # Unknown module rejected with ModuleNotFoundError
        with pytest.raises(ModuleNotFoundError):
            validate_module_enable(db, ids["core_tenant_id"], "NON_EXISTENT")

        # Bulk set without CORE rejected
        with pytest.raises(ModuleValidationError) as exc_core:
            validate_module_set_for_tenant(db, ["LENDING"])
        assert exc_core.value.error_code == "MANDATORY_CORE_REQUIRED"

        # Valid bulk sets
        assert validate_module_set_for_tenant(db, ["core"]) == ["CORE"]
        assert validate_module_set_for_tenant(db, ["lending", "core"]) == ["CORE", "LENDING"]

        # CORE cannot be disabled
        with pytest.raises(ModuleValidationError) as exc_disable_core:
            validate_module_disable(db, ids["default_tenant_id"], "CORE")
        assert exc_disable_core.value.error_code == "CORE_MODULE_IMMUTABLE"

        # Dependency enforcement on enable (tenant with 0 modules cannot enable LENDING without CORE)
        bare_tenant = TenantModel(name="Bare Org", slug="bare-org", is_active=True)
        db.add(bare_tenant)
        db.flush()
        with pytest.raises(ModuleValidationError) as exc_dep:
            validate_module_enable(db, bare_tenant.id, "LENDING")
        assert exc_dep.value.error_code == "MISSING_MODULE_DEPENDENCIES"
        assert exc_dep.value.details["missing_dependencies"] == ["CORE"]

        # Dependency enforcement on disable (COMMERCE cannot be disabled while FINANCING is active)
        db.add_all([
            TenantModuleModel(tenant_id=bare_tenant.id, module_code="CORE", is_enabled=True),
            TenantModuleModel(tenant_id=bare_tenant.id, module_code="COMMERCE", is_enabled=True),
            TenantModuleModel(tenant_id=bare_tenant.id, module_code="FINANCING", is_enabled=True),
        ])
        db.flush()
        with pytest.raises(ModuleValidationError) as exc_active_dep:
            validate_module_disable(db, bare_tenant.id, "COMMERCE")
        assert exc_active_dep.value.error_code == "ACTIVE_DEPENDENT_MODULES"
        assert exc_active_dep.value.details["active_dependents"] == ["FINANCING"]

        # Strict lending_entity_name resolution: NO fallback to tenant.legal_name or tenant.name
        assert get_lending_entity_name(db, ids["default_tenant_id"]) is None
        assert get_tenant_module_config(db, ids["default_tenant_id"], "LENDING") is None

        set_tenant_module_state(
            db,
            ids["default_tenant_id"],
            "LENDING",
            is_enabled=True,
            config_json={"lending_entity_name": "  Financiera Alfa S.A.P.I. de C.V.  "},
            update_config=True,
            updated_by_user_id=ids["superadmin_id"],
        )
        db.commit()
        assert get_lending_entity_name(db, ids["default_tenant_id"]) == "Financiera Alfa S.A.P.I. de C.V."
        assert get_tenant_module_config(db, ids["default_tenant_id"], "LENDING") == {
            "lending_entity_name": "Financiera Alfa S.A.P.I. de C.V."
        }

        # Disabling LENDING suppresses lending_entity_name resolution while preserving config_json in DB
        set_tenant_module_state(
            db,
            ids["default_tenant_id"],
            "LENDING",
            is_enabled=False,
            updated_by_user_id=ids["superadmin_id"],
        )
        db.commit()
        assert get_lending_entity_name(db, ids["default_tenant_id"]) is None
        assert get_tenant_module_config(db, ids["default_tenant_id"], "LENDING", require_enabled=False) == {
            "lending_entity_name": "Financiera Alfa S.A.P.I. de C.V."
        }
    finally:
        db.close()


def test_03_core_only_tenant_blocked_403_on_all_12_lending_endpoints_with_audit_and_no_mutations(mod_env):
    ids = _seed_actors(mod_env["SessionLocal"])
    client_def, hdr_def, _ = _login_client("owner@default.nura.io", ids["password"])
    client_sales, hdr_sales, _ = _login_client("sales@default.nura.io", ids["password"])
    client_core, hdr_core, _ = _login_client("owner@broker.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # Seed a real loan & payment in default-org so path IDs exist, and verify RBAC still works when LENDING is enabled
        r_cust_def = client_def.post(
            "/api/customers",
            headers=hdr_def,
            json={"entity_type": "INDIVIDUAL", "first_name": "Ana", "last_name": "Lender", "phone_primary": "5510001001"},
        )
        assert r_cust_def.status_code == 200
        r_loan_def = client_def.post(
            "/api/loans",
            headers=hdr_def,
            json={
                "idempotency_key": "b2-def-loan-1",
                "customer_id": r_cust_def.json()["id"],
                "product_name": "Personal Loan",
                "principal_pesos": 10000.0,
                "monthly_interest_rate_percent": 10.0,
                "assumed_interest_months": 2,
                "term_installments_count": 4,
                "start_date": today_iso,
                "first_payment_date": today_iso,
            },
        )
        assert r_loan_def.status_code == 200
        loan_id = r_loan_def.json()["loan"]["id"]

        # RBAC check preserved for SALES_AGENT on LENDING-enabled tenant (approve requires OWNER_ADMIN/CREDIT_ADVISOR)
        assert client_sales.post(f"/api/loans/{loan_id}/approve", headers=hdr_sales, json={}).status_code == 403
        assert client_def.post(f"/api/loans/{loan_id}/approve", headers=hdr_def, json={}).status_code == 200
        assert (
            client_def.post(
                f"/api/loans/{loan_id}/activate",
                headers=hdr_def,
                json={"confirm_disbursement": True, "disbursement_method": "SPEI_TRANSFER", "disbursement_reference": "B2-DISB-1"},
            ).status_code
            == 200
        )
        r_pay_def = client_def.post(
            f"/api/loans/{loan_id}/payments",
            headers=hdr_def,
            json={
                "idempotency_key": "b2-def-pay-1",
                "amount_pesos": 1000.0,
                "payment_method": "SPEI_TRANSFER",
                "payment_reference": "B2-PAY-1",
            },
        )
        assert r_pay_def.status_code == 200
        pay_id = r_pay_def.json()["payment"]["id"]

        # CORE-only tenant still operates CORE CRM routes normally
        r_core_cust = client_core.post(
            "/api/customers",
            headers=hdr_core,
            json={"entity_type": "INDIVIDUAL", "first_name": "Carlos", "last_name": "Broker", "phone_primary": "5520002002"},
        )
        assert r_core_cust.status_code == 200
        core_cust_id = r_core_cust.json()["id"]
        assert client_core.get("/api/customers").json()["total"] == 1
        assert client_core.get("/api/leads").status_code == 200

        # Snapshot commercial lending counts before negative calls
        db = mod_env["SessionLocal"]()
        try:
            before_counts = (
                db.query(LoanModel).count(),
                db.query(LoanInstallmentModel).count(),
                db.query(LoanPaymentModel).count(),
                db.query(InteractionModel).count(),
            )
        finally:
            db.close()

        # All 14 Lending/Collections endpoints (including Bloque 4 dashboard & customer summary) must return 403 Forbidden
        lending_calls = [
            ("GET", "/api/loans", None),
            ("GET", "/api/loans/dashboard-summary", None),
            ("GET", f"/api/loans/customers/{core_cust_id}/summary", None),
            (
                "POST",
                "/api/loans",
                {
                    "idempotency_key": "core-blocked-loan",
                    "customer_id": core_cust_id,
                    "product_name": "Personal Loan",
                    "principal_pesos": 5000.0,
                    "monthly_interest_rate_percent": 10.0,
                    "assumed_interest_months": 1,
                    "term_installments_count": 4,
                    "start_date": today_iso,
                    "first_payment_date": today_iso,
                },
            ),
            ("GET", f"/api/loans/{loan_id}", None),
            ("POST", f"/api/loans/{loan_id}/approve", {"approval_notes": "Should fail"}),
            (
                "POST",
                f"/api/loans/{loan_id}/activate",
                {"confirm_disbursement": True, "disbursement_method": "SPEI_TRANSFER", "disbursement_reference": "BLK-1"},
            ),
            ("POST", f"/api/loans/{loan_id}/cancel", {"cancellation_reason": "Blocked test"}),
            ("POST", f"/api/loans/{loan_id}/default", {"confirm_default": True, "default_reason": "Blocked test"}),
            (
                "POST",
                f"/api/loans/{loan_id}/collections-log",
                {"interaction_type": "CALL", "outcome": "NO_ANSWER", "notes": "Blocked log"},
            ),
            ("GET", "/api/collections/queue", None),
            ("GET", f"/api/loans/{loan_id}/payments", None),
            (
                "POST",
                f"/api/loans/{loan_id}/payments",
                {"idempotency_key": "core-blocked-pay", "amount_pesos": 500.0, "payment_method": "CASH_RECEIPT"},
            ),
            ("POST", f"/api/loans/{loan_id}/payments/{pay_id}/reverse", {"reversal_reason": "Blocked reversal"}),
        ]
        assert len(lending_calls) == 14

        for method, path, body in lending_calls:
            if method == "GET":
                resp = client_core.get(path)
            else:
                resp = client_core.post(path, headers=hdr_core, json=body)
            assert resp.status_code == 403, f"Expected 403 on {method} {path}, got {resp.status_code}: {resp.text}"
            assert "LENDING" in resp.json()["detail"]

        # Verify zero mutations and 14 MODULE_ACCESS_DENIED audit records (scope="TENANT")
        db = mod_env["SessionLocal"]()
        try:
            after_counts = (
                db.query(LoanModel).count(),
                db.query(LoanInstallmentModel).count(),
                db.query(LoanPaymentModel).count(),
                db.query(InteractionModel).count(),
            )
            assert after_counts == before_counts

            denied_audits = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "MODULE_ACCESS_DENIED",
                    AuditLogModel.tenant_id == ids["core_tenant_id"],
                )
                .order_by(AuditLogModel.id.asc())
                .all()
            )
            assert len(denied_audits) == 14
            audited_paths = []
            for entry in denied_audits:
                assert entry.scope == "TENANT"
                assert entry.tenant_id == ids["core_tenant_id"]
                assert entry.actor_user_id == ids["core_owner_id"]
                assert entry.outcome == "DENIED"
                details = json.loads(entry.details_json)
                assert details["module_code"] == "LENDING"
                assert details["tenant_id"] == str(ids["core_tenant_id"])
                assert details["user_id"] == str(ids["core_owner_id"])
                audited_paths.append(details["path"])
            assert audited_paths == [p for _, p, _ in lending_calls]
        finally:
            db.close()
    finally:
        client_def.close()
        client_sales.close()
        client_core.close()


def test_04_immediate_active_session_revocation_and_historical_data_preservation(mod_env):
    ids = _seed_actors(mod_env["SessionLocal"])
    client_def, hdr_def, _ = _login_client("owner@default.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # While LENDING is active, create a customer and loan in the active session
        r_cust = client_def.post(
            "/api/customers",
            headers=hdr_def,
            json={"entity_type": "INDIVIDUAL", "first_name": "Elena", "last_name": "Activa", "phone_primary": "5530003003"},
        )
        assert r_cust.status_code == 200
        r_loan = client_def.post(
            "/api/loans",
            headers=hdr_def,
            json={
                "idempotency_key": "b2-revoke-loan-1",
                "customer_id": r_cust.json()["id"],
                "product_name": "Personal Loan",
                "principal_pesos": 12000.0,
                "monthly_interest_rate_percent": 10.0,
                "assumed_interest_months": 2,
                "term_installments_count": 4,
                "start_date": today_iso,
                "first_payment_date": today_iso,
            },
        )
        assert r_loan.status_code == 200
        loan_id = r_loan.json()["loan"]["id"]
        loan_number = r_loan.json()["loan"]["loan_number"]
        assert client_def.get("/api/loans").json()["total"] == 1

        # Disable LENDING without touching the user's active session cookies
        db = mod_env["SessionLocal"]()
        try:
            set_tenant_module_state(
                db,
                ids["default_tenant_id"],
                "LENDING",
                is_enabled=False,
                updated_by_user_id=ids["superadmin_id"],
            )
            db.commit()
        finally:
            db.close()

        # Immediate 403 on next request in the same active session (no relogin required)
        assert client_def.get("/api/loans").status_code == 403
        assert client_def.get("/api/loans/dashboard-summary").status_code == 403
        assert client_def.get(f"/api/loans/customers/{r_cust.json()['id']}/summary").status_code == 403
        assert client_def.get(f"/api/loans/{loan_id}").status_code == 403
        assert client_def.get("/api/collections/queue").status_code == 403
        # /api/auth/me dynamically reflects revocation without relogin
        me_revoked = client_def.get("/api/auth/me").json()
        assert me_revoked["enabled_modules"] == ["CORE"]
        assert "lending_entity_name" not in me_revoked

        # Reactivate LENDING: same session immediately regains access with historical loan preserved
        db = mod_env["SessionLocal"]()
        try:
            set_tenant_module_state(
                db,
                ids["default_tenant_id"],
                "LENDING",
                is_enabled=True,
                updated_by_user_id=ids["superadmin_id"],
            )
            db.commit()
        finally:
            db.close()

        r_list_after = client_def.get("/api/loans")
        assert r_list_after.status_code == 200
        assert r_list_after.json()["total"] == 1
        assert r_list_after.json()["items"][0]["loan_number"] == loan_number
        r_det_after = client_def.get(f"/api/loans/{loan_id}")
        assert r_det_after.status_code == 200
        assert r_det_after.json()["principal_cents"] == 1_200_000
    finally:
        client_def.close()


def test_05_auth_contracts_enabled_modules_and_lending_entity_name(mod_env):
    ids = _seed_actors(mod_env["SessionLocal"])

    # 1. default-org (CORE + LENDING, unconfigured lending_entity_name -> explicitly null, no fallback to legal_name)
    c_def, _, login_def = _login_client("owner@default.nura.io", ids["password"])
    try:
        assert login_def["enabled_modules"] == ["CORE", "LENDING"]
        assert "lending_entity_name" in login_def
        assert login_def["lending_entity_name"] is None

        me_def = c_def.get("/api/auth/me").json()
        assert me_def["enabled_modules"] == ["CORE", "LENDING"]
        assert "lending_entity_name" in me_def
        assert me_def["lending_entity_name"] is None

        # Configure lending_entity_name in tenant_modules.config_json and verify dynamic reflection
        db = mod_env["SessionLocal"]()
        try:
            set_tenant_module_state(
                db,
                ids["default_tenant_id"],
                "LENDING",
                is_enabled=True,
                config_json={"lending_entity_name": "Nura Creditos S.A. de C.V. SOFOM ENR"},
                update_config=True,
                updated_by_user_id=ids["superadmin_id"],
            )
            db.commit()
        finally:
            db.close()

        me_def_cfg = c_def.get("/api/auth/me").json()
        assert me_def_cfg["enabled_modules"] == ["CORE", "LENDING"]
        assert me_def_cfg["lending_entity_name"] == "Nura Creditos S.A. de C.V. SOFOM ENR"
    finally:
        c_def.close()

    c_def2, _, login_def2 = _login_client("owner@default.nura.io", ids["password"])
    try:
        assert login_def2["enabled_modules"] == ["CORE", "LENDING"]
        assert login_def2["lending_entity_name"] == "Nura Creditos S.A. de C.V. SOFOM ENR"
    finally:
        c_def2.close()

    # 2. CORE-only tenant (no LENDING -> enabled_modules=["CORE"], lending_entity_name omitted)
    c_core, _, login_core = _login_client("owner@broker.nura.io", ids["password"])
    try:
        assert login_core["enabled_modules"] == ["CORE"]
        assert "lending_entity_name" not in login_core

        me_core = c_core.get("/api/auth/me").json()
        assert me_core["enabled_modules"] == ["CORE"]
        assert "lending_entity_name" not in me_core
    finally:
        c_core.close()

    # 3. SUPERADMIN (platform -> enabled_modules=[], lending_entity_name omitted)
    c_super, _, login_super = _login_client("super@nura.nura.io", ids["password"])
    try:
        assert login_super["enabled_modules"] == []
        assert "lending_entity_name" not in login_super

        me_super = c_super.get("/api/auth/me").json()
        assert me_super["enabled_modules"] == []
        assert "lending_entity_name" not in me_super
    finally:
        c_super.close()
