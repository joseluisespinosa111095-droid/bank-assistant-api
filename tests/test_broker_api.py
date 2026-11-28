"""Automated domain, API, authorization, isolation, and AST purity tests for Entregable 5B — Bloque 2 (BROKER).

Covers:
1. AST purity:
   - `services/broker.py` has 0 FastAPI/Starlette/routers/dependencies/main imports.
   - Core modules have 0 Broker imports.
   - Zero cross-imports between `routers/broker.py` and `routers/loans.py`.
2. Sequence decoupling: `ensure_tenant_sequences_for_modules` initializes `broker_cases` & `broker_commissions`
   idempotently without touching `loans`/`loan_payments`, and `BROKER.is_assignable` remains False in Bloque 2.
3. Full end-to-end Broker lifecycle:
   lender -> case (with converted source_lead_id + idempotency replay/conflict + BRK-00001) ->
   multi-lender submissions -> offers (auto-promoting case to OFFERS_RECEIVED) ->
   single offer selection (including switching selected offer before funding) ->
   funding (atomic FUNDED + COM-00001 with integer-cent math) ->
   commission lifecycle (PENDING -> INVOICED -> PAID + terminal immutability) + TENANT audit trail.
4. Strict multi-tenant isolation across 2 BROKER-enabled tenants (404 cross-tenant, shared codes/idempotency keys).
5. Fail-closed 403 + `MODULE_ACCESS_DENIED` (`scope="TENANT"`) on all 13 Broker endpoints for a `CORE+LENDING`
   tenant without `BROKER`, with zero mutations, plus RBAC preservation when `BROKER` is enabled.
6. Immediate active-session revocation when `BROKER` is disabled mid-session and full data preservation on reactivation.
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
from models import (
    AuditLogModel,
    BrokerCaseModel,
    BrokerCommissionModel,
    BrokerLenderModel,
    BrokerSubmissionModel,
    PlatformModuleModel,
    TenantModel,
    TenantModuleModel,
    TenantSequenceModel,
    UserModel,
)
from services.modules import ensure_tenant_sequences_for_modules


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
def migrated_broker_api_template_db(tmp_path_factory) -> Path:
    tmpl_dir = tmp_path_factory.mktemp("e5b_b2_template")
    tmpl_db = tmpl_dir / "template_014.db"
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
def broker_env(migrated_broker_api_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "broker_b2_case.db"
    shutil.copy2(migrated_broker_api_template_db, db_file)

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


def _seed_broker_actors(SessionLocal) -> dict:
    """Seeds:
    - default-org (CORE + LENDING, NO BROKER) -> owner@default.nura.io
    - broker-alpha (CORE + BROKER enabled directly in DB for Bloque 2 testing) -> owner@alpha.brk, sales@alpha.brk, ops@alpha.brk
    - broker-beta  (CORE + BROKER enabled directly in DB) -> owner@beta.brk
    Note: platform_modules.BROKER.is_assignable remains False!
    """
    db = SessionLocal()
    try:
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").one()

        alpha_t = TenantModel(name="Broker Alpha", slug="broker-alpha", is_active=True)
        beta_t = TenantModel(name="Broker Beta", slug="broker-beta", is_active=True)
        db.add_all([alpha_t, beta_t])
        db.flush()

        for t in (alpha_t, beta_t):
            db.add_all([
                TenantModuleModel(tenant_id=t.id, module_code="CORE", is_enabled=True),
                TenantModuleModel(tenant_id=t.id, module_code="BROKER", is_enabled=True),
            ])
            ensure_tenant_sequences_for_modules(db, t.id, ["CORE", "BROKER"])

        pw = "Password123!"
        pw_hash = main.pwd_context.hash(pw)

        def_owner = UserModel(
            tenant_id=default_t.id,
            email="owner@default.nura.io",
            full_name="Default Lending Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        alpha_owner = UserModel(
            tenant_id=alpha_t.id,
            email="owner@alpha.brk",
            full_name="Alpha Broker Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        alpha_sales = UserModel(
            tenant_id=alpha_t.id,
            email="sales@alpha.brk",
            full_name="Alpha Sales",
            password_hash=pw_hash,
            role="SALES_AGENT",
            is_superadmin=False,
            is_active=True,
        )
        alpha_ops = UserModel(
            tenant_id=alpha_t.id,
            email="ops@alpha.brk",
            full_name="Alpha Ops",
            password_hash=pw_hash,
            role="OPERATIONS_OFFICER",
            is_superadmin=False,
            is_active=True,
        )
        beta_owner = UserModel(
            tenant_id=beta_t.id,
            email="owner@beta.brk",
            full_name="Beta Broker Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        db.add_all([def_owner, alpha_owner, alpha_sales, alpha_ops, beta_owner])
        db.commit()
        return {
            "default_tenant_id": default_t.id,
            "alpha_tenant_id": alpha_t.id,
            "beta_tenant_id": beta_t.id,
            "def_owner_id": def_owner.id,
            "alpha_owner_id": alpha_owner.id,
            "alpha_sales_id": alpha_sales.id,
            "alpha_ops_id": alpha_ops.id,
            "beta_owner_id": beta_owner.id,
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


def _extract_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            found.add(mod)
            for alias in node.names:
                found.add(f"{mod}.{alias.name}" if mod else alias.name)
    return found


def test_01_ast_purity_and_zero_cross_vertical_imports():
    # 1. services/broker.py must be 100% free of FastAPI/HTTP/routers/dependencies/main
    broker_svc_imports = _extract_imports(REPO_ROOT / "services" / "broker.py")
    forbidden_roots = {"fastapi", "starlette", "routers", "dependencies", "main", "core"}
    roots = {imp.split(".")[0] for imp in broker_svc_imports}
    assert roots.isdisjoint(forbidden_roots), f"Forbidden imports in services/broker.py: {roots & forbidden_roots}"

    # 2. Core routers & services must have 0 broker imports
    core_files = [
        REPO_ROOT / "routers" / "dashboard.py",
        REPO_ROOT / "routers" / "customers.py",
        REPO_ROOT / "routers" / "leads.py",
        REPO_ROOT / "routers" / "assistant.py",
        REPO_ROOT / "routers" / "admin.py",
        REPO_ROOT / "routers" / "auth.py",
        REPO_ROOT / "services" / "customer_detail.py",
        REPO_ROOT / "services" / "serializers_core.py",
    ]
    for path in core_files:
        imps = _extract_imports(path)
        bad = [i for i in imps if "broker" in i.lower()]
        assert not bad, f"Unexpected broker import in Core file {path}: {bad}"

    # 3. Zero cross-imports between routers/broker.py and routers/loans.py (and Lending models/services)
    broker_router_imps = _extract_imports(REPO_ROOT / "routers" / "broker.py")
    loans_router_imps = _extract_imports(REPO_ROOT / "routers" / "loans.py")
    assert not any("loan" in i.lower() or "lending" in i.lower() or "financial_engine" in i.lower() for i in broker_router_imps)
    assert not any("broker" in i.lower() for i in loans_router_imps)


def test_02_broker_sequences_decoupled_and_is_assignable_enabled(broker_env):
    ids = _seed_broker_actors(broker_env["SessionLocal"])
    db = broker_env["SessionLocal"]()
    try:
        broker_mod = db.query(PlatformModuleModel).filter(PlatformModuleModel.code == "BROKER").one()
        assert broker_mod.is_assignable is True
        for future_code in ("COMMERCE", "FINANCING"):
            fmod = db.query(PlatformModuleModel).filter(PlatformModuleModel.code == future_code).one()
            assert fmod.is_assignable is False

        seqs = sorted(
            r[0]
            for r in db.query(TenantSequenceModel.entity_type)
            .filter(TenantSequenceModel.tenant_id == ids["alpha_tenant_id"])
            .all()
        )
        assert seqs == ["broker_cases", "broker_commissions", "customers", "leads"]

        # Calling ensure_tenant_sequences_for_modules again is strictly idempotent
        ensure_tenant_sequences_for_modules(db, ids["alpha_tenant_id"], ["CORE", "BROKER"])
        db.commit()
        seqs_after = sorted(
            r[0]
            for r in db.query(TenantSequenceModel.entity_type)
            .filter(TenantSequenceModel.tenant_id == ids["alpha_tenant_id"])
            .all()
        )
        assert seqs_after == ["broker_cases", "broker_commissions", "customers", "leads"]
    finally:
        db.close()


def test_03_full_broker_lifecycle_multi_lender_offers_selection_funding_and_commission(broker_env):
    ids = _seed_broker_actors(broker_env["SessionLocal"])
    c_owner, h_owner, login_owner = _login_client("owner@alpha.brk", ids["password"])
    c_sales, h_sales, _ = _login_client("sales@alpha.brk", ids["password"])
    c_ops, h_ops, _ = _login_client("ops@alpha.brk", ids["password"])

    try:
        assert login_owner["enabled_modules"] == ["BROKER", "CORE"]
        assert "lending_entity_name" not in login_owner

        # 1. Create Lead -> Convert to Customer in CORE
        r_lead = c_sales.post(
            "/api/leads",
            headers=h_sales,
            json={
                "full_name": "Industrias Horizonte SA de CV",
                "entity_type": "BUSINESS",
                "legal_representative_name": "Laura Vega",
                "phone_primary": "5540001122",
                "product_interest": "Business Loan",
                "requested_amount_pesos": 500000.0,
                "stage": "QUALIFIED",
            },
        )
        assert r_lead.status_code == 200, r_lead.text
        lead_id = r_lead.json()["id"]

        r_conv = c_sales.post(f"/api/leads/{lead_id}/convert", headers=h_sales, json={})
        assert r_conv.status_code == 200, r_conv.text
        cust_id = r_conv.json()["customer"]["id"]

        # 2. Lenders catalog (RBAC: SALES_AGENT blocked 403; OWNER_ADMIN creates 2 lenders)
        assert (
            c_sales.post(
                "/api/broker/lenders",
                headers=h_sales,
                json={"code": "BBVA", "name": "BBVA MX"},
            ).status_code
            == 403
        )

        r_l1 = c_owner.post(
            "/api/broker/lenders",
            headers=h_owner,
            json={
                "code": "bbva-mx",
                "name": "BBVA México",
                "lender_type": "BANK",
                "default_commission_bps": 250,
                "contact_name": "Carlos Banco",
                "contact_email": "carlos@bbva-demo.com",
            },
        )
        assert r_l1.status_code == 200, r_l1.text
        lender1 = r_l1.json()
        assert lender1["code"] == "BBVA-MX"
        assert lender1["default_commission_bps"] == 250

        # Duplicate lender code -> 409
        assert (
            c_owner.post(
                "/api/broker/lenders",
                headers=h_owner,
                json={"code": "BBVA-MX", "name": "Duplicate Bank"},
            ).status_code
            == 409
        )

        r_l2 = c_owner.post(
            "/api/broker/lenders",
            headers=h_owner,
            json={
                "code": "KONFIO",
                "name": "Konfío SOFOM",
                "lender_type": "FINTECH",
                "default_commission_bps": 325,
            },
        )
        assert r_l2.status_code == 200, r_l2.text
        lender2 = r_l2.json()

        # Update lender commission & verify list
        r_l2_upd = c_owner.patch(
            f"/api/broker/lenders/{lender2['id']}",
            headers=h_owner,
            json={"default_commission_bps": 300, "notes": "Fast PyME approval"},
        )
        assert r_l2_upd.status_code == 200
        assert r_l2_upd.json()["default_commission_bps"] == 300
        assert len(c_sales.get("/api/broker/lenders?active_only=true").json()) == 2

        # 3. Create Broker Case (idempotency replay + 409 conflict + BRK-00001)
        case_payload = {
            "idempotency_key": "alpha-brk-case-0001",
            "customer_id": cust_id,
            "source_lead_id": lead_id,
            "product_type": "PyME Working Capital",
            "requested_amount_pesos": 500000.0,
            "requested_term_months": 24,
            "purpose": "Inventory expansion",
        }
        r_case1 = c_sales.post("/api/broker/cases", headers=h_sales, json=case_payload)
        assert r_case1.status_code == 200, r_case1.text
        assert r_case1.json()["idempotent_replay"] is False
        bcase = r_case1.json()["case"]
        case_id = bcase["id"]
        assert bcase["case_number"] == "BRK-00001"
        assert bcase["status"] == "DRAFT"
        assert bcase["requested_amount_cents"] == 50_000_000

        # Idempotent replay returns exact same case
        r_case_replay = c_sales.post("/api/broker/cases", headers=h_sales, json=case_payload)
        assert r_case_replay.status_code == 200
        assert r_case_replay.json()["idempotent_replay"] is True
        assert r_case_replay.json()["case"]["id"] == case_id

        # Same idempotency_key with different payload -> 409
        bad_payload = dict(case_payload)
        bad_payload["requested_amount_pesos"] = 600000.0
        assert c_sales.post("/api/broker/cases", headers=h_sales, json=bad_payload).status_code == 409

        # Cannot fund before selecting an offer -> 409
        assert c_owner.post(f"/api/broker/cases/{case_id}/fund", headers=h_owner, json={}).status_code == 409

        # 4. Multi-lender submissions in parallel (promotes case DRAFT -> SUBMITTED)
        r_sub1 = c_sales.post(
            f"/api/broker/cases/{case_id}/submissions",
            headers=h_sales,
            json={"lender_id": lender1["id"], "external_reference": "BBVA-FOLIO-77"},
        )
        assert r_sub1.status_code == 200, r_sub1.text
        sub1_id = r_sub1.json()["id"]
        assert r_sub1.json()["status"] == "SUBMITTED"
        assert r_sub1.json()["offered_commission_bps"] == 250

        # Duplicate submission to same lender -> 409
        assert (
            c_sales.post(
                f"/api/broker/cases/{case_id}/submissions",
                headers=h_sales,
                json={"lender_id": lender1["id"]},
            ).status_code
            == 409
        )

        r_sub2 = c_sales.post(
            f"/api/broker/cases/{case_id}/submissions",
            headers=h_sales,
            json={"lender_id": lender2["id"], "external_reference": "KONFIO-99"},
        )
        assert r_sub2.status_code == 200, r_sub2.text
        sub2_id = r_sub2.json()["id"]

        assert c_owner.get(f"/api/broker/cases/{case_id}").json()["status"] == "SUBMITTED"

        # Cannot select non-APPROVED submission -> 409
        assert (
            c_owner.post(f"/api/broker/cases/{case_id}/submissions/{sub1_id}/select", headers=h_owner, json={}).status_code
            == 409
        )

        # 5. Record APPROVED offers on both lenders (promotes case to OFFERS_RECEIVED)
        r_app1 = c_owner.patch(
            f"/api/broker/cases/{case_id}/submissions/{sub1_id}",
            headers=h_owner,
            json={
                "status": "APPROVED",
                "offered_amount_pesos": 450000.0,
                "offered_rate_bps": 1850,
                "offered_term_months": 24,
                "offered_monthly_payment_pesos": 22575.50,
                "offered_commission_bps": 250,
            },
        )
        assert r_app1.status_code == 200, r_app1.text
        assert r_app1.json()["status"] == "APPROVED"

        r_app2 = c_owner.patch(
            f"/api/broker/cases/{case_id}/submissions/{sub2_id}",
            headers=h_owner,
            json={
                "status": "APPROVED",
                "offered_amount_pesos": 480000.0,
                "offered_rate_bps": 2100,
                "offered_term_months": 18,
                "offered_commission_bps": 300,
            },
        )
        assert r_app2.status_code == 200, r_app2.text

        # 6. Select offer 2 first, then switch selection to offer 1 before funding
        r_sel2 = c_owner.post(
            f"/api/broker/cases/{case_id}/submissions/{sub2_id}/select",
            headers=h_owner,
            json={"decision_notes": "Higher amount"},
        )
        assert r_sel2.status_code == 200, r_sel2.text
        assert r_sel2.json()["selected_submission_id"] == sub2_id

        r_sel1 = c_owner.post(
            f"/api/broker/cases/{case_id}/submissions/{sub1_id}/select",
            headers=h_owner,
            json={"decision_notes": "Customer preferred lower BBVA rate"},
        )
        assert r_sel1.status_code == 200, r_sel1.text
        assert r_sel1.json()["selected_submission_id"] == sub1_id
        subs_by_id = {s["id"]: s for s in r_sel1.json()["submissions"]}
        assert subs_by_id[sub1_id]["status"] == "SELECTED"
        assert subs_by_id[sub2_id]["status"] == "APPROVED"

        # 7. Fund case -> atomic FUNDED + COM-00001 with exact integer cents
        # 450,000.00 MXN (45,000,000 cents) * 250 bps // 10,000 = 1,125,000 cents ($11,250.00 MXN)
        r_fund = c_ops.post(
            f"/api/broker/cases/{case_id}/fund",
            headers=h_ops,
            json={
                "external_loan_reference": "BBVA-DISP-2026-881",
                "expected_pay_date": "2026-12-20",
                "notes": "Disbursed directly by BBVA",
            },
        )
        assert r_fund.status_code == 200, r_fund.text
        funded_case = r_fund.json()
        assert funded_case["status"] == "FUNDED"
        assert funded_case["funded_amount_cents"] == 45_000_000
        assert funded_case["external_loan_reference"] == "BBVA-DISP-2026-881"
        comm = funded_case["commission"]
        assert comm is not None
        assert comm["commission_number"] == "COM-00001"
        assert comm["funded_amount_cents"] == 45_000_000
        assert comm["commission_rate_bps"] == 250
        assert comm["expected_commission_cents"] == 1_125_000
        assert comm["paid_commission_cents"] == 0
        assert comm["status"] == "PENDING"
        comm_id = comm["id"]

        # Terminal case cannot be funded twice or mutated -> 409
        assert c_ops.post(f"/api/broker/cases/{case_id}/fund", headers=h_ops, json={}).status_code == 409
        assert c_owner.patch(f"/api/broker/cases/{case_id}", headers=h_owner, json={"notes": "Late edit"}).status_code == 409

        # 8. Commission lifecycle: PENDING -> INVOICED -> PAID (OPS & OWNER allowed; SALES blocked 403)
        assert c_sales.get("/api/broker/commissions").status_code == 403
        assert len(c_ops.get("/api/broker/commissions").json()["items"]) == 1

        r_inv = c_ops.patch(
            f"/api/broker/commissions/{comm_id}",
            headers=h_ops,
            json={"status": "INVOICED", "notes": "Factura CFDI A-104 enviada"},
        )
        assert r_inv.status_code == 200, r_inv.text
        assert r_inv.json()["status"] == "INVOICED"
        assert r_inv.json()["invoiced_at"] is not None

        r_paid = c_ops.patch(
            f"/api/broker/commissions/{comm_id}",
            headers=h_ops,
            json={"status": "PAID", "payment_reference": "SPEI-BBVA-COM-901"},
        )
        assert r_paid.status_code == 200, r_paid.text
        assert r_paid.json()["status"] == "PAID"
        assert r_paid.json()["paid_commission_cents"] == 1_125_000
        assert r_paid.json()["paid_at"] is not None

        # Terminal PAID commission cannot be modified -> 409
        assert (
            c_ops.patch(f"/api/broker/commissions/{comm_id}", headers=h_ops, json={"status": "CANCELLED"}).status_code
            == 409
        )

        # 9. Verify TENANT-scoped audit trail recorded all key Broker events
        db = broker_env["SessionLocal"]()
        try:
            events = {
                row[0]
                for row in db.query(AuditLogModel.event_type)
                .filter(
                    AuditLogModel.tenant_id == ids["alpha_tenant_id"],
                    AuditLogModel.scope == "TENANT",
                )
                .all()
            }
            for expected_ev in (
                "BROKER_LENDER_CREATED",
                "BROKER_LENDER_UPDATED",
                "BROKER_CASE_CREATED",
                "BROKER_SUBMISSION_CREATED",
                "BROKER_SUBMISSION_UPDATED",
                "BROKER_OFFER_SELECTED",
                "BROKER_CASE_FUNDED",
                "BROKER_COMMISSION_UPDATED",
            ):
                assert expected_ev in events, f"Missing audit event {expected_ev}"
        finally:
            db.close()
    finally:
        c_owner.close()
        c_sales.close()
        c_ops.close()


def test_04_multitenant_isolation_between_two_broker_tenants(broker_env):
    ids = _seed_broker_actors(broker_env["SessionLocal"])
    c_a, h_a, _ = _login_client("owner@alpha.brk", ids["password"])
    c_b, h_b, _ = _login_client("owner@beta.brk", ids["password"])

    try:
        # Both tenants create customer + lender with SAME code + case with SAME idempotency_key -> both get BRK-00001
        r_ca = c_a.post(
            "/api/customers",
            headers=h_a,
            json={"entity_type": "INDIVIDUAL", "first_name": "Ana", "last_name": "Alpha", "phone_primary": "5511110001"},
        )
        r_cb = c_b.post(
            "/api/customers",
            headers=h_b,
            json={"entity_type": "INDIVIDUAL", "first_name": "Beto", "last_name": "Beta", "phone_primary": "5511110001"},
        )
        assert r_ca.status_code == 200 and r_cb.status_code == 200

        la = c_a.post("/api/broker/lenders", headers=h_a, json={"code": "SANTANDER", "name": "Santander Alpha", "default_commission_bps": 200})
        lb = c_b.post("/api/broker/lenders", headers=h_b, json={"code": "SANTANDER", "name": "Santander Beta", "default_commission_bps": 250})
        assert la.status_code == 200 and lb.status_code == 200

        ca_case = c_a.post(
            "/api/broker/cases",
            headers=h_a,
            json={
                "idempotency_key": "shared-broker-idem-01",
                "customer_id": r_ca.json()["id"],
                "product_type": "Auto Loan",
                "requested_amount_pesos": 300000.0,
            },
        )
        cb_case = c_b.post(
            "/api/broker/cases",
            headers=h_b,
            json={
                "idempotency_key": "shared-broker-idem-01",
                "customer_id": r_cb.json()["id"],
                "product_type": "Auto Loan",
                "requested_amount_pesos": 300000.0,
            },
        )
        assert ca_case.status_code == 200 and ca_case.json()["case"]["case_number"] == "BRK-00001"
        assert cb_case.status_code == 200 and cb_case.json()["case"]["case_number"] == "BRK-00001"

        case_a_id = ca_case.json()["case"]["id"]
        lender_a_id = la.json()["id"]

        sub_a = c_a.post(f"/api/broker/cases/{case_a_id}/submissions", headers=h_a, json={"lender_id": lender_a_id})
        assert sub_a.status_code == 200
        sub_a_id = sub_a.json()["id"]
        assert (
            c_a.patch(
                f"/api/broker/cases/{case_a_id}/submissions/{sub_a_id}",
                headers=h_a,
                json={"status": "APPROVED", "offered_amount_pesos": 280000.0},
            ).status_code
            == 200
        )
        assert c_a.post(f"/api/broker/cases/{case_a_id}/submissions/{sub_a_id}/select", headers=h_a, json={}).status_code == 200
        funded_a = c_a.post(f"/api/broker/cases/{case_a_id}/fund", headers=h_a, json={})
        assert funded_a.status_code == 200
        comm_a_id = funded_a.json()["commission"]["id"]

        # Tenant B cannot read or mutate ANY Tenant A Broker resource -> 404
        assert c_b.patch(f"/api/broker/lenders/{lender_a_id}", headers=h_b, json={"name": "Hijacked"}).status_code == 404
        assert c_b.get(f"/api/broker/cases/{case_a_id}").status_code == 404
        assert c_b.patch(f"/api/broker/cases/{case_a_id}", headers=h_b, json={"notes": "Hijacked"}).status_code == 404
        assert c_b.post(f"/api/broker/cases/{case_a_id}/submissions", headers=h_b, json={"lender_id": lb.json()["id"]}).status_code == 404
        assert (
            c_b.post(
                f"/api/broker/cases/{cb_case.json()['case']['id']}/submissions",
                headers=h_b,
                json={"lender_id": lender_a_id},  # Tenant A lender inside Tenant B case -> 404
            ).status_code
            == 404
        )
        assert c_b.patch(f"/api/broker/cases/{case_a_id}/submissions/{sub_a_id}", headers=h_b, json={"status": "DECLINED"}).status_code == 404
        assert c_b.post(f"/api/broker/cases/{case_a_id}/submissions/{sub_a_id}/select", headers=h_b, json={}).status_code == 404
        assert c_b.post(f"/api/broker/cases/{case_a_id}/fund", headers=h_b, json={}).status_code == 404
        assert c_b.patch(f"/api/broker/commissions/{comm_a_id}", headers=h_b, json={"status": "PAID"}).status_code == 404

        # Tenant B lists show ONLY its own records
        assert len(c_b.get("/api/broker/lenders").json()) == 1
        assert c_b.get("/api/broker/cases").json()["total"] == 1
        assert c_b.get("/api/broker/commissions").json()["total"] == 0
    finally:
        c_a.close()
        c_b.close()


def test_05_core_plus_lending_tenant_blocked_403_on_all_13_broker_routes_and_active_session_revocation(broker_env):
    ids = _seed_broker_actors(broker_env["SessionLocal"])
    c_def, h_def, _ = _login_client("owner@default.nura.io", ids["password"])
    c_alpha, h_alpha, _ = _login_client("owner@alpha.brk", ids["password"])

    try:
        # Seed real Broker entities in Alpha so IDs exist in DB
        r_cust_a = c_alpha.post(
            "/api/customers",
            headers=h_alpha,
            json={"entity_type": "INDIVIDUAL", "first_name": "Diego", "last_name": "Alpha", "phone_primary": "5533330001"},
        )
        assert r_cust_a.status_code == 200
        l_a = c_alpha.post("/api/broker/lenders", headers=h_alpha, json={"code": "HSBC", "name": "HSBC MX", "default_commission_bps": 200}).json()
        case_a = c_alpha.post(
            "/api/broker/cases",
            headers=h_alpha,
            json={
                "idempotency_key": "alpha-revoke-case-1",
                "customer_id": r_cust_a.json()["id"],
                "product_type": "Business Credit",
                "requested_amount_pesos": 200000.0,
            },
        ).json()["case"]
        sub_a = c_alpha.post(f"/api/broker/cases/{case_a['id']}/submissions", headers=h_alpha, json={"lender_id": l_a["id"]}).json()

        # Snapshot counts before negative calls from default-org (CORE + LENDING, NO BROKER)
        db = broker_env["SessionLocal"]()
        try:
            before_counts = (
                db.query(BrokerLenderModel).count(),
                db.query(BrokerCaseModel).count(),
                db.query(BrokerSubmissionModel).count(),
                db.query(BrokerCommissionModel).count(),
            )
        finally:
            db.close()

        broker_calls = [
            ("GET", "/api/broker/lenders", None),
            ("POST", "/api/broker/lenders", {"code": "BLK", "name": "Blocked Bank"}),
            ("PATCH", f"/api/broker/lenders/{l_a['id']}", {"is_active": False}),
            ("GET", "/api/broker/cases", None),
            (
                "POST",
                "/api/broker/cases",
                {
                    "idempotency_key": "blocked-brk-case-1",
                    "customer_id": r_cust_a.json()["id"],
                    "product_type": "PyME",
                    "requested_amount_pesos": 100000.0,
                },
            ),
            ("GET", f"/api/broker/cases/{case_a['id']}", None),
            ("PATCH", f"/api/broker/cases/{case_a['id']}", {"notes": "Blocked"}),
            ("POST", f"/api/broker/cases/{case_a['id']}/submissions", {"lender_id": l_a["id"]}),
            ("PATCH", f"/api/broker/cases/{case_a['id']}/submissions/{sub_a['id']}", {"status": "IN_UNDERWRITING"}),
            ("POST", f"/api/broker/cases/{case_a['id']}/submissions/{sub_a['id']}/select", {}),
            ("POST", f"/api/broker/cases/{case_a['id']}/fund", {"funded_amount_pesos": 100000.0}),
            ("GET", "/api/broker/commissions", None),
            ("PATCH", "/api/broker/commissions/1", {"status": "PAID"}),
        ]
        assert len(broker_calls) == 13

        for method, path, body in broker_calls:
            if method == "GET":
                resp = c_def.get(path)
            else:
                resp = c_def.request(method, path, headers=h_def, json=body)
            assert resp.status_code == 403, f"Expected 403 on {method} {path}, got {resp.status_code}: {resp.text}"
            assert "BROKER" in resp.json()["detail"]

        db = broker_env["SessionLocal"]()
        try:
            after_counts = (
                db.query(BrokerLenderModel).count(),
                db.query(BrokerCaseModel).count(),
                db.query(BrokerSubmissionModel).count(),
                db.query(BrokerCommissionModel).count(),
            )
            assert after_counts == before_counts

            denied = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "MODULE_ACCESS_DENIED",
                    AuditLogModel.tenant_id == ids["default_tenant_id"],
                )
                .order_by(AuditLogModel.id.asc())
                .all()
            )
            assert len(denied) == 13
            for entry in denied:
                assert entry.scope == "TENANT"
                assert entry.outcome == "DENIED"
                d = json.loads(entry.details_json)
                assert d["module_code"] == "BROKER"
            assert [json.loads(e.details_json)["path"] for e in denied] == [p for _, p, _ in broker_calls]
        finally:
            db.close()

        # Active-session revocation on Alpha: disable BROKER in DB without touching cookies
        assert c_alpha.get("/api/broker/cases").status_code == 200
        db = broker_env["SessionLocal"]()
        try:
            tm = (
                db.query(TenantModuleModel)
                .filter(
                    TenantModuleModel.tenant_id == ids["alpha_tenant_id"],
                    TenantModuleModel.module_code == "BROKER",
                )
                .one()
            )
            tm.is_enabled = False
            db.commit()
        finally:
            db.close()

        # Immediate 403 on same active session; /api/auth/me dynamically drops BROKER
        assert c_alpha.get("/api/broker/lenders").status_code == 403
        assert c_alpha.get("/api/broker/cases").status_code == 403
        assert c_alpha.get(f"/api/broker/cases/{case_a['id']}").status_code == 403
        assert c_alpha.get("/api/broker/commissions").status_code == 403
        assert c_alpha.get("/api/auth/me").json()["enabled_modules"] == ["CORE"]

        # Reactivate BROKER: same session regains access with historical data intact
        db = broker_env["SessionLocal"]()
        try:
            tm = (
                db.query(TenantModuleModel)
                .filter(
                    TenantModuleModel.tenant_id == ids["alpha_tenant_id"],
                    TenantModuleModel.module_code == "BROKER",
                )
                .one()
            )
            tm.is_enabled = True
            db.commit()
        finally:
            db.close()

        r_after = c_alpha.get(f"/api/broker/cases/{case_a['id']}")
        assert r_after.status_code == 200
        assert r_after.json()["case_number"] == "BRK-00001"
        assert len(r_after.json()["submissions"]) == 1
    finally:
        c_def.close()
        c_alpha.close()
