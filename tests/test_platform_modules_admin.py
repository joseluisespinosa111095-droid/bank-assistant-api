"""Automated platform module catalog, decoupled onboarding, and module admin tests (Entregable 5A - Bloque 3).

Covers:
1. GET /api/platform/modules visibility (5 modules, is_assignable, dependencies) and SUPERADMIN-only access.
2. CORE-only tenant onboarding: creates ONLY customers & leads in tenant_sequences (no loans/loan_payments),
   and tenant can immediately use CORE CRM while being blocked 403 on Lending.
3. CORE + LENDING onboarding with explicit lending_entity_name (and verifying NO fallback to legal_name/name when omitted).
4. HTTP 400 rejections on non-assignable modules (BROKER, COMMERCE, FINANCING), disabling CORE, modifying platform tenant,
   missing dependencies, and disabling a module with an active dependent.
5. Post-onboarding activation of LENDING (idempotently creates loans/loan_payments sequences), updating lending_entity_name,
   deactivating LENDING without losing historical loan data, and verifying PLATFORM_TENANT_MODULES_UPDATED audit logs
   (scope='PLATFORM', before/after).
"""
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
from models import (
    AuditLogModel,
    LoanModel,
    TenantModel,
    TenantModuleModel,
    TenantSequenceModel,
    UserModel,
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
    tmpl_dir = tmp_path_factory.mktemp("e5a_b3_template")
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
def b3_env(migrated_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "b3_case.db"
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


def _seed_superadmin_and_default_owner(SessionLocal) -> dict:
    db = SessionLocal()
    try:
        platform_t = db.query(TenantModel).filter(TenantModel.slug == "platform").first()
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        pw = "Password123!"
        pw_hash = main.pwd_context.hash(pw)

        superadmin = UserModel(
            tenant_id=platform_t.id,
            email="super@nura.nura.io",
            full_name="Platform Superadmin",
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
        db.add_all([superadmin, default_owner])
        db.commit()
        return {
            "platform_id": platform_t.id,
            "default_tenant_id": default_t.id,
            "superadmin_id": superadmin.id,
            "default_owner_id": default_owner.id,
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


def test_01_platform_modules_catalog_visibility_and_guard(b3_env):
    ids = _seed_superadmin_and_default_owner(b3_env["SessionLocal"])
    c_super, _, _ = _login_client("super@nura.nura.io", ids["password"])
    c_owner, _, _ = _login_client("owner@default.nura.io", ids["password"])
    try:
        # Commercial OWNER_ADMIN blocked 403
        assert c_owner.get("/api/platform/modules").status_code == 403

        # SUPERADMIN sees all 5 seeded modules with exact flags and dependencies
        r = c_super.get("/api/platform/modules")
        assert r.status_code == 200, r.text
        catalog = {m["code"]: m for m in r.json()}
        assert set(catalog.keys()) == {"CORE", "LENDING", "BROKER", "COMMERCE", "FINANCING"}

        assert catalog["CORE"]["is_core_mandatory"] is True
        assert catalog["CORE"]["is_assignable"] is True
        assert catalog["CORE"]["is_active"] is True
        assert catalog["CORE"]["dependencies"] == []

        assert catalog["LENDING"]["is_core_mandatory"] is False
        assert catalog["LENDING"]["is_assignable"] is True
        assert catalog["LENDING"]["dependencies"] == ["CORE"]

        for future_code, expected_deps in (
            ("BROKER", ["CORE"]),
            ("COMMERCE", ["CORE"]),
            ("FINANCING", ["COMMERCE", "CORE"]),
        ):
            assert catalog[future_code]["is_core_mandatory"] is False
            assert catalog[future_code]["is_assignable"] is False
            assert catalog[future_code]["is_active"] is True
            assert catalog[future_code]["dependencies"] == expected_deps
    finally:
        c_super.close()
        c_owner.close()


def test_02_core_only_onboarding_decouples_sequences_and_blocks_lending(b3_env):
    ids = _seed_superadmin_and_default_owner(b3_env["SessionLocal"])
    c_super, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    try:
        r_create = c_super.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "Broker Franchise MX",
                "slug": "broker-franchise-mx",
                "legal_name": "Broker Franchise S.A.P.I. de C.V.",
                "owner_email": "owner@broker-franchise.nura.io",
                "owner_password": "BrokerPassword123!",
                "owner_full_name": "Broker Owner",
                "modules": [],
            },
        )
        assert r_create.status_code == 200, r_create.text
        body = r_create.json()
        tenant_id = body["tenant"]["id"]
        assert body["enabled_modules"] == ["CORE"]
        assert body["tenant"]["enabled_modules"] == ["CORE"]
        assert body["tenant"]["lending_entity_name"] is None

        # Verify tenant_sequences has ONLY customers and leads (no loans, no loan_payments)
        db = b3_env["SessionLocal"]()
        try:
            seq_entities = sorted(
                row[0]
                for row in db.query(TenantSequenceModel.entity_type)
                .filter(TenantSequenceModel.tenant_id == tenant_id)
                .all()
            )
            assert seq_entities == ["customers", "leads"]

            # Audit PLATFORM_TENANT_CREATED includes enabled_modules
            audit = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "PLATFORM_TENANT_CREATED",
                    AuditLogModel.resource_id == str(tenant_id),
                )
                .first()
            )
            assert audit is not None
            assert audit.scope == "PLATFORM"
            details = json.loads(audit.details_json)
            assert details["enabled_modules"] == "CORE"
        finally:
            db.close()

        # Verify tenant list and detail expose enabled_modules
        tenants_by_slug = {t["slug"]: t for t in c_super.get("/api/platform/tenants").json()}
        assert tenants_by_slug["broker-franchise-mx"]["enabled_modules"] == ["CORE"]
        assert tenants_by_slug["broker-franchise-mx"]["lending_entity_name"] is None
        assert tenants_by_slug["platform"]["enabled_modules"] == []

        detail = c_super.get(f"/api/platform/tenants/{tenant_id}").json()
        assert detail["enabled_modules"] == ["CORE"]
        assert detail["lending_entity_name"] is None

        # Broker owner logs in -> CORE works, LENDING returns 403
        c_broker, hdr_b, login_b = _login_client("owner@broker-franchise.nura.io", "BrokerPassword123!")
        try:
            assert login_b["enabled_modules"] == ["CORE"]
            assert "lending_entity_name" not in login_b
            assert c_broker.post(
                "/api/customers",
                headers=hdr_b,
                json={"entity_type": "INDIVIDUAL", "first_name": "Luis", "last_name": "Core", "phone_primary": "5511223344"},
            ).status_code == 200
            assert c_broker.get("/api/loans").status_code == 403
        finally:
            c_broker.close()
    finally:
        c_super.close()


