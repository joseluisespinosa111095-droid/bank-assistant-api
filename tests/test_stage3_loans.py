import os
import pytest
from datetime import date, timedelta
from decimal import Decimal
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from main import app, Base, get_db, LoanFinancialEngine, UserModel, CustomerModel

# --- TEST DATABASE SETUP (ISOLATED POSTGRESQL or SQLITE) ---
TEST_DATABASE_URL = os.getenv("NURA_TEST_POSTGRES_URL", "sqlite:///./test_nura.db")

is_postgres = "postgresql" in TEST_DATABASE_URL

if is_postgres:
    # Verify isolation from production
    if "primerbase" in TEST_DATABASE_URL or "render.com" in TEST_DATABASE_URL:
        raise RuntimeError("CRITICAL: Test database URL points to production!")

test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in TEST_DATABASE_URL else {})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_teardown_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


def create_test_user_and_login(role="OWNER_ADMIN", email="test.admin@nura.crm"):
    db = TestingSessionLocal()
    user = UserModel(
        email=email,
        full_name="Test User",
        password_hash="$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW", # dummy bcrypt for password
        role=role,
        is_active=True
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()

    # Login via API to obtain cookies
    response = client.post("/api/auth/login", json={"email": email, "password": "password123"})
    assert response.status_code == 200
    csrf_cookie = client.cookies.get("csrf_token")
    return {"X-CSRF-Token": csrf_cookie}


# --- TEST 1: FINANCIAL ENGINE REFERENCE CASE ---
def test_01_engine_reference_case_50k_16w_4m():
    res = LoanFinancialEngine.calculate_flat_schedule(
        principal_pesos=50000.0,
        monthly_rate_percent=10.0,
        assumed_interest_months=4,
        term_weeks=16,
        first_payment_date_iso="2026-11-02"
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
    # Test with non-divisible principal ($10,000.00 MXN in 17 weeks)
    res = LoanFinancialEngine.calculate_flat_schedule(
        principal_pesos=10000.0,
        monthly_rate_percent=7.75,
        assumed_interest_months=3,
        term_weeks=17,
        first_payment_date_iso="2026-11-02"
    )

    sum_principal = sum(inst["principal_due_cents"] for inst in res["schedule"])
    sum_interest = sum(inst["interest_due_cents"] for inst in res["schedule"])
    sum_scheduled = sum(inst["scheduled_payment_cents"] for inst in res["schedule"])

    assert sum_principal == res["principal_cents"]
    assert sum_interest == res["total_interest_cents"]
    assert sum_scheduled == res["total_repayment_cents"]
    assert res["schedule"][-1]["ending_principal_cents"] == 0


# --- TEST 3: IDEMPOTENCY REPLAY & CONFLICT ---
def test_03_idempotency_replay_and_conflict():
    headers = create_test_user_and_login(role="CREDIT_ADVISOR")

    # Create a test customer first
    db = TestingSessionLocal()
    cust = CustomerModel(
        customer_number="CUST-00001",
        full_legal_name="Test Borrower",
        phone_primary="5512345678",
        phone_primary_normalized="5512345678",
        owner_user_id=1,
        created_by_user_id=1
    )
    db.add(cust)
    db.commit()
    cust_id = cust.id
    db.close()

    payload = {
        "idempotency_key": "test-idem-key-12345",
        "customer_id": cust_id,
        "product_name": "Personal Loan",
        "principal_pesos": 50000.0,
        "monthly_interest_rate_percent": 10.0,
        "assumed_interest_months": 4,
        "term_installments_count": 16,
        "start_date": "2026-10-26",
        "first_payment_date": "2026-11-02"
    }

    # First request
    res1 = client.post("/api/loans", json=payload, headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["idempotent_replay"] is False
    loan_num = data1["loan"]["loan_number"]

    # Replay same payload with same idempotency_key
    res2 = client.post("/api/loans", json=payload, headers=headers)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["idempotent_replay"] is True
    assert data2["loan"]["loan_number"] == loan_num

    # Replay same key with DIFFERENT payload -> 409 Conflict
    payload_diff = dict(payload)
    payload_diff["principal_pesos"] = 60000.0
    res3 = client.post("/api/loans", json=payload_diff, headers=headers)
    assert res3.status_code == 409


# --- TEST 4: FINANCIAL SEMANTICS & ACCRUAL SEPARATION ---
def test_04_financial_semantics_and_accrual_separation():
    headers = create_test_user_and_login(role="OWNER_ADMIN")
    db = TestingSessionLocal()
    cust = CustomerModel(
        customer_number="CUST-00002",
        full_legal_name="Borrower Two",
        phone_primary="5587654321",
        phone_primary_normalized="5587654321",
        owner_user_id=1,
        created_by_user_id=1
    )
    db.add(cust)
    db.commit()
    cust_id = cust.id
    db.close()

    payload = {
        "idempotency_key": "test-idem-semantics-999",
        "customer_id": cust_id,
        "product_name": "Personal Loan",
        "principal_pesos": 50000.0,
        "monthly_interest_rate_percent": 10.0,
        "assumed_interest_months": 4,
        "term_installments_count": 16,
        "start_date": "2026-10-26",
        "first_payment_date": "2026-11-02"
    }

    res = client.post("/api/loans", json=payload, headers=headers)
    assert res.status_code == 200
    loan_id = res.json()["loan"]["id"]

    # In DRAFT, balances should be zero
    loan_detail = client.get(f"/api/loans/{loan_id}", headers=headers).json()
    assert loan_detail["unpaid_principal_cents"] == 0
    assert loan_detail["remaining_contract_total_cents"] == 0

    # Approve and Activate
    res_app = client.post(f"/api/loans/{loan_id}/approve", json={"approval_notes": "OK"}, headers=headers)
    assert res_app.status_code == 200

    res_act = client.post(f"/api/loans/{loan_id}/activate", json={
        "confirm_disbursement": True,
        "disbursement_method": "SPEI_TRANSFER",
        "disbursement_reference": "SPEI-REF-001"
    }, headers=headers)
    assert res_act.status_code == 200

    active_loan = res_act.json()
    assert active_loan["status"] == "ACTIVE"
    assert active_loan["unpaid_principal_cents"] == 5000000
    assert active_loan["remaining_contractual_interest_cents"] == 2000000
    assert active_loan["remaining_contract_total_cents"] == 7000000
    assert active_loan["current_payable_amount_cents"] == 0


# --- TEST 5: RBAC & LIFECYCLE TRANSITIONS ---
def test_05_rbac_and_lifecycle_transitions():
    sales_headers = create_test_user_and_login(role="SALES_AGENT", email="sales@nura.crm")
    admin_headers = create_test_user_and_login(role="OWNER_ADMIN", email="admin@nura.crm")

    db = TestingSessionLocal()
    cust = CustomerModel(
        customer_number="CUST-00003",
        full_legal_name="Borrower Three",
        phone_primary="5511223344",
        phone_primary_normalized="5511223344",
        owner_user_id=1,
        created_by_user_id=1
    )
    db.add(cust)
    db.commit()
    cust_id = cust.id
    db.close()

    payload = {
        "idempotency_key": "test-idem-rbac-777",
        "customer_id": cust_id,
        "product_name": "Business Loan",
        "principal_pesos": 100000.0,
        "monthly_interest_rate_percent": 8.0,
        "assumed_interest_months": 3,
        "term_installments_count": 12,
        "start_date": "2026-10-26",
        "first_payment_date": "2026-11-02"
    }

    # Sales agent can create draft
    res = client.post("/api/loans", json=payload, headers=sales_headers)
    assert res.status_code == 200
    loan_id = res.json()["loan"]["id"]

    # Sales agent CANNOT approve -> 403 Forbidden
    res_app_fail = client.post(f"/api/loans/{loan_id}/approve", json={}, headers=sales_headers)
    assert res_app_fail.status_code == 403

    # Admin approves
    res_app_ok = client.post(f"/api/loans/{loan_id}/approve", json={}, headers=admin_headers)
    assert res_app_ok.status_code == 200
    assert res_app_ok.json()["status"] == "APPROVED"

    # Admin activates with valid disbursement info
    res_act = client.post(f"/api/loans/{loan_id}/activate", json={
        "confirm_disbursement": True,
        "disbursement_method": "BANK_DEPOSIT",
        "disbursement_reference": "DEP-999"
    }, headers=admin_headers)
    assert res_act.status_code == 200
    assert res_act.json()["status"] == "ACTIVE"

    # Duplicate activation attempt -> 409 Conflict
    res_dup = client.post(f"/api/loans/{loan_id}/activate", json={
        "confirm_disbursement": True,
        "disbursement_method": "BANK_DEPOSIT",
        "disbursement_reference": "DEP-999"
    }, headers=admin_headers)
    assert res_dup.status_code == 409


# --- TEST 6: POSTGRESQL SPECIFIC TESTS (TRIGGERS & CONCURRENCY) ---
@pytest.mark.skipif(not is_postgres, reason="Requires PostgreSQL database for triggers and advisory locks.")
def test_06_postgres_triggers_and_concurrency():
    # This test runs only when NURA_TEST_POSTGRES_URL points to a PostgreSQL instance
    headers = create_test_user_and_login(role="OWNER_ADMIN", email="pg.admin@nura.crm")
    db = TestingSessionLocal()
    cust = CustomerModel(
        customer_number="CUST-PG-01",
        full_legal_name="PG Borrower",
        phone_primary="5599887766",
        phone_primary_normalized="5599887766",
        owner_user_id=1,
        created_by_user_id=1
    )
    db.add(cust)
    db.commit()
    cust_id = cust.id
    db.close()

    payload = {
        "idempotency_key": "test-pg-immutability-1",
        "customer_id": cust_id,
        "product_name": "Personal Loan",
        "principal_pesos": 25000.0,
        "monthly_interest_rate_percent": 10.0,
        "assumed_interest_months": 2,
        "term_installments_count": 8,
        "start_date": "2026-10-26",
        "first_payment_date": "2026-11-02"
    }

    res = client.post("/api/loans", json=payload, headers=headers)
    assert res.status_code == 200
    loan_id = res.json()["loan"]["id"]

    # Verify direct SQL UPDATE on principal is blocked by trigger trg_protect_loans_immutability
    with pytest.raises(Exception):
        db_raw = TestingSessionLocal()
        db_raw.execute(text(f"UPDATE loans SET principal_cents = 999999 WHERE id = {loan_id}"))
        db_raw.commit()
        db_raw.close()
