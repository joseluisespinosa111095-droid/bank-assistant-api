"""Automated multi-tenant verification suite for NURA CRM (Entregable 2).

Covers:
1. Full tenant isolation across all CRM resources, documents, dashboard, collections, users & audit logs.
2. SUPERADMIN isolation (no CRM bypass) and OWNER_ADMIN 403 on /api/platform/* & /api/admin/schema-status.
3. Immediate session & login blocking upon tenant suspension.
4. Database-level 2-column and 3-column composite FK enforcement (with PRAGMA foreign_keys=ON).
5. Per-tenant uniqueness (customer_number, phone, idempotency_key, partial posted payment reference).
6. Concurrent sequence generation within the same tenant and across distinct tenants.
7. Removal of POST /api/auth/bootstrap-admin (404) and one-shot scripts/bootstrap_superadmin.py CLI.
8. Automated 011 -> 012 Alembic migration backfill verification on seeded data.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure deterministic dev environment before importing main
os.environ["ENVIRONMENT"] = "development"
os.environ["RENDER"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"

import main
from models import (
    AuditLogModel,
    CustomerDocumentModel,
    CustomerModel,
    LeadModel,
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentAllocationModel,
    LoanPaymentModel,
    TenantModel,
    TenantModuleModel,
    TenantSequenceModel,
    UserModel,
)


def _clean_env(extra: dict | None = None) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "DATABASE_URL",
            "MIGRATION_DATABASE_URL",
            "RENDER",
            "ENVIRONMENT",
            "SUPERADMIN_EMAIL",
            "SUPERADMIN_PASSWORD",
            "SUPERADMIN_FULL_NAME",
        )
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["RENDER"] = "false"
    env["ENVIRONMENT"] = "development"
    env["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"
    if extra:
        env.update(extra)
    return env


def _run_alembic(args: list[str], db_path: Path) -> subprocess.CompletedProcess:
    env = _clean_env(
        {
            "MIGRATION_DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
        }
    )
    return subprocess.run(
        [sys.executable, "-B", "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def _make_sqlite_engine(db_path: Path, wal: bool = False):
    eng = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=10000")
        if wal:
            cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return eng


@pytest.fixture(scope="session")
def migrated_template_db(tmp_path_factory) -> Path:
    tmpl_dir = tmp_path_factory.mktemp("mt_template")
    tmpl_db = tmpl_dir / "template_012.db"
    res = _run_alembic(["upgrade", "head"], tmpl_db)
    assert res.returncode == 0, f"Template migration failed:\n{res.stdout}\n{res.stderr}"
    return tmpl_db


@pytest.fixture()
def mt_env(migrated_template_db: Path, tmp_path: Path):
    """Provides a clean per-test SQLite DB at revision 012 with PRAGMA foreign_keys=ON."""
    db_file = tmp_path / "case_012.db"
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


def _seed_base_actors(SessionLocal) -> dict:
    """Seeds platform SUPERADMIN, Tenant A (OWNER_ADMIN + SALES_AGENT), and Tenant B (OWNER_ADMIN)."""
    db = SessionLocal()
    try:
        platform_t = db.query(TenantModel).filter(TenantModel.slug == "platform").first()
        tenant_a = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        tenant_b = TenantModel(name="Beta Financial", slug="beta-financial", is_active=True)
        db.add(tenant_b)
        db.flush()
        for et in ("customers", "leads", "loans", "loan_payments"):
            db.add(TenantSequenceModel(tenant_id=tenant_b.id, entity_type=et, last_value=0))
        for mc in ("CORE", "LENDING"):
            db.add(TenantModuleModel(tenant_id=tenant_b.id, module_code=mc, is_enabled=True))

        pw_hash = main.pwd_context.hash("Password123!")
        superadmin = UserModel(
            tenant_id=platform_t.id,
            email="super@nura.nura.io",
            full_name="Platform Root",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=True,
            is_active=True,
        )
        owner_a = UserModel(
            tenant_id=tenant_a.id,
            email="owner.a@alpha.nura.io",
            full_name="Owner Alpha",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        sales_a = UserModel(
            tenant_id=tenant_a.id,
            email="sales.a@alpha.nura.io",
            full_name="Sales Alpha",
            password_hash=pw_hash,
            role="SALES_AGENT",
            is_superadmin=False,
            is_active=True,
        )
        owner_b = UserModel(
            tenant_id=tenant_b.id,
            email="owner.b@beta.nura.io",
            full_name="Owner Beta",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        db.add_all([superadmin, owner_a, sales_a, owner_b])
        db.commit()
        return {
            "platform_id": platform_t.id,
            "tenant_a_id": tenant_a.id,
            "tenant_b_id": tenant_b.id,
            "superadmin_id": superadmin.id,
            "owner_a_id": owner_a.id,
            "sales_a_id": sales_a.id,
            "owner_b_id": owner_b.id,
            "password": "Password123!",
        }
    finally:
        db.close()


def _login_client(email: str, password: str) -> tuple[TestClient, dict]:
    client = TestClient(main.app, base_url="http://localhost")
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    csrf = client.cookies.get("csrf_token")
    assert csrf
    return client, {"Origin": "http://localhost", "X-CSRF-Token": csrf}


# ---------------------------------------------------------------------------
# 1. FULL TENANT ISOLATION (TENANT A vs TENANT B)
# ---------------------------------------------------------------------------
def test_01_full_tenant_isolation_between_tenants(mt_env):
    ids = _seed_base_actors(mt_env["SessionLocal"])
    client_a, hdr_a = _login_client("owner.a@alpha.nura.io", ids["password"])
    client_b, hdr_b = _login_client("owner.b@beta.nura.io", ids["password"])
    client_s, hdr_s = _login_client("super@nura.nura.io", ids["password"])

    try:
        # Trigger an ANONYMOUS audit event (failed login) and a PLATFORM audit event
        anon_client = TestClient(main.app, base_url="http://localhost")
        assert anon_client.post("/api/auth/login", json={"email": "nobody@x.nura.io", "password": "wrong"}).status_code == 401
        anon_client.close()

        r_plat_upd = client_s.patch(
            f"/api/platform/tenants/{ids['tenant_b_id']}",
            headers=hdr_s,
            json={"legal_name": "Beta Financial SA"},
        )
        assert r_plat_upd.status_code == 200

        # Tenant A creates customer, KYC PDF document, lead, active loan (due today), payment & conversation
        r_cust = client_a.post(
            "/api/customers",
            headers=hdr_a,
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Alice",
                "last_name": "Alpha",
                "phone_primary": "5510001111",
                "email": "alice@alpha.nura.io",
            },
        )
        assert r_cust.status_code == 200, r_cust.text
        cust_a_id = r_cust.json()["id"]

        pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
        r_doc = client_a.post(
            f"/api/customers/{cust_a_id}/documents",
            headers=hdr_a,
            data={"document_type": "INE_ID"},
            files={"file": ("ine_alice.pdf", pdf_bytes, "application/pdf")},
        )
        assert r_doc.status_code == 200, r_doc.text
        doc_a_id = r_doc.json()["id"]

        r_lead = client_a.post(
            "/api/leads",
            headers=hdr_a,
            json={
                "full_name": "Lead Alpha",
                "phone_primary": "5510002222",
                "product_interest": "Personal Loan",
                "requested_amount_pesos": 10000.0,
            },
        )
        assert r_lead.status_code == 200, r_lead.text
        lead_a_id = r_lead.json()["id"]

        _, _, _, today_local = main.get_local_day_bounds_utc()
        today_iso = today_local.isoformat()
        r_loan = client_a.post(
            "/api/loans",
            headers=hdr_a,
            json={
                "idempotency_key": "iso-loan-a-0001",
                "customer_id": cust_a_id,
                "product_name": "Personal Loan",
                "principal_pesos": 10000.0,
                "monthly_interest_rate_percent": 10.0,
                "assumed_interest_months": 2,
                "term_installments_count": 4,
                "start_date": today_iso,
                "first_payment_date": today_iso,
            },
        )
        assert r_loan.status_code == 200, r_loan.text
        loan_a_id = r_loan.json()["loan"]["id"]

        assert client_a.post(f"/api/loans/{loan_a_id}/approve", headers=hdr_a, json={"approval_notes": "OK"}).status_code == 200
        assert (
            client_a.post(
                f"/api/loans/{loan_a_id}/activate",
                headers=hdr_a,
                json={
                    "confirm_disbursement": True,
                    "disbursement_method": "SPEI_TRANSFER",
                    "disbursement_reference": "DISB-A-001",
                },
            ).status_code
            == 200
        )

        r_pay = client_a.post(
            f"/api/loans/{loan_a_id}/payments",
            headers=hdr_a,
            json={
                "idempotency_key": "iso-pay-a-0001",
                "amount_pesos": 1000.0,
                "payment_method": "SPEI_TRANSFER",
                "payment_reference": "SPEI-A-001",
            },
        )
        assert r_pay.status_code == 200, r_pay.text
        pay_a_id = r_pay.json()["payment"]["id"]

        # Sanity: Tenant A sees its own data in dashboard and collections queue
        assert client_a.get("/api/dashboard/summary").json()["counts"]["customers"] == 1
        assert client_a.get("/api/collections/queue").json()["counts"]["total_actionable"] == 1

        # --- TENANT B CANNOT SEE OR MUTATE ANY TENANT A RESOURCE ---
        # Customers & interactions
        assert client_b.get("/api/customers").json()["total"] == 0
        assert client_b.get(f"/api/customers/{cust_a_id}").status_code == 404
        assert client_b.patch(f"/api/customers/{cust_a_id}", headers=hdr_b, json={"first_name": "Hacked"}).status_code == 404
        assert client_b.get(f"/api/customers/{cust_a_id}/interactions").status_code == 404
        assert (
            client_b.post(
                f"/api/customers/{cust_a_id}/interactions",
                headers=hdr_b,
                json={"interaction_type": "NOTE", "content": "Cross-tenant attempt"},
            ).status_code
            == 404
        )
        # Cross-tenant owner_user_id rejected
        assert (
            client_b.post(
                "/api/customers",
                headers=hdr_b,
                json={
                    "entity_type": "INDIVIDUAL",
                    "first_name": "Bad",
                    "last_name": "Owner",
                    "phone_primary": "5599990000",
                    "owner_user_id": ids["sales_a_id"],
                },
            ).status_code
            == 404
        )

        # Documents & downloads
        assert client_b.get(f"/api/customers/{cust_a_id}/documents").status_code == 404
        assert client_b.get(f"/api/customers/{cust_a_id}/documents/{doc_a_id}/download").status_code == 404
        assert client_b.delete(f"/api/customers/{cust_a_id}/documents/{doc_a_id}", headers=hdr_b).status_code == 404

        # Leads
        assert client_b.get("/api/leads").json()["total"] == 0
        assert client_b.get(f"/api/leads/{lead_a_id}").status_code == 404
        assert client_b.patch(f"/api/leads/{lead_a_id}", headers=hdr_b, json={"full_name": "Hacked"}).status_code == 404
        assert client_b.post(f"/api/leads/{lead_a_id}/convert", headers=hdr_b, json={}).status_code == 404

        # Loans, payments, collections & dashboard
        assert client_b.get("/api/loans").json()["total"] == 0
        assert client_b.get(f"/api/loans/{loan_a_id}").status_code == 404
        assert client_b.get(f"/api/loans/{loan_a_id}/payments").status_code == 404
        assert (
            client_b.post(
                f"/api/loans/{loan_a_id}/payments/{pay_a_id}/reverse",
                headers=hdr_b,
                json={"reversal_reason": "Cross-tenant reversal attempt"},
            ).status_code
            == 404
        )
        assert client_b.get("/api/collections/queue").json()["counts"]["total_actionable"] == 0
        dash_b = client_b.get("/api/dashboard/summary").json()
        assert dash_b["counts"]["customers"] == 0
        assert dash_b["counts"]["open_leads"] == 0
        r_ldash_b = client_b.get("/api/loans/dashboard-summary")
        assert r_ldash_b.status_code == 200
        ldash_b = r_ldash_b.json()
        assert ldash_b["counts"]["active_loans"] == 0
        assert ldash_b["active_loan_portfolio_cents"] == 0
        assert ldash_b["collected_this_week_cents"] == 0

        # Users & team admin
        users_b = client_b.get("/api/admin/users").json()
        assert [u["email"] for u in users_b] == ["owner.b@beta.nura.io"]
        assert client_b.patch(f"/api/admin/users/{ids['sales_a_id']}", headers=hdr_b, json={"is_active": False}).status_code == 404

        # Audit logs: Tenant B sees ONLY its own TENANT events (no Tenant A, no ANONYMOUS, no PLATFORM)
        logs_b = client_b.get("/api/admin/audit-logs?limit=100").json()
        event_types_b = {entry["event_type"] for entry in logs_b}
        assert "CUSTOMER_CREATED" not in event_types_b
        assert "LOAN_PAYMENT_RECORDED" not in event_types_b
        assert "LOGIN_FAILED" not in event_types_b
        assert "PLATFORM_TENANT_UPDATED" not in event_types_b
        assert all(entry["actor_user_id"] == ids["owner_b_id"] for entry in logs_b)
    finally:
        client_a.close()
        client_b.close()
        client_s.close()


# ---------------------------------------------------------------------------
# 2. SUPERADMIN ISOLATION & PLATFORM RBAC
# ---------------------------------------------------------------------------
def test_02_superadmin_isolation_and_platform_rbac(mt_env):
    ids = _seed_base_actors(mt_env["SessionLocal"])
    client_a, hdr_a = _login_client("owner.a@alpha.nura.io", ids["password"])
    client_s, hdr_s = _login_client("super@nura.nura.io", ids["password"])

    try:
        # Tenant A creates commercial data
        r_cust = client_a.post(
            "/api/customers",
            headers=hdr_a,
            json={"entity_type": "INDIVIDUAL", "first_name": "Carlos", "last_name": "Uno", "phone_primary": "5520001111"},
        )
        assert r_cust.status_code == 200
        cust_a_id = r_cust.json()["id"]

        # OWNER_ADMIN receives 403 on all /api/platform/* and /api/admin/schema-status
        assert client_a.get("/api/platform/tenants").status_code == 403
        assert client_a.get(f"/api/platform/tenants/{ids['tenant_b_id']}").status_code == 403
        assert client_a.get(f"/api/platform/tenants/{ids['tenant_b_id']}/users").status_code == 403
        assert client_a.get("/api/platform/audit-logs").status_code == 403
        assert (
            client_a.post(
                "/api/platform/tenants",
                headers=hdr_a,
                json={"name": "Rogue", "slug": "rogue", "owner_email": "r@r.nura.io", "owner_password": "Password123!"},
            ).status_code
            == 403
        )
        assert client_a.patch(f"/api/platform/tenants/{ids['tenant_b_id']}", headers=hdr_a, json={"name": "Hijack"}).status_code == 403
        assert client_a.get("/api/admin/schema-status").status_code == 403

        # SUPERADMIN can manage /api/platform/tenants* and /api/admin/schema-status
        r_schema = client_s.get("/api/admin/schema-status")
        assert r_schema.status_code == 200
        assert r_schema.json()["alembic_version"] == main.EXPECTED_ALEMBIC_REVISION

        r_new = client_s.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "Gamma Lending",
                "slug": "gamma-lending",
                "legal_name": "Gamma Lending SAPI",
                "owner_email": "owner@gamma.nura.io",
                "owner_password": "GammaPassword123!",
                "owner_full_name": "Gamma Owner",
            },
        )
        assert r_new.status_code == 200, r_new.text
        assert r_new.json()["owner_admin"]["is_superadmin"] is False

        # SUPERADMIN cannot suspend platform tenant
        assert client_s.patch(f"/api/platform/tenants/{ids['platform_id']}", headers=hdr_s, json={"is_active": False}).status_code == 400

        # SUPERADMIN is explicitly blocked (HTTP 403) on all commercial CRM routes (Entregable 3)
        assert client_s.get("/api/customers").status_code == 403
        assert client_s.get(f"/api/customers/{cust_a_id}").status_code == 403
        assert client_s.get("/api/leads").status_code == 403
        assert client_s.get("/api/loans").status_code == 403
        assert client_s.get("/api/dashboard/summary").status_code == 403
        assert client_s.get("/api/admin/users").status_code == 403
    finally:
        client_a.close()
        client_s.close()


# ---------------------------------------------------------------------------
# 3. TENANT SUSPENSION BLOCKS ACTIVE SESSIONS IMMEDIATELY
# ---------------------------------------------------------------------------
def test_03_tenant_suspension_blocks_active_sessions_and_login(mt_env):
    ids = _seed_base_actors(mt_env["SessionLocal"])
    client_s, hdr_s = _login_client("super@nura.nura.io", ids["password"])
    client_b, _ = _login_client("owner.b@beta.nura.io", ids["password"])

    try:
        assert client_b.get("/api/customers").status_code == 200

        # Case A: direct DB suspension -> get_current_user blocks immediately with 403
        db = mt_env["SessionLocal"]()
        tb = db.query(TenantModel).filter(TenantModel.id == ids["tenant_b_id"]).first()
        tb.is_active = False
        db.commit()
        db.close()

        assert client_b.get("/api/customers").status_code == 403

        # Re-activate via platform endpoint, re-login, then suspend via platform endpoint
        assert client_s.patch(f"/api/platform/tenants/{ids['tenant_b_id']}", headers=hdr_s, json={"is_active": True}).status_code == 200
        client_b2, _ = _login_client("owner.b@beta.nura.io", ids["password"])
        try:
            assert client_b2.get("/api/customers").status_code == 200
            assert client_s.patch(f"/api/platform/tenants/{ids['tenant_b_id']}", headers=hdr_s, json={"is_active": False}).status_code == 200
            # Session revoked immediately
            assert client_b2.get("/api/customers").status_code in (401, 403)
            # Fresh login blocked with 403
            r_relogin = client_b2.post("/api/auth/login", json={"email": "owner.b@beta.nura.io", "password": ids["password"]})
            assert r_relogin.status_code == 403
        finally:
            client_b2.close()
    finally:
        client_b.close()
        client_s.close()


# ---------------------------------------------------------------------------
# 4. DATABASE-LEVEL COMPOSITE REFERENTIAL INTEGRITY (2-COL & 3-COL FKs)
# ---------------------------------------------------------------------------
def test_04_db_composite_foreign_keys_2col_and_3col(mt_env):
    ids = _seed_base_actors(mt_env["SessionLocal"])
    SessionLocal = mt_env["SessionLocal"]
    now_utc = datetime.now(timezone.utc)
    today = date.today()

    # 4a. 2-column cross-tenant FK rejection: Customer in Tenant B referencing owner from Tenant A
    db = SessionLocal()
    try:
        db.add(
            CustomerModel(
                tenant_id=ids["tenant_b_id"],
                customer_number="CUST-90001",
                full_legal_name="Cross Tenant User Ref",
                phone_primary="5500001111",
                phone_primary_normalized="5500001111",
                owner_user_id=ids["owner_a_id"],  # WRONG TENANT!
                created_by_user_id=ids["owner_b_id"],
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()

    # Seed valid base records in Tenant A (2 customers, 2 loans, 2 installments, 1 payment) & Tenant B
    db = SessionLocal()
    try:
        c1 = CustomerModel(
            tenant_id=ids["tenant_a_id"],
            customer_number="CUST-00001",
            full_legal_name="Borrower One",
            phone_primary="5530001111",
            phone_primary_normalized="5530001111",
            owner_user_id=ids["owner_a_id"],
            created_by_user_id=ids["owner_a_id"],
        )
        c2 = CustomerModel(
            tenant_id=ids["tenant_a_id"],
            customer_number="CUST-00002",
            full_legal_name="Borrower Two",
            phone_primary="5530002222",
            phone_primary_normalized="5530002222",
            owner_user_id=ids["owner_a_id"],
            created_by_user_id=ids["owner_a_id"],
        )
        db.add_all([c1, c2])
        db.flush()

        lead1 = LeadModel(
            tenant_id=ids["tenant_a_id"],
            lead_number="LD-00001",
            full_name="Borrower One",
            phone_primary="5530001111",
            phone_primary_normalized="5530001111",
            stage="CONVERTED",
            owner_user_id=ids["owner_a_id"],
            created_by_user_id=ids["owner_a_id"],
            converted_customer_id=c1.id,
            converted_by_user_id=ids["owner_a_id"],
        )
        db.add(lead1)
        db.flush()

        def _make_loan(num: str, idem: str, cid: int, src_lead: int | None = None) -> LoanModel:
            return LoanModel(
                tenant_id=ids["tenant_a_id"],
                loan_number=num,
                idempotency_key=idem,
                request_payload_hash="h" * 64,
                customer_id=cid,
                source_lead_id=src_lead,
                status="ACTIVE",
                product_name="Personal Loan",
                principal_cents=100000,
                monthly_interest_rate_bps=1000,
                assumed_interest_months=1,
                term_installments_count=1,
                contract_rules_json="{}",
                monthly_interest_amount_cents=10000,
                total_interest_cents=10000,
                total_repayment_cents=110000,
                regular_installment_cents=110000,
                final_installment_cents=110000,
                start_date=today,
                first_payment_date=today,
                maturity_date=today,
                unpaid_principal_cents=100000,
                remaining_contractual_interest_cents=10000,
                remaining_contract_total_cents=110000,
                amount_paid_cents=0,
                owner_user_id=ids["owner_a_id"],
                created_by_user_id=ids["owner_a_id"],
            )

        loan1 = _make_loan("LN-00001", "fk-loan-1", c1.id, src_lead=lead1.id)
        loan2 = _make_loan("LN-00002", "fk-loan-2", c2.id, src_lead=None)
        db.add_all([loan1, loan2])
        db.flush()

        inst1 = LoanInstallmentModel(
            tenant_id=ids["tenant_a_id"],
            loan_id=loan1.id,
            installment_number=1,
            due_date=today,
            beginning_principal_cents=100000,
            principal_due_cents=100000,
            interest_due_cents=10000,
            scheduled_payment_cents=110000,
            ending_principal_cents=0,
            remaining_amount_cents=110000,
        )
        inst2 = LoanInstallmentModel(
            tenant_id=ids["tenant_a_id"],
            loan_id=loan2.id,
            installment_number=1,
            due_date=today,
            beginning_principal_cents=100000,
            principal_due_cents=100000,
            interest_due_cents=10000,
            scheduled_payment_cents=110000,
            ending_principal_cents=0,
            remaining_amount_cents=110000,
        )
        db.add_all([inst1, inst2])
        db.flush()

        pay1 = LoanPaymentModel(
            tenant_id=ids["tenant_a_id"],
            payment_number="PAY-000001",
            loan_id=loan1.id,
            customer_id=c1.id,
            loan_payment_seq=1,
            idempotency_key="fk-pay-1",
            request_payload_hash="p" * 64,
            status="POSTED",
            currency="MXN",
            amount_cents=50000,
            interest_applied_cents=10000,
            principal_applied_cents=40000,
            prev_loan_status="ACTIVE",
            prev_unpaid_principal_cents=100000,
            prev_remaining_interest_cents=10000,
            prev_remaining_total_cents=110000,
            prev_amount_paid_cents=0,
            payment_method="CASH_RECEIPT",
            paid_at=now_utc,
            recorded_by_user_id=ids["owner_a_id"],
        )
        db.add(pay1)
        db.commit()

        c1_id, c2_id = c1.id, c2.id
        lead1_id = lead1.id
        loan1_id, loan2_id = loan1.id, loan2.id
        inst2_id = inst2.id
        pay1_id = pay1.id
    finally:
        db.close()

    # 4b. 2-col cross-tenant FK: CustomerDocument in Tenant B referencing Tenant A customer
    db = SessionLocal()
    try:
        db.add(
            CustomerDocumentModel(
                tenant_id=ids["tenant_b_id"],
                customer_id=c1_id,  # Tenant A customer!
                document_type="INE_ID",
                original_filename="x.pdf",
                mime_type="application/pdf",
                file_size_bytes=4,
                file_hash="a" * 64,
                file_data=b"%PDF",
                uploaded_by_user_id=ids["owner_b_id"],
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()

    # 4c. 3-col financial FK on loan_payments: payment for loan1 (c1) but with WRONG customer_id (c2) in SAME tenant
    db = SessionLocal()
    try:
        db.add(
            LoanPaymentModel(
                tenant_id=ids["tenant_a_id"],
                payment_number="PAY-000002",
                loan_id=loan1_id,
                customer_id=c2_id,  # WRONG CUSTOMER for loan1!
                loan_payment_seq=2,
                idempotency_key="fk-pay-bad-cust",
                request_payload_hash="p" * 64,
                status="POSTED",
                currency="MXN",
                amount_cents=10000,
                interest_applied_cents=0,
                principal_applied_cents=10000,
                prev_loan_status="ACTIVE",
                prev_unpaid_principal_cents=100000,
                prev_remaining_interest_cents=10000,
                prev_remaining_total_cents=110000,
                prev_amount_paid_cents=0,
                payment_method="CASH_RECEIPT",
                paid_at=now_utc,
                recorded_by_user_id=ids["owner_a_id"],
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()

    # 4d. 3-col financial FK on loan_payment_allocations: payment of loan1 allocated to installment of loan2 in SAME tenant
    db = SessionLocal()
    try:
        db.add(
            LoanPaymentAllocationModel(
                tenant_id=ids["tenant_a_id"],
                payment_id=pay1_id,  # belongs to loan1
                loan_id=loan1_id,
                installment_id=inst2_id,  # belongs to loan2!
                installment_number=1,
                interest_allocated_cents=10000,
                principal_allocated_cents=40000,
                total_allocated_cents=50000,
                prev_principal_paid_cents=0,
                prev_interest_paid_cents=0,
                prev_amount_paid_cents=0,
                prev_remaining_amount_cents=110000,
                prev_paid_at=None,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()

    # 4e. 3-col FK on loans: loan for c2 referencing lead1 (converted to c1) in SAME tenant
    db = SessionLocal()
    try:
        db.add(
            LoanModel(
                tenant_id=ids["tenant_a_id"],
                loan_number="LN-00003",
                idempotency_key="fk-loan-bad-lead",
                request_payload_hash="h" * 64,
                customer_id=c2_id,  # c2 != lead1.converted_customer_id (c1)!
                source_lead_id=lead1_id,
                status="DRAFT",
                product_name="Personal Loan",
                principal_cents=100000,
                monthly_interest_rate_bps=1000,
                assumed_interest_months=1,
                term_installments_count=1,
                contract_rules_json="{}",
                monthly_interest_amount_cents=10000,
                total_interest_cents=10000,
                total_repayment_cents=110000,
                regular_installment_cents=110000,
                final_installment_cents=110000,
                start_date=today,
                first_payment_date=today,
                maturity_date=today,
                owner_user_id=ids["owner_a_id"],
                created_by_user_id=ids["owner_a_id"],
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. PER-TENANT UNIQUENESS (ALLOWED ACROSS TENANTS, REJECTED WITHIN TENANT)
# ---------------------------------------------------------------------------
def test_05_per_tenant_uniqueness_and_partial_posted_reference(mt_env):
    ids = _seed_base_actors(mt_env["SessionLocal"])
    client_a, hdr_a = _login_client("owner.a@alpha.nura.io", ids["password"])
    client_b, hdr_b = _login_client("owner.b@beta.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # Both tenants create CUST-00001 with identical phone_primary
        cust_payload = {
            "entity_type": "INDIVIDUAL",
            "first_name": "Shared",
            "last_name": "Phone",
            "phone_primary": "5544445555",
        }
        ca = client_a.post("/api/customers", headers=hdr_a, json=cust_payload)
        cb = client_b.post("/api/customers", headers=hdr_b, json=cust_payload)
        assert ca.status_code == 200 and ca.json()["customer_number"] == "CUST-00001"
        assert cb.status_code == 200 and cb.json()["customer_number"] == "CUST-00001"

        # Duplicate phone inside Tenant A -> 409
        assert client_a.post("/api/customers", headers=hdr_a, json=cust_payload).status_code == 409

        # Both tenants create LN-00001 with identical idempotency_key
        def _loan_body(cid: int) -> dict:
            return {
                "idempotency_key": "shared-loan-idem-key-01",
                "customer_id": cid,
                "product_name": "Personal Loan",
                "principal_pesos": 10000.0,
                "monthly_interest_rate_percent": 10.0,
                "assumed_interest_months": 2,
                "term_installments_count": 4,
                "start_date": today_iso,
                "first_payment_date": today_iso,
            }

        la = client_a.post("/api/loans", headers=hdr_a, json=_loan_body(ca.json()["id"]))
        lb = client_b.post("/api/loans", headers=hdr_b, json=_loan_body(cb.json()["id"]))
        assert la.status_code == 200 and la.json()["loan"]["loan_number"] == "LN-00001"
        assert lb.status_code == 200 and lb.json()["loan"]["loan_number"] == "LN-00001"

        # Same idempotency_key with different principal within Tenant A -> 409
        bad_la = _loan_body(ca.json()["id"])
        bad_la["principal_pesos"] = 20000.0
        assert client_a.post("/api/loans", headers=hdr_a, json=bad_la).status_code == 409

        # Approve & activate both loans
        for cli, hdr, lid, ref in (
            (client_a, hdr_a, la.json()["loan"]["id"], "DISB-SHARED-01"),
            (client_b, hdr_b, lb.json()["loan"]["id"], "DISB-SHARED-01"),
        ):
            assert cli.post(f"/api/loans/{lid}/approve", headers=hdr, json={}).status_code == 200
            assert (
                cli.post(
                    f"/api/loans/{lid}/activate",
                    headers=hdr,
                    json={"confirm_disbursement": True, "disbursement_method": "SPEI_TRANSFER", "disbursement_reference": ref},
                ).status_code
                == 200
            )

        # Both tenants post PAY-000001 with SAME idempotency_key AND SAME SPEI payment_reference
        pay_body = {
            "idempotency_key": "shared-pay-idem-key-01",
            "amount_pesos": 500.0,
            "payment_method": "SPEI_TRANSFER",
            "payment_reference": "SPEI-UNIQ-777",
        }
        pa = client_a.post(f"/api/loans/{la.json()['loan']['id']}/payments", headers=hdr_a, json=pay_body)
        pb = client_b.post(f"/api/loans/{lb.json()['loan']['id']}/payments", headers=hdr_b, json=pay_body)
        assert pa.status_code == 200 and pa.json()["payment"]["payment_number"] == "PAY-000001"
        assert pb.status_code == 200 and pb.json()["payment"]["payment_number"] == "PAY-000001"

        # Duplicate POSTED payment reference within Tenant A (case/whitespace insensitive) -> 409
        dup_pay = {
            "idempotency_key": "different-pay-idem-key-02",
            "amount_pesos": 500.0,
            "payment_method": "SPEI_TRANSFER",
            "payment_reference": "  spei-uniq-777  ",
        }
        assert client_a.post(f"/api/loans/{la.json()['loan']['id']}/payments", headers=hdr_a, json=dup_pay).status_code == 409
    finally:
        client_a.close()
        client_b.close()


# ---------------------------------------------------------------------------
# 6. CONCURRENT SEQUENCE GENERATION (SAME TENANT & DISTINCT TENANTS)
# ---------------------------------------------------------------------------
def test_06_concurrent_sequence_generation(migrated_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "concurrent_seq.db"
    shutil.copy2(migrated_template_db, db_file)

    eng = _make_sqlite_engine(db_file, wal=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)

    db = SessionLocal()
    tenant_a = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
    tenant_b = TenantModel(name="Concurrent Org B", slug="concurrent-org-b", is_active=True)
    db.add(tenant_b)
    db.flush()
    db.add(TenantSequenceModel(tenant_id=tenant_b.id, entity_type="customers", last_value=0))
    db.commit()
    ta_id, tb_id = tenant_a.id, tenant_b.id
    db.close()

    def _allocate(tid: int) -> tuple[int, str]:
        s = SessionLocal()
        try:
            num = main.generate_next_customer_number(s, tid)
            s.commit()
            return tid, num
        finally:
            s.close()

    n_per_tenant = 12
    work = [ta_id] * n_per_tenant + [tb_id] * n_per_tenant
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_allocate, work))

    eng.dispose()

    nums_a = sorted(num for tid, num in results if tid == ta_id)
    nums_b = sorted(num for tid, num in results if tid == tb_id)
    expected = [f"CUST-{i:05d}" for i in range(1, n_per_tenant + 1)]
    assert nums_a == expected, f"Tenant A sequence mismatch: {nums_a}"
    assert nums_b == expected, f"Tenant B sequence mismatch: {nums_b}"


# ---------------------------------------------------------------------------
# 7. BOOTSTRAP-ADMIN REMOVED (404) & BOOTSTRAP CLI ONE-SHOT ENFORCEMENT
# ---------------------------------------------------------------------------
def test_07_bootstrap_admin_removed_and_cli_one_shot(migrated_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "boot_cli.db"
    shutil.copy2(migrated_template_db, db_file)

    # 7a. Old HTTP bootstrap endpoint removed -> 404
    client = TestClient(main.app, base_url="http://localhost")
    try:
        assert client.post("/api/auth/bootstrap-admin", json={"secret": "123456789012"}).status_code == 404
    finally:
        client.close()

    # 7b. First CLI run succeeds (rc == 0)
    env1 = _clean_env(
        {
            "DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
            "SUPERADMIN_EMAIL": "cli.super@nura.nura.io",
            "SUPERADMIN_PASSWORD": "SuperCliPassword2026!",
        }
    )
    r1 = subprocess.run(
        [sys.executable, "-B", "scripts/bootstrap_superadmin.py"],
        cwd=str(REPO_ROOT),
        env=env1,
        capture_output=True,
        text=True,
    )
    assert r1.returncode == 0, f"First CLI bootstrap failed:\n{r1.stdout}\n{r1.stderr}"

    # 7c. Second CLI run aborts with non-zero rc without modifying DB
    env2 = _clean_env(
        {
            "DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
            "SUPERADMIN_EMAIL": "second.super@nura.nura.io",
            "SUPERADMIN_PASSWORD": "AnotherPassword2026!",
        }
    )
    r2 = subprocess.run(
        [sys.executable, "-B", "scripts/bootstrap_superadmin.py"],
        cwd=str(REPO_ROOT),
        env=env2,
        capture_output=True,
        text=True,
    )
    assert r2.returncode != 0

    conn = sqlite3.connect(db_file)
    supers = conn.execute("SELECT email, is_superadmin FROM users WHERE is_superadmin = 1").fetchall()
    audits = conn.execute(
        "SELECT event_type, scope, tenant_id FROM audit_logs WHERE event_type = 'PLATFORM_SUPERADMIN_BOOTSTRAPPED'"
    ).fetchall()
    conn.close()
    assert supers == [("cli.super@nura.nura.io", 1)]
    assert audits == [("PLATFORM_SUPERADMIN_BOOTSTRAPPED", "PLATFORM", None)]


# ---------------------------------------------------------------------------
# 8. AUTOMATED MIGRATION 011 -> 012 BACKFILL VERIFICATION
# ---------------------------------------------------------------------------
def test_08_migration_011_to_012_backfill_preserves_data(tmp_path: Path):
    db_file = tmp_path / "mig_011_to_012.db"
    r011 = _run_alembic(["upgrade", "011"], db_file)
    assert r011.returncode == 0, f"Upgrade to 011 failed:\n{r011.stdout}\n{r011.stderr}"

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        INSERT INTO users (id, email, full_name, password_hash, role, preferred_language, is_active, created_at)
        VALUES (1, 'legacy@org.nura.io', 'Legacy Admin', 'hash', 'OWNER_ADMIN', 'es', 1, '2026-11-01 00:00:00');

        INSERT INTO conversations (id, user_id, title, created_at, updated_at)
        VALUES (1, 1, 'Legacy Conv', '2026-11-01 00:00:00', '2026-11-01 00:00:00');

        INSERT INTO chat_messages (id, conversation_id, role, content, created_at)
        VALUES (1, 1, 'user', 'Hola', '2026-11-01 00:00:00');

        INSERT INTO audit_logs (id, created_at, actor_user_id, actor_role, event_type, resource_type, resource_id, outcome)
        VALUES
          (1, '2026-11-01 00:00:00', 1, 'OWNER_ADMIN', 'LOGIN_SUCCESS', 'auth', '1', 'SUCCESS'),
          (2, '2026-11-01 00:01:00', NULL, NULL, 'LOGIN_FAILED', 'auth', 'anon', 'ERROR');

        INSERT INTO customers (id, customer_number, entity_type, status, verification_status, full_legal_name, phone_primary, phone_primary_normalized, owner_user_id, created_by_user_id, customer_since, created_at, updated_at)
        VALUES
          (1, 'CUST-00009', 'INDIVIDUAL', 'ACTIVE', 'VERIFIED', 'Legacy Client', '5511110000', '5511110000', 1, 1, '2026-11-01 00:00:00', '2026-11-01 00:00:00', '2026-11-01 00:00:00'),
          (2, 'CUST-LEGACY-X', 'BUSINESS', 'ACTIVE', 'VERIFIED', 'Legacy Corp', '5522220000', '5522220000', 1, 1, '2026-11-01 00:00:00', '2026-11-01 00:00:00', '2026-11-01 00:00:00');

        INSERT INTO leads (id, lead_number, full_name, entity_type, phone_primary, phone_primary_normalized, source, product_interest, requested_amount_cents, currency, stage, priority, owner_user_id, created_by_user_id, converted_customer_id, converted_by_user_id, created_at, updated_at)
        VALUES (1, 'LD-00015', 'Legacy Client', 'INDIVIDUAL', '5511110000', '5511110000', 'REFERRAL', 'Personal Loan', 500000, 'MXN', 'CONVERTED', 'HIGH', 1, 1, 1, 1, '2026-11-01 00:00:00', '2026-11-01 00:00:00');

        INSERT INTO loans (id, loan_number, idempotency_key, request_payload_hash, customer_id, source_lead_id, status, product_name, product_version, currency, principal_cents, monthly_interest_rate_bps, interest_calculation_method, interest_basis, interest_accrual_convention, assumed_interest_months, payment_frequency, term_installments_count, rounding_policy, contract_rules_json, monthly_interest_amount_cents, total_interest_cents, total_repayment_cents, regular_installment_cents, final_installment_cents, start_date, first_payment_date, maturity_date, unpaid_principal_cents, remaining_contractual_interest_cents, remaining_contract_total_cents, amount_paid_cents, owner_user_id, created_by_user_id, created_at, updated_at)
        VALUES (1, 'LN-00004', 'mig-loan-1', 'hash', 1, 1, 'ACTIVE', 'Personal Loan', 'v1.0', 'MXN', 100000, 1000, 'FLAT_ORIGINAL_PRINCIPAL', 'ORIGINAL_PRINCIPAL', 'ON_INSTALLMENT_DUE_DATE', 1, 'WEEKLY', 1, 'FLOOR_REGULAR_EXACT_FINAL', '{}', 10000, 10000, 110000, 110000, 110000, '2026-11-01', '2026-11-08', '2026-11-08', 100000, 10000, 110000, 0, 1, 1, '2026-11-01 00:00:00', '2026-11-01 00:00:00');

        INSERT INTO loan_installments (id, loan_id, installment_number, due_date, beginning_principal_cents, principal_due_cents, interest_due_cents, scheduled_payment_cents, ending_principal_cents, principal_paid_cents, interest_paid_cents, amount_paid_cents, remaining_amount_cents, paid_at, created_at)
        VALUES (1, 1, 1, '2026-11-08', 100000, 100000, 10000, 110000, 0, 0, 0, 0, 110000, NULL, '2026-11-01 00:00:00');

        INSERT INTO loan_payments (id, payment_number, loan_id, customer_id, loan_payment_seq, idempotency_key, request_payload_hash, status, currency, amount_cents, interest_applied_cents, principal_applied_cents, prev_loan_status, prev_unpaid_principal_cents, prev_remaining_interest_cents, prev_remaining_total_cents, prev_amount_paid_cents, payment_method, payment_reference, paid_at, recorded_by_user_id, created_at)
        VALUES (1, 'PAY-000021', 1, 1, 1, 'mig-pay-1', 'hash', 'POSTED', 'MXN', 50000, 10000, 40000, 'ACTIVE', 100000, 10000, 110000, 0, 'SPEI_TRANSFER', 'REF-MIG-1', '2026-11-05 00:00:00', 1, '2026-11-05 00:00:00');

        INSERT INTO loan_payment_allocations (id, payment_id, loan_id, installment_id, installment_number, interest_allocated_cents, principal_allocated_cents, total_allocated_cents, prev_principal_paid_cents, prev_interest_paid_cents, prev_amount_paid_cents, prev_remaining_amount_cents, prev_paid_at, created_at)
        VALUES (1, 1, 1, 1, 1, 10000, 40000, 50000, 0, 0, 0, 110000, NULL, '2026-11-05 00:00:00');
        """
    )
    conn.close()

    r012 = _run_alembic(["upgrade", "head"], db_file)
    assert r012.returncode == 0, f"Upgrade 011->012 failed:\n{r012.stdout}\n{r012.stderr}"

    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA foreign_keys=ON")
    platform_id = conn.execute("SELECT id FROM tenants WHERE slug = 'platform'").fetchone()[0]
    default_id = conn.execute("SELECT id FROM tenants WHERE slug = 'default-org'").fetchone()[0]

    for tbl, expected_cnt in (
        ("users", 1),
        ("conversations", 1),
        ("chat_messages", 1),
        ("customers", 2),
        ("leads", 1),
        ("loans", 1),
        ("loan_installments", 1),
        ("loan_payments", 1),
        ("loan_payment_allocations", 1),
    ):
        assert conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE tenant_id = ?", (default_id,)).fetchone()[0] == expected_cnt
        assert conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE tenant_id = ?", (platform_id,)).fetchone()[0] == 0

    audit_rows = conn.execute("SELECT id, actor_user_id, tenant_id, scope FROM audit_logs ORDER BY id").fetchall()
    assert audit_rows == [(1, 1, default_id, "TENANT"), (2, None, None, "ANONYMOUS")]

    seqs = {
        (r[0], r[1]): r[2]
        for r in conn.execute(
            "SELECT t.slug, ts.entity_type, ts.last_value FROM tenant_sequences ts JOIN tenants t ON t.id = ts.tenant_id"
        ).fetchall()
    }
    assert seqs == {
        ("default-org", "customers"): 9,
        ("default-org", "leads"): 15,
        ("default-org", "loans"): 4,
        ("default-org", "loan_payments"): 21,
        ("platform", "customers"): 0,
        ("platform", "leads"): 0,
        ("platform", "loans"): 0,
        ("platform", "loan_payments"): 0,
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