def test_03_core_plus_lending_onboarding_with_and_without_lending_entity_name(b3_env):
    ids = _seed_superadmin_and_default_owner(b3_env["SessionLocal"])
    c_super, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    try:
        # Case A: LENDING enabled with explicit lending_entity_name
        r_with = c_super.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "Financiera Sol",
                "slug": "financiera-sol",
                "legal_name": "Grupo Sol Holdings S.A.",
                "owner_email": "owner@sol.nura.io",
                "owner_password": "SolPassword123!",
                "modules": ["LENDING"],
                "lending_entity_name": "Financiera Sol S.A. de C.V. SOFOM ENR",
            },
        )
        assert r_with.status_code == 200, r_with.text
        sol_id = r_with.json()["tenant"]["id"]
        assert r_with.json()["enabled_modules"] == ["CORE", "LENDING"]
        assert r_with.json()["tenant"]["lending_entity_name"] == "Financiera Sol S.A. de C.V. SOFOM ENR"

        # Case B: LENDING enabled WITHOUT lending_entity_name (must remain None, NO fallback to legal_name/name)
        r_without = c_super.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "Financiera Luna",
                "slug": "financiera-luna",
                "legal_name": "Luna Legal S.A.",
                "owner_email": "owner@luna.nura.io",
                "owner_password": "LunaPassword123!",
                "modules": ["CORE", "LENDING"],
            },
        )
        assert r_without.status_code == 200, r_without.text
        luna_id = r_without.json()["tenant"]["id"]
        assert r_without.json()["enabled_modules"] == ["CORE", "LENDING"]
        assert r_without.json()["tenant"]["lending_entity_name"] is None

        # Verify all 4 sequences initialized for LENDING-enabled tenants
        db = b3_env["SessionLocal"]()
        try:
            for tid in (sol_id, luna_id):
                seq_entities = sorted(
                    row[0]
                    for row in db.query(TenantSequenceModel.entity_type)
                    .filter(TenantSequenceModel.tenant_id == tid)
                    .all()
                )
                assert seq_entities == ["customers", "leads", "loan_payments", "loans"]
        finally:
            db.close()

        # Verify /api/auth/me exposes exact lending_entity_name without fallback
        c_sol, _, login_sol = _login_client("owner@sol.nura.io", "SolPassword123!")
        c_luna, _, login_luna = _login_client("owner@luna.nura.io", "LunaPassword123!")
        try:
            assert login_sol["lending_entity_name"] == "Financiera Sol S.A. de C.V. SOFOM ENR"
            assert login_luna["lending_entity_name"] is None
        finally:
            c_sol.close()
            c_luna.close()
    finally:
        c_super.close()


