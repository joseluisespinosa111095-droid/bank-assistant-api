"""Automated Stage 3 Loans engine, idempotency, financial semantics, and RBAC tests.

Adapted to Alembic 013 multitenant architecture with isolated per-test SQLite DBs
(or optional NURA_TEST_POSTGRES_URL) and clean dependency_overrides teardown.
"""
from datetime import timedelta
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ["ENVIRONMENT"] = "development"
os.environ["RENDER"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"

import main
from models import CustomerModel, TenantModel, UserModel
from services.financial_engine import LoanFinancialEngine

PG_TEST_URL = os.getenv("NURA_TEST_POSTGRES_URL", "").strip()
is_postgres = "postgresql" in PG_TEST_URL
if is_postgres and ("primerbase" in PG_TEST_URL or "render.com" in PG_TEST_URL):
    raise RuntimeError("CRITICAL: Test database URL points to production!")


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
def migrated_stage3_template_db(tmp_path_factory) -> Path:
    tmpl_dir = tmp_path_factory.mktemp("stage3_template")
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
def stage3_env(migrated_stage3_template_db: Path, tmp_path: Path):
    if is_postgres:
        eng = create_engine(PG_TEST_URL)
    else:
        db_file = tmp_path / "stage3_case.db"
        shutil.copy2(migrated_stage3_template_db, db_file)
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
        yield {"engine": eng, "SessionLocal": SessionLocal}
    finally:
        main.app.dependency_overrides.clear()
        eng.dispose()


def _create_user_and_login(SessionLocal, *, role: str = "OWNER_ADMIN", email: str = "test.admin@nura.crm"):
    pw = "Password123!"
    db = SessionLocal()
    try:
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        user = UserModel(
            tenant_id=default_t.id,
            email=email,
            full_name="Test User",
            password_hash=main.pwd_context.hash(pw),
            role=role,
            is_superadmin=False,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        tenant_id = default_t.id
        user_id = user.id
    finally:
        db.close()

    client = TestClient(main.app, base_url="http://localhost")
    response = client.post("/api/auth/login", json={"email": email, "password": pw})
    assert response.status_code == 200, f"Login failed for {email}: {response.status_code} {response.text}"
    csrf_cookie = client.cookies.get("csrf_token")
    assert csrf_cookie
    return client, {"Origin": "http://localhost", "X-CSRF-Token": csrf_cookie}, tenant_id, user_id


def _seed_customer(SessionLocal, tenant_id: int, user_id: int, number: str, name: str, phone: str) -> int:
    db = SessionLocal()
    try:
        cust = CustomerModel(
            tenant_id=tenant_id,
            customer_number=number,
            full_legal_name=name,
            phone_primary=phone,
            phone_primary_normalized=phone,
            owner_user_id=user_id,
            created_by_user_id=user_id,
        )
        db.add(cust)
        db.commit()
        db.refresh(cust)
        return cust.id
    finally:
        db.close()


# --- TEST 1: FINANCIAL ENGINE REFERENCE CASE ---
def test_01_engine_reference_case_50k_16w_4m():
    res = LoanFinancialEngine.calculate_flat_schedule(
        principal_pesos=50000.0,
        monthly_rate_percent=10.0,
        assumed_interest_months=4,
        term_weeks=16,
        first_payment_date_iso="2026-11-02",
    )

    assert res["principal_cents"] == 5000000
    assert res["monthly_interest_rate_bps"] == 1000
    assert res["monthly_interest_amount_cents"] == 500000
    assert res["months_count"] == 4
    assert res["installments_count"] == 16
    assert res["total_interest_cents"] == 2000000
    assert res["total_repayment_cents"] == 7000000
    assert res["regular_installment_cents"] == 437500
    assert res["final_installment_cents"] == 437500
    assert len(res["schedule"]) == 16
    assert res["schedule"][-1]["ending_principal_cents"] == 0


# --- TEST 2: ROUNDING & SCHEDULE SUM INVARIANTS ---
def test_02_engine_rounding_and_schedule_sum_invariants():
    res = LoanFinancialEngine.calculate_flat_schedule(
        principal_pesos=10000.0,
        monthly_rate_percent=7.75,
        assumed_interest_months=3,
        term_weeks=17,
        first_payment_date_iso="2026-11-02",
    )

    sum_principal = sum(inst["principal_due_cents"] for inst in res["schedule"])
    sum_interest = sum(inst["interest_due_cents"] for inst in res["schedule"])
    sum_scheduled = sum(inst["scheduled_payment_cents"] for inst in res["schedule"])

    assert sum_principal == res["principal_cents"]
    assert sum_interest == res["total_interest_cents"]
    assert sum_scheduled == res["total_repayment_cents"]
    assert res["schedule"][-1]["ending_principal_cents"] == 0


# --- TEST 3: IDEMPOTENCY REPLAY & CONFLICT ---
def test_03_idempotency_replay_and_conflict(stage3_env):
    SessionLocal = stage3_env["SessionLocal"]
    client, headers, tenant_id, user_id = _create_user_and_login(SessionLocal, role="CREDIT_ADVISOR")
    _, _, _, today_local = main.get_local_day_bounds_utc()
    start_iso = today_local.isoformat()
    first_pay_iso = (today_local + timedelta(days=7)).isoformat()

    try:
        cust_id = _seed_customer(SessionLocal, tenant_id, user_id, "CUST-00001", "Test Borrower", "5512345678")
        payload = {
            "idempotency_key": "test-idem-key-12345",
            "customer_id": cust_id,
            "product_name": "Personal Loan",
            "principal_pesos": 50000.0,
            "monthly_interest_rate_percent": 10.0,
            "assumed_interest_months": 4,
            "term_installments_count": 16,
            "start_date": start_iso,
            "first_payment_date": first_pay_iso,
        }

        res1 = client.post("/api/loans", json=payload, headers=headers)
        assert res1.status_code == 200, res1.text
        data1 = res1.json()
        assert data1["idempotent_replay"] is False
        loan_num = data1["loan"]["loan_number"]

        res2 = client.post("/api/loans", json=payload, headers=headers)
        assert res2.status_code == 200, res2.text
        data2 = res2.json()
        assert data2["idempotent_replay"] is True
        assert data2["loan"]["loan_number"] == loan_num

        payload_diff = dict(payload)
        payload_diff["principal_pesos"] = 60000.0
        res3 = client.post("/api/loans", json=payload_diff, headers=headers)
        assert res3.status_code == 409
    finally:
        client.close()


# --- TEST 4: FINANCIAL SEMANTICS & ACCRUAL SEPARATION ---
def test_04_financial_semantics_and_accrual_separation(stage3_env):
    SessionLocal = stage3_env["SessionLocal"]
    client, headers, tenant_id, user_id = _create_user_and_login(SessionLocal, role="OWNER_ADMIN")
    _, _, _, today_local = main.get_local_day_bounds_utc()
    start_iso = today_local.isoformat()
    first_pay_iso = (today_local + timedelta(days=7)).isoformat()

    try:
        cust_id = _seed_customer(SessionLocal, tenant_id, user_id, "CUST-00002", "Borrower Two", "5587654321")
        payload = {
            "idempotency_key": "test-idem-semantics-999",
            "customer_id": cust_id,
            "product_name": "Personal Loan",
            "principal_pesos": 50000.0,
            "monthly_interest_rate_percent": 10.0,
            "assumed_interest_months": 4,
            "term_installments_count": 16,
            "start_date": start_iso,
            "first_payment_date": first_pay_iso,
        }

        res = client.post("/api/loans", json=payload, headers=headers)
        assert res.status_code == 200, res.text
        loan_id = res.json()["loan"]["id"]

        loan_detail = client.get(f"/api/loans/{loan_id}").json()
        assert loan_detail["unpaid_principal_cents"] == 0
        assert loan_detail["remaining_contract_total_cents"] == 0

        res_app = client.post(f"/api/loans/{loan_id}/approve", json={"approval_notes": "OK"}, headers=headers)
        assert res_app.status_code == 200

        res_act = client.post(
            f"/api/loans/{loan_id}/activate",
            json={
                "confirm_disbursement": True,
                "disbursement_method": "SPEI_TRANSFER",
                "disbursement_reference": "SPEI-REF-001",
            },
            headers=headers,
        )
        assert res_act.status_code == 200

        active_loan = res_act.json()
        assert active_loan["status"] == "ACTIVE"
        assert active_loan["unpaid_principal_cents"] == 5000000
        assert active_loan["remaining_contractual_interest_cents"] == 2000000
        assert active_loan["remaining_contract_total_cents"] == 7000000
        assert active_loan["current_payable_amount_cents"] == 0
    finally:
        client.close()


# --- TEST 5: RBAC & LIFECYCLE TRANSITIONS ---
def test_05_rbac_and_lifecycle_transitions(stage3_env):
    SessionLocal = stage3_env["SessionLocal"]
    sales_client, sales_headers, tenant_id, sales_id = _create_user_and_login(
        SessionLocal, role="SALES_AGENT", email="sales@nura.crm"
    )
    admin_client, admin_headers, _, _ = _create_user_and_login(
        SessionLocal, role="OWNER_ADMIN", email="admin@nura.crm"
    )
    _, _, _, today_local = main.get_local_day_bounds_utc()
    start_iso = today_local.isoformat()
    first_pay_iso = (today_local + timedelta(days=7)).isoformat()

    try:
        cust_id = _seed_customer(SessionLocal, tenant_id, sales_id, "CUST-00003", "Borrower Three", "5511223344")
        payload = {
            "idempotency_key": "test-idem-rbac-777",
            "customer_id": cust_id,
            "product_name": "Business Loan",
            "principal_pesos": 100000.0,
            "monthly_interest_rate_percent": 8.0,
            "assumed_interest_months": 3,
            "term_installments_count": 12,
            "start_date": start_iso,
            "first_payment_date": first_pay_iso,
        }

        res = sales_client.post("/api/loans", json=payload, headers=sales_headers)
        assert res.status_code == 200, res.text
        loan_id = res.json()["loan"]["id"]

        res_app_fail = sales_client.post(f"/api/loans/{loan_id}/approve", json={}, headers=sales_headers)
        assert res_app_fail.status_code == 403

        res_app_ok = admin_client.post(f"/api/loans/{loan_id}/approve", json={}, headers=admin_headers)
        assert res_app_ok.status_code == 200
        assert res_app_ok.json()["status"] == "APPROVED"

        res_act = admin_client.post(
            f"/api/loans/{loan_id}/activate",
            json={
                "confirm_disbursement": True,
                "disbursement_method": "BANK_DEPOSIT",
                "disbursement_reference": "DEP-999",
            },
            headers=admin_headers,
        )
        assert res_act.status_code == 200
        assert res_act.json()["status"] == "ACTIVE"

        res_dup = admin_client.post(
            f"/api/loans/{loan_id}/activate",
            json={
                "confirm_disbursement": True,
                "disbursement_method": "BANK_DEPOSIT",
                "disbursement_reference": "DEP-999",
            },
            headers=admin_headers,
        )
        assert res_dup.status_code == 409
    finally:
        sales_client.close()
        admin_client.close()


# --- TEST 6: POSTGRESQL SPECIFIC TESTS (TRIGGERS & CONCURRENCY) ---
@pytest.mark.skipif(not is_postgres, reason="Requires PostgreSQL database for triggers and advisory locks.")
def test_06_postgres_triggers_and_concurrency(stage3_env):
    SessionLocal = stage3_env["SessionLocal"]
    client, headers, tenant_id, user_id = _create_user_and_login(
        SessionLocal, role="OWNER_ADMIN", email="pg.admin@nura.crm"
    )
    _, _, _, today_local = main.get_local_day_bounds_utc()
    start_iso = today_local.isoformat()
    first_pay_iso = (today_local + timedelta(days=7)).isoformat()

    try:
        cust_id = _seed_customer(SessionLocal, tenant_id, user_id, "CUST-PG-01", "PG Borrower", "5599887766")
        payload = {
            "idempotency_key": "test-pg-immutability-1",
            "customer_id": cust_id,
            "product_name": "Personal Loan",
            "principal_pesos": 25000.0,
            "monthly_interest_rate_percent": 10.0,
            "assumed_interest_months": 2,
            "term_installments_count": 8,
            "start_date": start_iso,
            "first_payment_date": first_pay_iso,
        }

        res = client.post("/api/loans", json=payload, headers=headers)
        assert res.status_code == 200
        loan_id = res.json()["loan"]["id"]

        with pytest.raises(Exception):
            db_raw = SessionLocal()
            try:
                db_raw.execute(text("UPDATE loans SET principal_cents = 999999 WHERE id = :lid"), {"lid": loan_id})
                db_raw.commit()
            finally:
                db_raw.close()
    finally:
        client.close()
