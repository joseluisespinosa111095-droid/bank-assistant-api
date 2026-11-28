"""Automated Broker decoupling & LENDING + BROKER coexistence tests (Entregable 5B — Bloque 3).

Covers:
1. AST purity confirming 0 Broker imports in Core modules (routers/dashboard.py,
   services/customer_detail.py, routers/customers.py, routers/leads.py,
   services/serializers_core.py), 0 FastAPI/HTTP or Lending imports in services/broker.py,
   0 cross-imports between routers/broker.py and routers/loans.py, and BROKER.is_assignable == False.
2. Exact integer-cent arithmetic on GET /api/broker/dashboard-summary and
   GET /api/broker/customers/{customer_id}/summary across all 6 case statuses and all 4 commission
   statuses, plus strict multi-tenant isolation (404 cross-tenant customer summary, zero cross-tenant leaks).
3. 403 Forbidden + MODULE_ACCESS_DENIED (scope='TENANT') on both summary endpoints for tenants without
   BROKER (CORE-only and CORE+LENDING), plus immediate hot revocation & reactivation on active session.
4. Full coexistence of all 3 commercial profiles (CORE-only, CORE + BROKER, CORE + LENDING + BROKER):
   /api/dashboard/summary and /api/customers/{id} remain strictly Core; in CORE + LENDING + BROKER,
   loans (LN-/PMT-) and broker cases/commissions (BRK-/COM-) coexist on the same customer with
   independent tenant_sequences and zero cross-vertical leakage.
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
    PlatformModuleModel,
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
def migrated_broker_b3_template(tmp_path_factory) -> Path:
    tmpl_dir = tmp_path_factory.mktemp("broker_b3_template")
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
    assert res.returncode == 0, f"Alembic upgrade head failed:\n{res.stdout}\n{res.stderr}"
    return tmpl_db


@pytest.fixture()
def b3_env(migrated_broker_b3_template: Path, tmp_path: Path):
    db_file = tmp_path / "broker_b3_case.db"
    shutil.copy2(migrated_broker_b3_template, db_file)

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


def _login_client(email: str, password: str) -> tuple[TestClient, dict, dict]:
    client = TestClient(main.app, base_url="http://localhost")
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {email}: {resp.status_code} {resp.text}"
    csrf = resp.cookies.get("csrf_token") or client.cookies.get("csrf_token")
    assert csrf, "Missing csrf_token cookie"
    return client, {"Origin": "http://localhost", "X-CSRF-Token": csrf}, resp.json()


def _collect_imports(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            found.add(mod)
            for alias in node.names:
                found.add(f"{mod}.{alias.name}" if mod else alias.name)
                found.add(alias.name)
    return found


def _create_funded_case(
    client: TestClient,
    headers: dict,
    *,
    idem_key: str,
    customer_id: int,
    lender_id: int,
    requested_pesos: float,
    funded_pesos: float,
    commission_bps: int,
    product_type: str = "PyME Working Capital",
    external_ref: str = "EXT-REF-001",
) -> dict:
    r_case = client.post(
        "/api/broker/cases",
        headers=headers,
        json={
            "idempotency_key": idem_key,
            "customer_id": customer_id,
            "product_type": product_type,
            "requested_amount_pesos": requested_pesos,
            "requested_term_months": 24,
        },
    )
    assert r_case.status_code == 200, r_case.text
    case_id = r_case.json()["case"]["id"]

    r_sub = client.post(
        f"/api/broker/cases/{case_id}/submissions",
        headers=headers,
        json={"lender_id": lender_id, "offered_commission_bps": commission_bps},
    )
    assert r_sub.status_code == 200, r_sub.text
    sub_id = r_sub.json()["id"]

    r_app = client.patch(
        f"/api/broker/cases/{case_id}/submissions/{sub_id}",
        headers=headers,
        json={
            "status": "APPROVED",
            "offered_amount_pesos": funded_pesos,
            "offered_rate_bps": 1800,
            "offered_term_months": 24,
            "offered_commission_bps": commission_bps,
        },
    )
    assert r_app.status_code == 200, r_app.text

    r_sel = client.post(
        f"/api/broker/cases/{case_id}/submissions/{sub_id}/select",
        headers=headers,
        json={},
    )
    assert r_sel.status_code == 200, r_sel.text

    r_fund = client.post(
        f"/api/broker/cases/{case_id}/fund",
        headers=headers,
        json={
            "funded_amount_pesos": funded_pesos,
            "commission_rate_bps": commission_bps,
            "external_loan_reference": external_ref,
        },
    )
    assert r_fund.status_code == 200, r_fund.text
    return r_fund.json()


# ---------------------------------------------------------------------------
# 1. AST PURITY & ZERO CROSS-VERTICAL IMPORTS + is_assignable == False
# ---------------------------------------------------------------------------
def test_01_ast_purity_zero_broker_imports_in_core_and_zero_cross_vertical_imports(b3_env):
    broker_tokens = (
        "broker",
        "BrokerLenderModel",
        "BrokerCaseModel",
        "BrokerSubmissionModel",
        "BrokerCommissionModel",
    )
    lending_tokens = (
        "routers.loans",
        "schemas.lending",
        "services.financial_engine",
        "services.serializers_lending",
        "LoanModel",
        "LoanPaymentModel",
        "LoanInstallmentModel",
    )
    http_tokens = ("fastapi", "starlette", "routers", "dependencies", "main")

    # 1. Core modules: 0 Broker imports
    for rel in (
        "routers/dashboard.py",
        "services/customer_detail.py",
        "routers/customers.py",
        "routers/leads.py",
        "services/serializers_core.py",
    ):
        imports = _collect_imports(REPO_ROOT / rel)
        bad = [imp for imp in imports if any(tok.lower() in imp.lower() for tok in broker_tokens)]
        assert not bad, f"Core file {rel} must have 0 Broker imports, found: {bad}"

    # 2. services/broker.py: 0 FastAPI/HTTP and 0 Lending imports
    svc_imports = _collect_imports(REPO_ROOT / "services/broker.py")
    bad_http = [
        imp
        for imp in svc_imports
        if any(imp == tok or imp.startswith(f"{tok}.") for tok in http_tokens)
    ]
    assert not bad_http, f"services/broker.py must be HTTP/FastAPI-free, found: {bad_http}"
    bad_lending_svc = [imp for imp in svc_imports if any(tok in imp for tok in lending_tokens)]
    assert not bad_lending_svc, f"services/broker.py must have 0 Lending imports, found: {bad_lending_svc}"

    # 3. Zero cross-imports between routers/broker.py and routers/loans.py
    broker_router_imports = _collect_imports(REPO_ROOT / "routers/broker.py")
    bad_lending_router = [imp for imp in broker_router_imports if any(tok in imp for tok in lending_tokens)]
    assert not bad_lending_router, f"routers/broker.py must have 0 Lending imports, found: {bad_lending_router}"

    loans_router_imports = _collect_imports(REPO_ROOT / "routers/loans.py")
    bad_broker_in_loans = [imp for imp in loans_router_imports if any(tok.lower() in imp.lower() for tok in broker_tokens)]
    assert not bad_broker_in_loans, f"routers/loans.py must have 0 Broker imports, found: {bad_broker_in_loans}"

    # 4. BROKER.is_assignable is True in Bloque 5; COMMERCE & FINANCING remain False
    db = b3_env["SessionLocal"]()
    try:
        brk_mod = db.query(PlatformModuleModel).filter(PlatformModuleModel.code == "BROKER").one()
        assert brk_mod.is_assignable is True
        for future_code in ("COMMERCE", "FINANCING"):
            fmod = db.query(PlatformModuleModel).filter(PlatformModuleModel.code == future_code).one()
            assert fmod.is_assignable is False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. EXACT ARITHMETIC ON DASHBOARD & CUSTOMER SUMMARIES + MULTITENANT ISOLATION
# ---------------------------------------------------------------------------
def test_02_broker_dashboard_and_customer_summary_exact_arithmetic_and_multitenant_isolation(b3_env):
    db = b3_env["SessionLocal"]()
    pw = "Password123!"
    pw_hash = main.pwd_context.hash(pw)
    try:
        t_alpha = TenantModel(name="Alpha Brokers", slug="alpha-brokers-b3", is_active=True)
        t_beta = TenantModel(name="Beta Brokers", slug="beta-brokers-b3", is_active=True)
        db.add_all([t_alpha, t_beta])
        db.flush()

        for t in (t_alpha, t_beta):
            for et in ("customers", "leads", "broker_cases", "broker_commissions"):
                db.add(TenantSequenceModel(tenant_id=t.id, entity_type=et, last_value=0))
            for mc in ("CORE", "BROKER"):
                db.add(TenantModuleModel(tenant_id=t.id, module_code=mc, is_enabled=True))

        db.add_all(
            [
                UserModel(
                    tenant_id=t_alpha.id,
                    email="owner@alpha-b3.com",
                    full_name="Alpha Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
                UserModel(
                    tenant_id=t_beta.id,
                    email="owner@beta-b3.com",
                    full_name="Beta Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    c_alpha, h_alpha, _ = _login_client("owner@alpha-b3.com", pw)
    c_beta, h_beta, _ = _login_client("owner@beta-b3.com", pw)

    try:
        # Empty dashboard summary sanity check
        empty_dash = c_alpha.get("/api/broker/dashboard-summary")
        assert empty_dash.status_code == 200, empty_dash.text
        ed = empty_dash.json()
        assert ed["active_pipeline_cents"] == 0
        assert ed["funded_volume_cents"] == 0
        assert ed["active_lenders_count"] == 0
        assert ed["counts"]["total_cases"] == 0
        assert ed["commissions"]["total_earned_cents"] == 0
        assert ed["recent_cases"] == []

        # Create 2 lenders on Alpha (deactivate 1)
        l1 = c_alpha.post(
            "/api/broker/lenders",
            headers=h_alpha,
            json={"code": "BBVA", "name": "BBVA MX", "lender_type": "BANK", "default_commission_bps": 250},
        ).json()
        l2 = c_alpha.post(
            "/api/broker/lenders",
            headers=h_alpha,
            json={"code": "KONFIO", "name": "Konfio", "lender_type": "FINTECH", "default_commission_bps": 300},
        ).json()

        # Create 2 customers on Alpha
        cust1 = c_alpha.post(
            "/api/customers",
            headers=h_alpha,
            json={"entity_type": "INDIVIDUAL", "first_name": "Elena", "last_name": "Rios", "phone_primary": "5511001100"},
        ).json()
        cust2 = c_alpha.post(
            "/api/customers",
            headers=h_alpha,
            json={"entity_type": "INDIVIDUAL", "first_name": "Marco", "last_name": "Diaz", "phone_primary": "5511002200"},
        ).json()

        # --- Alpha Case 1 (cust1): DRAFT ($100,000 -> 10_000_000 cents) ---
        assert (
            c_alpha.post(
                "/api/broker/cases",
                headers=h_alpha,
                json={
                    "idempotency_key": "alpha-b3-case-01",
                    "customer_id": cust1["id"],
                    "product_type": "PyME",
                    "requested_amount_pesos": 100000.0,
                },
            ).status_code
            == 200
        )

        # --- Alpha Case 2 (cust1): SUBMITTED ($250,000 -> 25_000_000 cents), 1 submission ---
        c2 = c_alpha.post(
            "/api/broker/cases",
            headers=h_alpha,
            json={
                "idempotency_key": "alpha-b3-case-02",
                "customer_id": cust1["id"],
                "product_type": "PyME",
                "requested_amount_pesos": 250000.0,
            },
        ).json()["case"]
        assert (
            c_alpha.post(
                f"/api/broker/cases/{c2['id']}/submissions",
                headers=h_alpha,
                json={"lender_id": l1["id"]},
            ).status_code
            == 200
        )

        # --- Alpha Case 3 (cust1): OFFERS_RECEIVED ($300,000 -> 30_000_000 cents), 2 subs (1 APPROVED, 1 DECLINED) ---
        c3 = c_alpha.post(
            "/api/broker/cases",
            headers=h_alpha,
            json={
                "idempotency_key": "alpha-b3-case-03",
                "customer_id": cust1["id"],
                "product_type": "Equipment",
                "requested_amount_pesos": 300000.0,
            },
        ).json()["case"]
        s3_1 = c_alpha.post(
            f"/api/broker/cases/{c3['id']}/submissions", headers=h_alpha, json={"lender_id": l1["id"]}
        ).json()
        s3_2 = c_alpha.post(
            f"/api/broker/cases/{c3['id']}/submissions", headers=h_alpha, json={"lender_id": l2["id"]}
        ).json()
        assert (
            c_alpha.patch(
                f"/api/broker/cases/{c3['id']}/submissions/{s3_1['id']}",
                headers=h_alpha,
                json={"status": "APPROVED", "offered_amount_pesos": 280000.0, "offered_rate_bps": 1900},
            ).status_code
            == 200
        )
        assert (
            c_alpha.patch(
                f"/api/broker/cases/{c3['id']}/submissions/{s3_2['id']}",
                headers=h_alpha,
                json={"status": "DECLINED"},
            ).status_code
            == 200
        )

        # --- Alpha Case 4 (cust1): FUNDED $400,000 @ 250 bps -> $10,000 (1_000_000 cents) PENDING ---
        _create_funded_case(
            c_alpha,
            h_alpha,
            idem_key="alpha-b3-case-04",
            customer_id=cust1["id"],
            lender_id=l1["id"],
            requested_pesos=400000.0,
            funded_pesos=400000.0,
            commission_bps=250,
            external_ref="BBVA-004",
        )

        # --- Alpha Case 5 (cust1): FUNDED $500,000 @ 300 bps -> $15,000 (1_500_000 cents) INVOICED ---
        f5 = _create_funded_case(
            c_alpha,
            h_alpha,
            idem_key="alpha-b3-case-05",
            customer_id=cust1["id"],
            lender_id=l2["id"],
            requested_pesos=500000.0,
            funded_pesos=500000.0,
            commission_bps=300,
            external_ref="KONFIO-005",
        )
        assert (
            c_alpha.patch(
                f"/api/broker/commissions/{f5['commission']['id']}",
                headers=h_alpha,
                json={"status": "INVOICED"},
            ).status_code
            == 200
        )

        # --- Alpha Case 6 (cust2): FUNDED $200,000 @ 275 bps -> $5,500 (550_000 cents) PAID ---
        f6 = _create_funded_case(
            c_alpha,
            h_alpha,
            idem_key="alpha-b3-case-06",
            customer_id=cust2["id"],
            lender_id=l1["id"],
            requested_pesos=200000.0,
            funded_pesos=200000.0,
            commission_bps=275,
            external_ref="BBVA-006",
        )
        assert (
            c_alpha.patch(
                f"/api/broker/commissions/{f6['commission']['id']}",
                headers=h_alpha,
                json={"status": "PAID", "payment_reference": "SPEI-COM-06"},
            ).status_code
            == 200
        )

        # --- Alpha Case 7 (cust2): FUNDED $100,000 @ 200 bps -> $2,000 (200_000 cents) CANCELLED ---
        f7 = _create_funded_case(
            c_alpha,
            h_alpha,
            idem_key="alpha-b3-case-07",
            customer_id=cust2["id"],
            lender_id=l2["id"],
            requested_pesos=100000.0,
            funded_pesos=100000.0,
            commission_bps=200,
            external_ref="KONFIO-007",
        )
        assert (
            c_alpha.patch(
                f"/api/broker/commissions/{f7['commission']['id']}",
                headers=h_alpha,
                json={"status": "CANCELLED"},
            ).status_code
            == 200
        )

        # --- Alpha Case 8 (cust2): CLOSED_LOST ($150,000) ---
        c8 = c_alpha.post(
            "/api/broker/cases",
            headers=h_alpha,
            json={
                "idempotency_key": "alpha-b3-case-08",
                "customer_id": cust2["id"],
                "product_type": "Bridge",
                "requested_amount_pesos": 150000.0,
            },
        ).json()["case"]
        assert (
            c_alpha.patch(
                f"/api/broker/cases/{c8['id']}",
                headers=h_alpha,
                json={"status": "CLOSED_LOST", "closed_reason": "Client chose competitor"},
            ).status_code
            == 200
        )

        # --- Alpha Case 9 (cust2): CANCELLED ($80,000) ---
        c9 = c_alpha.post(
            "/api/broker/cases",
            headers=h_alpha,
            json={
                "idempotency_key": "alpha-b3-case-09",
                "customer_id": cust2["id"],
                "product_type": "Auto",
                "requested_amount_pesos": 80000.0,
            },
        ).json()["case"]
        assert (
            c_alpha.patch(
                f"/api/broker/cases/{c9['id']}",
                headers=h_alpha,
                json={"status": "CANCELLED", "closed_reason": "Withdrawn by client"},
            ).status_code
            == 200
        )

        # Deactivate lender 2 so Alpha has 1 active lender out of 2 total
        assert (
            c_alpha.patch(
                f"/api/broker/lenders/{l2['id']}",
                headers=h_alpha,
                json={"is_active": False},
            ).status_code
            == 200
        )

        # --- Seed Beta tenant data to prove 0 cross-tenant contamination ---
        l_beta = c_beta.post(
            "/api/broker/lenders",
            headers=h_beta,
            json={"code": "SANTANDER", "name": "Santander MX", "default_commission_bps": 400},
        ).json()
        cust_beta = c_beta.post(
            "/api/customers",
            headers=h_beta,
            json={"entity_type": "INDIVIDUAL", "first_name": "Beta", "last_name": "Client", "phone_primary": "5599001122"},
        ).json()
        _create_funded_case(
            c_beta,
            h_beta,
            idem_key="beta-b3-case-01",
            customer_id=cust_beta["id"],
            lender_id=l_beta["id"],
            requested_pesos=999000.0,
            funded_pesos=999000.0,
            commission_bps=400,
            external_ref="SANT-999",
        )

        # --- Verify Alpha Dashboard Summary exact arithmetic ---
        r_dash_a = c_alpha.get("/api/broker/dashboard-summary")
        assert r_dash_a.status_code == 200, r_dash_a.text
        da = r_dash_a.json()

        assert da["counts"] == {
            "total_cases": 9,
            "active_cases": 3,
            "draft_cases": 1,
            "submitted_cases": 1,
            "offers_received_cases": 1,
            "funded_cases": 4,
            "closed_lost_cases": 1,
            "cancelled_cases": 1,
            "active_lenders": 1,
            "total_lenders": 2,
        }
        assert da["cases_by_status"] == {
            "DRAFT": 1,
            "SUBMITTED": 1,
            "OFFERS_RECEIVED": 1,
            "FUNDED": 4,
            "CLOSED_LOST": 1,
            "CANCELLED": 1,
        }
        assert da["active_lenders_count"] == 1
        assert da["total_lenders_count"] == 2
        # Active pipeline = DRAFT ($100k) + SUBMITTED ($250k) + OFFERS_RECEIVED ($300k) = $650,000
        assert da["active_pipeline_cents"] == 65_000_000
        # Funded volume = $400k + $500k + $200k + $100k = $1,200,000
        assert da["funded_volume_cents"] == 120_000_000

        assert da["commissions"] == {
            "pending_count": 1,
            "pending_cents": 1_000_000,
            "invoiced_count": 1,
            "invoiced_cents": 1_500_000,
            "paid_count": 1,
            "paid_cents": 550_000,
            "cancelled_count": 1,
            "cancelled_cents": 200_000,
            "total_earned_count": 3,
            "total_earned_cents": 3_050_000,
            "total_expected_cents": 3_050_000,
        }
        assert len(da["recent_cases"]) == 9
        assert all(c["customer_name"] != "Beta Client" for c in da["recent_cases"])

        # Recent limit parameter works
        r_dash_lim = c_alpha.get("/api/broker/dashboard-summary?recent_limit=3")
        assert r_dash_lim.status_code == 200
        assert len(r_dash_lim.json()["recent_cases"]) == 3

        # --- Verify Alpha Customer 1 Summary exact arithmetic ---
        r_cs1 = c_alpha.get(f"/api/broker/customers/{cust1['id']}/summary")
        assert r_cs1.status_code == 200, r_cs1.text
        cs1 = r_cs1.json()
        assert cs1["customer_id"] == cust1["id"]
        bs1 = cs1["broker_summary"]
        assert bs1["total_cases"] == 5
        assert bs1["active_cases"] == 3
        assert bs1["funded_cases"] == 2
        assert bs1["closed_lost_cases"] == 0
        assert bs1["cancelled_cases"] == 0
        # Submissions on cust1: Case2(1) + Case3(2: 1 APPROVED, 1 DECLINED) + Case4(1 SELECTED) + Case5(1 SELECTED) = 5
        assert bs1["total_submissions"] == 5
        assert bs1["approved_offers"] == 3
        assert bs1["selected_offers"] == 2
        assert bs1["active_pipeline_cents"] == 65_000_000
        assert bs1["total_funded_cents"] == 90_000_000
        assert bs1["commissions"] == {
            "pending_count": 1,
            "pending_cents": 1_000_000,
            "invoiced_count": 1,
            "invoiced_cents": 1_500_000,
            "paid_count": 0,
            "paid_cents": 0,
            "cancelled_count": 0,
            "cancelled_cents": 0,
            "total_earned_count": 2,
            "total_earned_cents": 2_500_000,
            "total_expected_cents": 2_500_000,
        }
        assert len(cs1["cases"]) == 5
        assert len(cs1["commissions"]) == 2

        # --- Verify Beta Dashboard Summary isolated ---
        r_dash_b = c_beta.get("/api/broker/dashboard-summary")
        assert r_dash_b.status_code == 200
        db_sum = r_dash_b.json()
        assert db_sum["counts"]["total_cases"] == 1
        assert db_sum["counts"]["funded_cases"] == 1
        assert db_sum["counts"]["active_lenders"] == 1
        assert db_sum["active_pipeline_cents"] == 0
        assert db_sum["funded_volume_cents"] == 99_900_000
        assert db_sum["commissions"]["pending_cents"] == 3_996_000  # 999,000 * 4%
        assert db_sum["commissions"]["total_earned_cents"] == 3_996_000
        assert len(db_sum["recent_cases"]) == 1

        # --- Cross-tenant customer summary returns 404 ---
        assert c_alpha.get(f"/api/broker/customers/{cust_beta['id']}/summary").status_code == 404
        assert c_beta.get(f"/api/broker/customers/{cust1['id']}/summary").status_code == 404
        assert c_alpha.get("/api/broker/customers/999999/summary").status_code == 404
    finally:
        c_alpha.close()
        c_beta.close()


# ---------------------------------------------------------------------------
# 3. 403 + MODULE_ACCESS_DENIED + HOT SESSION REVOCATION ON NEW ENDPOINTS
# ---------------------------------------------------------------------------
def test_03_new_broker_summary_endpoints_blocked_403_with_audit_and_active_session_revocation(b3_env):
    db = b3_env["SessionLocal"]()
    pw = "Password123!"
    pw_hash = main.pwd_context.hash(pw)
    try:
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").one()
        core_t = TenantModel(name="Core Only Co", slug="core-only-b3", is_active=True)
        brk_t = TenantModel(name="Broker Revoke Co", slug="broker-revoke-b3", is_active=True)
        db.add_all([core_t, brk_t])
        db.flush()

        for et in ("customers", "leads"):
            db.add(TenantSequenceModel(tenant_id=core_t.id, entity_type=et, last_value=0))
        db.add(TenantModuleModel(tenant_id=core_t.id, module_code="CORE", is_enabled=True))

        for et in ("customers", "leads", "broker_cases", "broker_commissions"):
            db.add(TenantSequenceModel(tenant_id=brk_t.id, entity_type=et, last_value=0))
        for mc in ("CORE", "BROKER"):
            db.add(TenantModuleModel(tenant_id=brk_t.id, module_code=mc, is_enabled=True))

        db.add_all(
            [
                UserModel(
                    tenant_id=default_t.id,
                    email="owner@default-b3.com",
                    full_name="Default Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
                UserModel(
                    tenant_id=core_t.id,
                    email="owner@core-b3.com",
                    full_name="Core Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
                UserModel(
                    tenant_id=brk_t.id,
                    email="owner@revoke-b3.com",
                    full_name="Broker Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
            ]
        )
        db.commit()
        ids = {
            "default_tid": default_t.id,
            "core_tid": core_t.id,
            "brk_tid": brk_t.id,
        }
    finally:
        db.close()

    c_def, h_def, _ = _login_client("owner@default-b3.com", pw)
    c_core, h_core, _ = _login_client("owner@core-b3.com", pw)
    c_brk, h_brk, _ = _login_client("owner@revoke-b3.com", pw)

    try:
        cust_def = c_def.post(
            "/api/customers",
            headers=h_def,
            json={"entity_type": "INDIVIDUAL", "first_name": "Def", "last_name": "Cust", "phone_primary": "5510001000"},
        ).json()
        cust_core = c_core.post(
            "/api/customers",
            headers=h_core,
            json={"entity_type": "INDIVIDUAL", "first_name": "Core", "last_name": "Cust", "phone_primary": "5520002000"},
        ).json()
        cust_brk = c_brk.post(
            "/api/customers",
            headers=h_brk,
            json={"entity_type": "INDIVIDUAL", "first_name": "Brk", "last_name": "Cust", "phone_primary": "5530003000"},
        ).json()

        # Both CORE+LENDING (default-org) and CORE-only receive 403 + MODULE_ACCESS_DENIED
        for client, tid, cid in (
            (c_def, ids["default_tid"], cust_def["id"]),
            (c_core, ids["core_tid"], cust_core["id"]),
        ):
            paths = ["/api/broker/dashboard-summary", f"/api/broker/customers/{cid}/summary"]
            for path in paths:
                resp = client.get(path)
                assert resp.status_code == 403, f"Expected 403 on {path}, got {resp.status_code}"
                assert "BROKER" in resp.json()["detail"]

            db = b3_env["SessionLocal"]()
            try:
                logs = (
                    db.query(AuditLogModel)
                    .filter(
                        AuditLogModel.event_type == "MODULE_ACCESS_DENIED",
                        AuditLogModel.tenant_id == tid,
                    )
                    .order_by(AuditLogModel.id.asc())
                    .all()
                )
                assert len(logs) == 2
                for log in logs:
                    assert log.scope == "TENANT"
                    assert log.outcome == "DENIED"
                    det = json.loads(log.details_json)
                    assert det["module_code"] == "BROKER"
                assert [json.loads(l.details_json)["path"] for l in logs] == paths
            finally:
                db.close()

        # Hot session revocation on BROKER tenant
        assert c_brk.get("/api/broker/dashboard-summary").status_code == 200
        assert c_brk.get(f"/api/broker/customers/{cust_brk['id']}/summary").status_code == 200

        db = b3_env["SessionLocal"]()
        try:
            tm = (
                db.query(TenantModuleModel)
                .filter(TenantModuleModel.tenant_id == ids["brk_tid"], TenantModuleModel.module_code == "BROKER")
                .one()
            )
            tm.is_enabled = False
            db.commit()
        finally:
            db.close()

        r_rev_dash = c_brk.get("/api/broker/dashboard-summary")
        r_rev_cust = c_brk.get(f"/api/broker/customers/{cust_brk['id']}/summary")
        assert r_rev_dash.status_code == 403
        assert r_rev_cust.status_code == 403

        db = b3_env["SessionLocal"]()
        try:
            rev_logs = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "MODULE_ACCESS_DENIED",
                    AuditLogModel.tenant_id == ids["brk_tid"],
                )
                .all()
            )
            assert len(rev_logs) == 2
            assert all(l.scope == "TENANT" and l.outcome == "DENIED" for l in rev_logs)
            # Re-enable BROKER and verify same session recovers cleanly
            tm = (
                db.query(TenantModuleModel)
                .filter(TenantModuleModel.tenant_id == ids["brk_tid"], TenantModuleModel.module_code == "BROKER")
                .one()
            )
            tm.is_enabled = True
            db.commit()
        finally:
            db.close()

        assert c_brk.get("/api/broker/dashboard-summary").status_code == 200
        assert c_brk.get(f"/api/broker/customers/{cust_brk['id']}/summary").status_code == 200
    finally:
        c_def.close()
        c_core.close()
        c_brk.close()


# ---------------------------------------------------------------------------
# 4. COEXISTENCE OF CORE-ONLY, CORE + BROKER, AND CORE + LENDING + BROKER
# ---------------------------------------------------------------------------
def test_04_coexistence_of_three_commercial_profiles_core_broker_and_lending_plus_broker(b3_env):
    db = b3_env["SessionLocal"]()
    pw = "Password123!"
    pw_hash = main.pwd_context.hash(pw)
    try:
        t_core = TenantModel(name="Pure Core Co", slug="pure-core-b3", is_active=True)
        t_broker = TenantModel(name="Pure Broker Co", slug="pure-broker-b3", is_active=True)
        t_hybrid = TenantModel(
            name="Hybrid Financial Co",
            slug="hybrid-fin-b3",
            legal_name="Hybrid Financial SAPI de CV",
            is_active=True,
        )
        db.add_all([t_core, t_broker, t_hybrid])
        db.flush()

        # CORE-only
        for et in ("customers", "leads"):
            db.add(TenantSequenceModel(tenant_id=t_core.id, entity_type=et, last_value=0))
        db.add(TenantModuleModel(tenant_id=t_core.id, module_code="CORE", is_enabled=True))

        # CORE + BROKER
        for et in ("customers", "leads", "broker_cases", "broker_commissions"):
            db.add(TenantSequenceModel(tenant_id=t_broker.id, entity_type=et, last_value=0))
        for mc in ("CORE", "BROKER"):
            db.add(TenantModuleModel(tenant_id=t_broker.id, module_code=mc, is_enabled=True))

        # CORE + LENDING + BROKER
        for et in ("customers", "leads", "loans", "loan_payments", "broker_cases", "broker_commissions"):
            db.add(TenantSequenceModel(tenant_id=t_hybrid.id, entity_type=et, last_value=0))
        db.add(TenantModuleModel(tenant_id=t_hybrid.id, module_code="CORE", is_enabled=True))
        db.add(
            TenantModuleModel(
                tenant_id=t_hybrid.id,
                module_code="LENDING",
                is_enabled=True,
                config_json={"lending_entity_name": "Hybrid Capital SAPI"},
            )
        )
        db.add(TenantModuleModel(tenant_id=t_hybrid.id, module_code="BROKER", is_enabled=True))

        db.add_all(
            [
                UserModel(
                    tenant_id=t_core.id,
                    email="owner@pure-core.com",
                    full_name="Core Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
                UserModel(
                    tenant_id=t_broker.id,
                    email="owner@pure-broker.com",
                    full_name="Broker Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
                UserModel(
                    tenant_id=t_hybrid.id,
                    email="owner@hybrid-fin.com",
                    full_name="Hybrid Owner",
                    password_hash=pw_hash,
                    role="OWNER_ADMIN",
                    is_active=True,
                ),
            ]
        )
        db.commit()
        hybrid_tid = t_hybrid.id
    finally:
        db.close()

    c_core, h_core, login_core = _login_client("owner@pure-core.com", pw)
    c_brk, h_brk, login_brk = _login_client("owner@pure-broker.com", pw)
    c_hyb, h_hyb, login_hyb = _login_client("owner@hybrid-fin.com", pw)

    try:
        # 1. Verify /api/auth/login & /api/auth/me enabled_modules contracts
        assert login_core["enabled_modules"] == ["CORE"]
        assert "lending_entity_name" not in login_core

        assert login_brk["enabled_modules"] == ["BROKER", "CORE"]
        assert "lending_entity_name" not in login_brk

        assert login_hyb["enabled_modules"] == ["BROKER", "CORE", "LENDING"]
        assert login_hyb["lending_entity_name"] == "Hybrid Capital SAPI"

        # 2. Create 1 customer in each tenant
        cust_core = c_core.post(
            "/api/customers",
            headers=h_core,
            json={"entity_type": "INDIVIDUAL", "first_name": "Ana", "last_name": "Core", "phone_primary": "5501010101"},
        ).json()
        cust_brk = c_brk.post(
            "/api/customers",
            headers=h_brk,
            json={"entity_type": "INDIVIDUAL", "first_name": "Luis", "last_name": "Broker", "phone_primary": "5502020202"},
        ).json()
        cust_hyb = c_hyb.post(
            "/api/customers",
            headers=h_hyb,
            json={"entity_type": "INDIVIDUAL", "first_name": "Sofia", "last_name": "Hybrid", "phone_primary": "5503030303"},
        ).json()

        # 3. Access matrix across profiles
        # CORE-only: blocked from both Lending and Broker summaries
        assert c_core.get("/api/loans/dashboard-summary").status_code == 403
        assert c_core.get(f"/api/loans/customers/{cust_core['id']}/summary").status_code == 403
        assert c_core.get("/api/broker/dashboard-summary").status_code == 403
        assert c_core.get(f"/api/broker/customers/{cust_core['id']}/summary").status_code == 403

        # CORE + BROKER: blocked from Lending, allowed on Broker
        assert c_brk.get("/api/loans/dashboard-summary").status_code == 403
        assert c_brk.get(f"/api/loans/customers/{cust_brk['id']}/summary").status_code == 403
        assert c_brk.get("/api/broker/dashboard-summary").status_code == 200
        assert c_brk.get(f"/api/broker/customers/{cust_brk['id']}/summary").status_code == 200

        # 4. On CORE + LENDING + BROKER: run BOTH a full Loan + Payment AND a full Broker Case + Commission on the SAME customer
        _, _, _, today_local = main.get_local_day_bounds_utc()
        today_iso = today_local.isoformat()

        r_loan = c_hyb.post(
            "/api/loans",
            headers=h_hyb,
            json={
                "idempotency_key": "hybrid-loan-0001",
                "customer_id": cust_hyb["id"],
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
        loan = r_loan.json()["loan"]
        assert loan["loan_number"] == "LN-00001"
        assert c_hyb.post(f"/api/loans/{loan['id']}/approve", headers=h_hyb, json={}).status_code == 200
        assert (
            c_hyb.post(
                f"/api/loans/{loan['id']}/activate",
                headers=h_hyb,
                json={
                    "confirm_disbursement": True,
                    "disbursement_method": "SPEI_TRANSFER",
                    "disbursement_reference": "HYB-DISB-001",
                },
            ).status_code
            == 200
        )
        r_pay = c_hyb.post(
            f"/api/loans/{loan['id']}/payments",
            headers=h_hyb,
            json={
                "idempotency_key": "hybrid-pay-0001",
                "amount_pesos": 5000.0,
                "payment_method": "SPEI_TRANSFER",
                "payment_reference": "HYB-PMT-001",
            },
        )
        assert r_pay.status_code == 200, r_pay.text
        assert r_pay.json()["payment"]["payment_number"] == "PAY-000001"

        # Now Broker workflow on the EXACT same customer
        l_hyb = c_hyb.post(
            "/api/broker/lenders",
            headers=h_hyb,
            json={"code": "BANORTE", "name": "Banorte MX", "default_commission_bps": 250},
        ).json()
        funded_hyb = _create_funded_case(
            c_hyb,
            h_hyb,
            idem_key="hybrid-brk-0001",
            customer_id=cust_hyb["id"],
            lender_id=l_hyb["id"],
            requested_pesos=300000.0,
            funded_pesos=300000.0,
            commission_bps=250,
            external_ref="BNT-HYB-01",
        )
        assert funded_hyb["case_number"] == "BRK-00001"
        assert funded_hyb["commission"]["commission_number"] == "COM-00001"
        assert funded_hyb["commission"]["expected_commission_cents"] == 750_000  # 300,000 * 2.5%

        # 5. Verify tenant_sequences in Hybrid tenant have zero collisions
        db = b3_env["SessionLocal"]()
        try:
            seqs = {
                s.entity_type: int(s.last_value)
                for s in db.query(TenantSequenceModel).filter(TenantSequenceModel.tenant_id == hybrid_tid).all()
            }
            assert seqs["customers"] == 1
            assert seqs["loans"] == 1
            assert seqs["loan_payments"] == 1
            assert seqs["broker_cases"] == 1
            assert seqs["broker_commissions"] == 1
        finally:
            db.close()

        # 6. Verify /api/dashboard/summary and /api/customers/{id} remain 100% Core for ALL 3 profiles
        lending_only_keys = {
            "active_loan_portfolio_cents",
            "due_today_cents",
            "overdue_amount_cents",
            "total_outstanding_principal_cents",
            "collected_this_week_cents",
            "credit_summary",
            "loans",
        }
        broker_only_keys = {
            "active_pipeline_cents",
            "funded_volume_cents",
            "cases_by_status",
            "commissions",
            "recent_cases",
            "broker_summary",
            "cases",
        }
        for client, cid in (
            (c_core, cust_core["id"]),
            (c_brk, cust_brk["id"]),
            (c_hyb, cust_hyb["id"]),
        ):
            r_cdash = client.get("/api/dashboard/summary")
            assert r_cdash.status_code == 200, r_cdash.text
            cdash = r_cdash.json()
            assert not (set(cdash.keys()) & lending_only_keys), f"Core dashboard leaked Lending keys: {cdash.keys()}"
            assert not (set(cdash.keys()) & broker_only_keys), f"Core dashboard leaked Broker keys: {cdash.keys()}"

            r_cdet = client.get(f"/api/customers/{cid}")
            assert r_cdet.status_code == 200, r_cdet.text
            cdet = r_cdet.json()
            assert not (set(cdet.keys()) & lending_only_keys), f"Core customer detail leaked Lending keys: {cdet.keys()}"
            assert not (set(cdet.keys()) & broker_only_keys), f"Core customer detail leaked Broker keys: {cdet.keys()}"

        # 7. Verify Hybrid vertical summaries are completely clean and non-overlapping
        r_ldash = c_hyb.get("/api/loans/dashboard-summary")
        assert r_ldash.status_code == 200
        ldash = r_ldash.json()
        assert ldash["counts"]["active_loans"] == 1
        assert ldash["collected_this_week_cents"] == 500_000
        assert not (set(ldash.keys()) & broker_only_keys)

        r_bdash = c_hyb.get("/api/broker/dashboard-summary")
        assert r_bdash.status_code == 200
        bdash = r_bdash.json()
        assert bdash["counts"]["funded_cases"] == 1
        assert bdash["funded_volume_cents"] == 30_000_000
        assert bdash["commissions"]["pending_cents"] == 750_000
        assert not (set(bdash.keys()) & lending_only_keys)

        r_lcust = c_hyb.get(f"/api/loans/customers/{cust_hyb['id']}/summary")
        assert r_lcust.status_code == 200
        lcust = r_lcust.json()
        assert len(lcust["loans"]) == 1
        assert lcust["loans"][0]["loan_number"] == "LN-00001"
        assert not (set(lcust.keys()) & broker_only_keys)

        r_bcust = c_hyb.get(f"/api/broker/customers/{cust_hyb['id']}/summary")
        assert r_bcust.status_code == 200
        bcust = r_bcust.json()
        assert len(bcust["cases"]) == 1
        assert bcust["cases"][0]["case_number"] == "BRK-00001"
        assert len(bcust["commissions"]) == 1
        assert bcust["commissions"][0]["commission_number"] == "COM-00001"
        assert not (set(bcust.keys()) & lending_only_keys)
    finally:
        c_core.close()
        c_brk.close()
        c_hyb.close()