def test_04_rejections_400_for_non_assignable_core_disable_platform_and_dependencies(b3_env):
    ids = _seed_superadmin_and_default_owner(b3_env["SessionLocal"])
    c_super, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    try:
        # 1. Onboarding rejects BROKER, COMMERCE, FINANCING (400)
        for bad_mod in ("BROKER", "COMMERCE", "FINANCING"):
            r_bad = c_super.post(
                "/api/platform/tenants",
                headers=hdr_s,
                json={
                    "name": f"Bad {bad_mod}",
                    "slug": f"bad-{bad_mod.lower()}",
                    "owner_email": f"owner@{bad_mod.lower()}.nura.io",
                    "owner_password": "Password123!",
                    "modules": [bad_mod],
                },
            )
            assert r_bad.status_code == 400, f"Expected 400 for onboarding with {bad_mod}, got {r_bad.status_code}: {r_bad.text}"

        # 2. Onboarding rejects lending_entity_name when LENDING is not enabled
        r_bad_lender = c_super.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "No Lending Org",
                "slug": "no-lending-org",
                "owner_email": "owner@nolending.nura.io",
                "owner_password": "Password123!",
                "modules": [],
                "lending_entity_name": "Should Fail SA",
            },
        )
        assert r_bad_lender.status_code == 400

        # 3. PATCH rejects BROKER, COMMERCE, FINANCING on existing tenant
        for bad_mod in ("BROKER", "COMMERCE", "FINANCING"):
            r_patch_bad = c_super.patch(
                f"/api/platform/tenants/{ids['default_tenant_id']}/modules",
                headers=hdr_s,
                json={"modules": [{"module_code": bad_mod, "is_enabled": True}]},
            )
            assert r_patch_bad.status_code == 400

        # 4. PATCH rejects disabling CORE
        r_disable_core = c_super.patch(
            f"/api/platform/tenants/{ids['default_tenant_id']}/modules",
            headers=hdr_s,
            json={"modules": [{"module_code": "CORE", "is_enabled": False}]},
        )
        assert r_disable_core.status_code == 400

        # 5. PATCH rejects modifying platform tenant modules
        r_plat_mod = c_super.patch(
            f"/api/platform/tenants/{ids['platform_id']}/modules",
            headers=hdr_s,
            json={"modules": [{"module_code": "LENDING", "is_enabled": True}]},
        )
        assert r_plat_mod.status_code == 400

        # 6. PATCH rejects missing dependency on enable & active dependent on disable
        db = b3_env["SessionLocal"]()
        try:
            bare_t = TenantModel(name="Dep Test Org", slug="dep-test-org", is_active=True)
            db.add(bare_t)
            db.flush()
            bare_id = bare_t.id
            db.commit()
        finally:
            db.close()

        # Enabling LENDING without CORE -> 400
        assert (
            c_super.patch(
                f"/api/platform/tenants/{bare_id}/modules",
                headers=hdr_s,
                json={"modules": [{"module_code": "LENDING", "is_enabled": True}]},
            ).status_code
            == 400
        )

        # Disabling COMMERCE while FINANCING is active -> 400
        db = b3_env["SessionLocal"]()
        try:
            db.add_all([
                TenantModuleModel(tenant_id=bare_id, module_code="CORE", is_enabled=True),
                TenantModuleModel(tenant_id=bare_id, module_code="COMMERCE", is_enabled=True),
                TenantModuleModel(tenant_id=bare_id, module_code="FINANCING", is_enabled=True),
            ])
            db.commit()
        finally:
            db.close()

        assert (
            c_super.patch(
                f"/api/platform/tenants/{bare_id}/modules",
                headers=hdr_s,
                json={"modules": [{"module_code": "COMMERCE", "is_enabled": False}]},
            ).status_code
            == 400
        )

        # Missing tenant -> 404
        assert (
            c_super.patch(
                "/api/platform/tenants/999999/modules",
                headers=hdr_s,
                json={"modules": [{"module_code": "LENDING", "is_enabled": False}]},
            ).status_code
            == 404
        )
    finally:
        c_super.close()


def test_05_post_onboarding_lending_activation_sequences_config_deactivation_and_audit(b3_env):
    ids = _seed_superadmin_and_default_owner(b3_env["SessionLocal"])
    c_super, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # 1. Create CORE-only tenant
        r_new = c_super.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "Upgradeable Org",
                "slug": "upgradeable-org",
                "legal_name": "Upgradeable Holdings SA",
                "owner_email": "owner@upgradeable.nura.io",
                "owner_password": "UpgradePassword123!",
                "modules": [],
            },
        )
        assert r_new.status_code == 200, r_new.text
        tid = r_new.json()["tenant"]["id"]

        # 2. Activate LENDING later + set lending_entity_name -> initializes loans & loan_payments idempotently
        r_enable = c_super.patch(
            f"/api/platform/tenants/{tid}/modules",
            headers=hdr_s,
            json={
                "modules": [
                    {
                        "module_code": "LENDING",
                        "is_enabled": True,
                        "config_json": {"lending_entity_name": "Acreditante Upgradeable S.A. de C.V."},
                    }
                ]
            },
        )
        assert r_enable.status_code == 200, r_enable.text
        assert r_enable.json()["enabled_modules"] == ["CORE", "LENDING"]
        assert r_enable.json()["lending_entity_name"] == "Acreditante Upgradeable S.A. de C.V."

        db = b3_env["SessionLocal"]()
        try:
            seq_map = {
                row.entity_type: row.last_value
                for row in db.query(TenantSequenceModel).filter(TenantSequenceModel.tenant_id == tid).all()
            }
            assert seq_map == {"customers": 0, "leads": 0, "loans": 0, "loan_payments": 0}
        finally:
            db.close()

        # 3. Tenant creates customer + loan -> sequence advances to LN-00001
        c_org, hdr_org, _ = _login_client("owner@upgradeable.nura.io", "UpgradePassword123!")
        try:
            r_cust = c_org.post(
                "/api/customers",
                headers=hdr_org,
                json={"entity_type": "INDIVIDUAL", "first_name": "Sofia", "last_name": "Mendez", "phone_primary": "5588990011"},
            )
            assert r_cust.status_code == 200
            r_loan = c_org.post(
                "/api/loans",
                headers=hdr_org,
                json={
                    "idempotency_key": "b3-upg-loan-1",
                    "customer_id": r_cust.json()["id"],
                    "product_name": "Personal Loan",
                    "principal_pesos": 20000.0,
                    "monthly_interest_rate_percent": 10.0,
                    "assumed_interest_months": 2,
                    "term_installments_count": 4,
                    "start_date": today_iso,
                    "first_payment_date": today_iso,
                },
            )
            assert r_loan.status_code == 200, r_loan.text
            assert r_loan.json()["loan"]["loan_number"] == "LN-00001"

            # 4. Disable LENDING via SUPERADMIN -> immediate 403, historical loan & sequence value preserved
            r_disable = c_super.patch(
                f"/api/platform/tenants/{tid}/modules",
                headers=hdr_s,
                json={"modules": [{"module_code": "LENDING", "is_enabled": False}]},
            )
            assert r_disable.status_code == 200, r_disable.text
            assert r_disable.json()["enabled_modules"] == ["CORE"]
            assert r_disable.json()["lending_entity_name"] is None
            assert c_org.get("/api/loans").status_code == 403

            # 5. Re-enable LENDING -> idempotent sequence init MUST NOT reset last_value=1, historical loan intact
            r_reenable = c_super.patch(
                f"/api/platform/tenants/{tid}/modules",
                headers=hdr_s,
                json={
                    "modules": [
                        {
                            "module_code": "LENDING",
                            "is_enabled": True,
                            "lending_entity_name": "Acreditante Renombrada S.A.",
                        }
                    ]
                },
            )
            assert r_reenable.status_code == 200, r_reenable.text
            assert r_reenable.json()["lending_entity_name"] == "Acreditante Renombrada S.A."
            assert c_org.get("/api/loans").json()["total"] == 1

            # Verify next loan gets LN-00002 (sequence not reset)
            r_loan2 = c_org.post(
                "/api/loans",
                headers=hdr_org,
                json={
                    "idempotency_key": "b3-upg-loan-2",
                    "customer_id": r_cust.json()["id"],
                    "product_name": "Personal Loan",
                    "principal_pesos": 5000.0,
                    "monthly_interest_rate_percent": 10.0,
                    "assumed_interest_months": 1,
                    "term_installments_count": 4,
                    "start_date": today_iso,
                    "first_payment_date": today_iso,
                },
            )
            assert r_loan2.status_code == 200, r_loan2.text
            assert r_loan2.json()["loan"]["loan_number"] == "LN-00002"
        finally:
            c_org.close()

        # 6. Verify PLATFORM_TENANT_MODULES_UPDATED audit logs (scope='PLATFORM', before/after) & preserved DB rows
        db = b3_env["SessionLocal"]()
        try:
            assert db.query(LoanModel).filter(LoanModel.tenant_id == tid).count() == 2
            audits = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "PLATFORM_TENANT_MODULES_UPDATED",
                    AuditLogModel.resource_id == str(tid),
                )
                .order_by(AuditLogModel.id.asc())
                .all()
            )
            assert len(audits) == 3
            for a in audits:
                assert a.scope == "PLATFORM"
                assert a.tenant_id is None
                d = json.loads(a.details_json)
                assert "before" in d and "after" in d

            first_d = json.loads(audits[0].details_json)
            assert json.loads(first_d["before"]) == {"enabled_modules": ["CORE"], "lending_entity_name": None}
            assert json.loads(first_d["after"]) == {
                "enabled_modules": ["CORE", "LENDING"],
                "lending_entity_name": "Acreditante Upgradeable S.A. de C.V.",
            }

            second_d = json.loads(audits[1].details_json)
            assert json.loads(second_d["after"]) == {"enabled_modules": ["CORE"], "lending_entity_name": None}

            third_d = json.loads(audits[2].details_json)
            assert json.loads(third_d["after"]) == {
                "enabled_modules": ["CORE", "LENDING"],
                "lending_entity_name": "Acreditante Renombrada S.A.",
            }
        finally:
            db.close()
    finally:
        c_super.close()


def test_06_long_lending_entity_name_audit_integrity_and_financing_enable_dep(b3_env):
    """Regression for 5A audit findings:
    1. A valid 200-char lending_entity_name (with accents/quotes) MUST NOT be truncated or corrupt JSON in
       PLATFORM_TENANT_MODULES_UPDATED audit logs (both `after` and subsequent `before`).
    2. Dependency graph enforces FINANCING -> COMMERCE on enable/onboarding when assignable.
    """
    from models import PlatformModuleModel
    from services.modules import (
        ModuleValidationError,
        validate_module_enable,
        validate_module_set_for_tenant,
    )

    ids = _seed_superadmin_and_default_owner(b3_env["SessionLocal"])
    c_super, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    try:
        # Exactly 200 characters (maximum allowed by MAX_LENDING_ENTITY_NAME_LEN) including accents & quotes
        prefix = 'Financiera "Crédito Óptimo" S.A.P.I. de C.V., SOFOM, E.N.R. — '
        long_lender = (prefix + ("X" * 200))[:200]
        assert len(long_lender) == 200

        r_set = c_super.patch(
            f"/api/platform/tenants/{ids['default_tenant_id']}/modules",
            headers=hdr_s,
            json={
                "modules": [
                    {
                        "module_code": "LENDING",
                        "is_enabled": True,
                        "lending_entity_name": long_lender,
                    }
                ]
            },
        )
        assert r_set.status_code == 200, r_set.text
        assert r_set.json()["lending_entity_name"] == long_lender

        # Perform a second update so `before` also carries the 200-char lender name
        r_set2 = c_super.patch(
            f"/api/platform/tenants/{ids['default_tenant_id']}/modules",
            headers=hdr_s,
            json={
                "modules": [
                    {
                        "module_code": "LENDING",
                        "is_enabled": True,
                        "lending_entity_name": "Short Lender S.A.",
                    }
                ]
            },
        )
        assert r_set2.status_code == 200, r_set2.text

        db = b3_env["SessionLocal"]()
        try:
            audits = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "PLATFORM_TENANT_MODULES_UPDATED",
                    AuditLogModel.resource_id == str(ids["default_tenant_id"]),
                )
                .order_by(AuditLogModel.id.asc())
                .all()
            )
            assert len(audits) == 2

            d1 = json.loads(audits[0].details_json)
            assert json.loads(d1["before"]) == {"enabled_modules": ["CORE", "LENDING"], "lending_entity_name": None}
            assert json.loads(d1["after"]) == {
                "enabled_modules": ["CORE", "LENDING"],
                "lending_entity_name": long_lender,
            }

            d2 = json.loads(audits[1].details_json)
            assert json.loads(d2["before"]) == {
                "enabled_modules": ["CORE", "LENDING"],
                "lending_entity_name": long_lender,
            }
            assert json.loads(d2["after"]) == {
                "enabled_modules": ["CORE", "LENDING"],
                "lending_entity_name": "Short Lender S.A.",
            }

            # Verify FINANCING -> COMMERCE dependency enforcement on enable when assignable
            db.query(PlatformModuleModel).filter(
                PlatformModuleModel.code.in_(["COMMERCE", "FINANCING"])
            ).update({"is_assignable": True}, synchronize_session=False)
            db.flush()

            with pytest.raises(ModuleValidationError) as exc_en:
                validate_module_enable(db, ids["default_tenant_id"], "FINANCING")
            assert exc_en.value.error_code == "MISSING_MODULE_DEPENDENCIES"
            assert exc_en.value.details["missing_dependencies"] == ["COMMERCE"]

            with pytest.raises(ModuleValidationError) as exc_set:
                validate_module_set_for_tenant(db, ["CORE", "FINANCING"])
            assert exc_set.value.error_code == "MISSING_MODULE_DEPENDENCIES"
            assert exc_set.value.details["missing_dependencies"] == ["COMMERCE"]

            assert validate_module_set_for_tenant(db, ["CORE", "COMMERCE", "FINANCING"]) == [
                "COMMERCE",
                "CORE",
                "FINANCING",
            ]
        finally:
            db.rollback()
            db.close()
    finally:
        c_super.close()
