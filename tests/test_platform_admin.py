"""Automated security, route coverage, privacy, and platform API suite (Entregable 3 - Bloque 1).

Covers:
A. Commercial guard enforcement (HTTP 403 for SUPERADMIN and platform-tenant accounts across ALL commercial routes,
   both GET and write methods, verifying zero commercial mutations).
B. Platform guard enforcement (HTTP 403 for commercial OWNER_ADMIN across ALL platform routes + PLATFORM_ACCESS_DENIED audit).
C. Full FastAPI route inventory & dependency-graph coverage (detecting unprotected commercial routes or misguarded auth/platform routes)
   complemented by real TestClient requests.
D. Privacy, aggregate metrics isolation (no cross-tenant leakage, no N+1 on tenant list), 404 on missing tenants,
   platform-only audit log scoping/ordering/filtering/limit bounds, and zero password/hash exposure.
E. Authentication contracts (tenant_slug & tenant_name in /api/auth/login and /api/auth/me) + commercial regression.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.routing import Mount

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
    ChatMessageModel,
    ConversationModel,
    CustomerDocumentModel,
    CustomerModel,
    InteractionModel,
    LeadModel,
    LoanInstallmentModel,
    LoanModel,
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
    tmpl_dir = tmp_path_factory.mktemp("p3_template")
    tmpl_db = tmpl_dir / "template_012.db"
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
def p3_env(migrated_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "p3_case.db"
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


def _login_client(email: str, password: str) -> tuple[TestClient, dict, dict]:
    client = TestClient(main.app, base_url="http://localhost")
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.status_code} {r.text}"
    csrf = client.cookies.get("csrf_token")
    assert csrf
    return client, {"Origin": "http://localhost", "X-CSRF-Token": csrf}, r.json()


def _seed_actors(SessionLocal) -> dict:
    db = SessionLocal()
    try:
        platform_t = db.query(TenantModel).filter(TenantModel.slug == "platform").first()
        tenant_a = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        tenant_b = TenantModel(
            name="Beta Financial",
            slug="beta-financial",
            legal_name="Beta Financial SAPI de CV",
            is_active=True,
        )
        tenant_c = TenantModel(
            name="Empty Org",
            slug="empty-org",
            legal_name=None,
            is_active=True,
        )
        db.add_all([tenant_b, tenant_c])
        db.flush()
        for t in (tenant_b, tenant_c):
            for et in ("customers", "leads", "loans", "loan_payments"):
                db.add(TenantSequenceModel(tenant_id=t.id, entity_type=et, last_value=0))
        for mc in ("CORE", "LENDING"):
            db.add(TenantModuleModel(tenant_id=tenant_b.id, module_code=mc, is_enabled=True))

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
        inactive_a = UserModel(
            tenant_id=tenant_a.id,
            email="inactive.a@alpha.nura.io",
            full_name="Inactive Alpha",
            password_hash=pw_hash,
            role="OPERATIONS_OFFICER",
            is_superadmin=False,
            is_active=False,
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
        db.add_all([superadmin, owner_a, sales_a, inactive_a, owner_b])
        db.commit()
        return {
            "platform_id": platform_t.id,
            "tenant_a_id": tenant_a.id,
            "tenant_b_id": tenant_b.id,
            "tenant_c_id": tenant_c.id,
            "superadmin_id": superadmin.id,
            "owner_a_id": owner_a.id,
            "sales_a_id": sales_a.id,
            "inactive_a_id": inactive_a.id,
            "owner_b_id": owner_b.id,
            "password": pw,
        }
    finally:
        db.close()


def _snapshot_commercial_counts(SessionLocal) -> dict:
    db = SessionLocal()
    try:
        return {
            "users": db.query(UserModel).count(),
            "customers": db.query(CustomerModel).count(),
            "customer_documents": db.query(CustomerDocumentModel).count(),
            "leads": db.query(LeadModel).count(),
            "interactions": db.query(InteractionModel).count(),
            "loans": db.query(LoanModel).count(),
            "loan_installments": db.query(LoanInstallmentModel).count(),
            "loan_payments": db.query(LoanPaymentModel).count(),
            "conversations": db.query(ConversationModel).count(),
            "chat_messages": db.query(ChatMessageModel).count(),
        }
    finally:
        db.close()


def _collect_resolved_dependencies(dependant) -> set:
    """Recursively collect all dependency callables (direct, nested, router-level)."""
    callables = set()
    for dep in dependant.dependencies:
        if dep.call is not None:
            callables.add(dep.call)
        callables.update(_collect_resolved_dependencies(dep))
    return callables


def _iter_all_api_routes(routes, prefix: str = ""):
    """Walk FastAPI routes recursively, including any mounted sub-apps or routers."""
    for route in routes:
        if isinstance(route, APIRoute):
            full_path = f"{prefix}{route.path}"
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, full_path, route
        elif isinstance(route, Mount) and hasattr(route, "routes"):
            yield from _iter_all_api_routes(route.routes, prefix=f"{prefix}{route.path}")
        elif hasattr(route, "original_router") and hasattr(route.original_router, "routes"):
            sub_prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            yield from _iter_all_api_routes(route.original_router.routes, prefix=f"{prefix}{sub_prefix}")


# ---------------------------------------------------------------------------
# A. COMMERCIAL GUARD PROTECTION (SUPERADMIN & PLATFORM TENANT -> HTTP 403)
# ---------------------------------------------------------------------------
def test_a1_superadmin_receives_403_on_all_commercial_routes_without_mutations(p3_env):
    ids = _seed_actors(p3_env["SessionLocal"])
    client_a, hdr_a, _ = _login_client("owner.a@alpha.nura.io", ids["password"])
    client_s, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])

    try:
        # Seed real commercial entities in Tenant A so path params point to real IDs
        r_cust = client_a.post(
            "/api/customers",
            headers=hdr_a,
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Maria",
                "last_name": "Lopez",
                "phone_primary": "5511002200",
                "email": "maria@alpha.nura.io",
            },
        )
        assert r_cust.status_code == 200, r_cust.text
        cust_id = r_cust.json()["id"]

        pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
        r_doc = client_a.post(
            f"/api/customers/{cust_id}/documents",
            headers=hdr_a,
            data={"document_type": "INE_ID"},
            files={"file": ("ine.pdf", pdf_bytes, "application/pdf")},
        )
        assert r_doc.status_code == 200, r_doc.text
        doc_id = r_doc.json()["id"]

        r_lead = client_a.post(
            "/api/leads",
            headers=hdr_a,
            json={
                "full_name": "Prospect One",
                "phone_primary": "5511003300",
                "product_interest": "Personal Loan",
                "requested_amount_pesos": 15000.0,
            },
        )
        assert r_lead.status_code == 200, r_lead.text
        lead_id = r_lead.json()["id"]

        _, _, _, today_local = main.get_local_day_bounds_utc()
        today_iso = today_local.isoformat()
        r_loan = client_a.post(
            "/api/loans",
            headers=hdr_a,
            json={
                "idempotency_key": "p3-loan-0001",
                "customer_id": cust_id,
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
        loan_id = r_loan.json()["loan"]["id"]
        assert client_a.post(f"/api/loans/{loan_id}/approve", headers=hdr_a, json={}).status_code == 200
        assert (
            client_a.post(
                f"/api/loans/{loan_id}/activate",
                headers=hdr_a,
                json={
                    "confirm_disbursement": True,
                    "disbursement_method": "SPEI_TRANSFER",
                    "disbursement_reference": "DISB-P3-001",
                },
            ).status_code
            == 200
        )
        r_pay = client_a.post(
            f"/api/loans/{loan_id}/payments",
            headers=hdr_a,
            json={
                "idempotency_key": "p3-pay-0001",
                "amount_pesos": 1000.0,
                "payment_method": "SPEI_TRANSFER",
                "payment_reference": "SPEI-P3-001",
            },
        )
        assert r_pay.status_code == 200, r_pay.text
        pay_id = r_pay.json()["payment"]["id"]

        r_conv = client_a.post("/api/conversations", headers=hdr_a, json={"title": "Tenant A Conv"})
        assert r_conv.status_code == 200, r_conv.text
        conv_id = r_conv.json()["id"]

        before_counts = _snapshot_commercial_counts(p3_env["SessionLocal"])

        # Exhaustive list of ALL 38 commercial routes (GET + write methods)
        commercial_calls = [
            # Tenant team administration & tenant audit (4)
            ("POST", "/api/auth/register", {"json": {"email": "rogue@platform.io", "password": "Password123!", "role": "SALES_AGENT"}}),
            ("GET", "/api/admin/users", {}),
            ("PATCH", f"/api/admin/users/{ids['sales_a_id']}", {"json": {"is_active": False}}),
            ("GET", "/api/admin/audit-logs", {}),
            # Customers, interactions & documents (10)
            ("GET", "/api/customers", {}),
            ("POST", "/api/customers", {"json": {"entity_type": "INDIVIDUAL", "first_name": "Bad", "last_name": "Actor", "phone_primary": "5588889999"}}),
            ("GET", f"/api/customers/{cust_id}", {}),
            ("PATCH", f"/api/customers/{cust_id}", {"json": {"first_name": "Tampered"}}),
            ("GET", f"/api/customers/{cust_id}/interactions", {}),
            ("POST", f"/api/customers/{cust_id}/interactions", {"json": {"interaction_type": "NOTE", "content": "Unauthorized"}}),
            ("POST", f"/api/customers/{cust_id}/documents", {"data": {"document_type": "INE_ID"}, "files": {"file": ("x.pdf", pdf_bytes, "application/pdf")}}),
            ("GET", f"/api/customers/{cust_id}/documents", {}),
            ("GET", f"/api/customers/{cust_id}/documents/{doc_id}/download", {}),
            ("DELETE", f"/api/customers/{cust_id}/documents/{doc_id}", {}),
            # Leads & pipeline (7)
            ("GET", "/api/leads", {}),
            ("GET", "/api/leads/calendar?year=2026&month=11", {}),
            ("POST", "/api/leads", {"json": {"full_name": "Unauthorized Lead", "phone_primary": "5577778888", "product_interest": "Personal Loan", "requested_amount_pesos": 5000.0}}),
            ("GET", f"/api/leads/{lead_id}", {}),
            ("PATCH", f"/api/leads/{lead_id}", {"json": {"full_name": "Tampered Lead"}}),
            ("POST", f"/api/leads/{lead_id}/interactions", {"json": {"interaction_type": "CALL", "content": "Unauthorized"}}),
            ("POST", f"/api/leads/{lead_id}/convert", {"json": {}}),
            # Loans, collections & payments (14)
            ("GET", "/api/loans", {}),
            ("GET", "/api/loans/dashboard-summary", {}),
            ("GET", f"/api/loans/customers/{cust_id}/summary", {}),
            ("POST", "/api/loans", {"json": {"idempotency_key": "super-loan-1", "customer_id": cust_id, "product_name": "Personal Loan", "principal_pesos": 5000.0, "monthly_interest_rate_percent": 10.0, "assumed_interest_months": 1, "term_installments_count": 4, "start_date": today_iso, "first_payment_date": today_iso}}),
            ("GET", f"/api/loans/{loan_id}", {}),
            ("POST", f"/api/loans/{loan_id}/approve", {"json": {"approval_notes": "No"}}),
            ("POST", f"/api/loans/{loan_id}/activate", {"json": {"confirm_disbursement": True, "disbursement_method": "CASH_RECEIPT"}}),
            ("POST", f"/api/loans/{loan_id}/cancel", {"json": {"cancellation_reason": "No"}}),
            ("POST", f"/api/loans/{loan_id}/default", {"json": {"default_reason": "No"}}),
            ("POST", f"/api/loans/{loan_id}/collections-log", {"json": {"interaction_type": "CALL", "outcome": "CONTACTED", "notes": "No"}}),
            ("GET", "/api/collections/queue", {}),
            ("GET", f"/api/loans/{loan_id}/payments", {}),
            ("POST", f"/api/loans/{loan_id}/payments", {"json": {"idempotency_key": "super-pay-1", "amount_pesos": 500.0, "payment_method": "CASH_RECEIPT"}}),
            ("POST", f"/api/loans/{loan_id}/payments/{pay_id}/reverse", {"json": {"reversal_reason": "Unauthorized"}}),
            # Broker (15)
            ("GET", "/api/broker/dashboard-summary", {}),
            ("GET", f"/api/broker/customers/{cust_id}/summary", {}),
            ("GET", "/api/broker/lenders", {}),
            ("POST", "/api/broker/lenders", {"json": {"code": "SUP-BNK", "name": "Super Bank"}}),
            ("PATCH", "/api/broker/lenders/1", {"json": {"is_active": False}}),
            ("GET", "/api/broker/cases", {}),
            ("POST", "/api/broker/cases", {"json": {"idempotency_key": "super-brk-001", "customer_id": cust_id, "product_type": "PyME", "requested_amount_pesos": 100000.0}}),
            ("GET", "/api/broker/cases/1", {}),
            ("PATCH", "/api/broker/cases/1", {"json": {"notes": "Unauthorized"}}),
            ("POST", "/api/broker/cases/1/submissions", {"json": {"lender_id": 1}}),
            ("PATCH", "/api/broker/cases/1/submissions/1", {"json": {"status": "IN_UNDERWRITING"}}),
            ("POST", "/api/broker/cases/1/submissions/1/select", {"json": {}}),
            ("POST", "/api/broker/cases/1/fund", {"json": {"funded_amount_pesos": 50000.0}}),
            ("GET", "/api/broker/commissions", {}),
            ("PATCH", "/api/broker/commissions/1", {"json": {"status": "PAID"}}),
            # Dashboard & conversations / assistant (6)
            ("GET", "/api/dashboard/summary", {}),
            ("GET", "/api/conversations", {}),
            ("POST", "/api/conversations", {"json": {"title": "Super Conv"}}),
            ("GET", f"/api/conversations/{conv_id}/messages", {}),
            ("DELETE", f"/api/conversations/{conv_id}", {}),
            ("POST", "/chat-ejecutivo", {"json": {"mensaje": "Hola"}}),
        ]
        assert len(commercial_calls) == 56

        for method, path, kwargs in commercial_calls:
            req_kwargs = dict(kwargs)
            if method in ("POST", "PATCH", "PUT", "DELETE"):
                req_kwargs["headers"] = hdr_s
            resp = client_s.request(method, path, **req_kwargs)
            assert resp.status_code == 403, f"Expected 403 for SUPERADMIN on {method} {path}, got {resp.status_code}: {resp.text}"

        # Confirm ZERO commercial records were created or deleted
        after_counts = _snapshot_commercial_counts(p3_env["SessionLocal"])
        assert after_counts == before_counts

        # Confirm existing commercial records in Tenant A were not mutated
        db = p3_env["SessionLocal"]()
        try:
            assert db.query(CustomerModel).filter(CustomerModel.id == cust_id).first().first_name == "Maria"
            assert db.query(LeadModel).filter(LeadModel.id == lead_id).first().full_name == "Prospect One"
            assert db.query(UserModel).filter(UserModel.id == ids["sales_a_id"]).first().is_active is True
            assert db.query(LoanModel).filter(LoanModel.id == loan_id).first().status == "ACTIVE"
        finally:
            db.close()
    finally:
        client_a.close()
        client_s.close()


def test_a2_platform_slug_user_and_rogue_superadmin_flag_both_rejected_403(p3_env):
    """Guard must reject BOTH is_superadmin==True (any tenant) AND tenant.slug=='platform' (even if is_superadmin==False)."""
    ids = _seed_actors(p3_env["SessionLocal"])
    db = p3_env["SessionLocal"]()
    try:
        pw_hash = main.pwd_context.hash(ids["password"])
        # Case 1: non-superadmin inside tenant.slug == 'platform'
        platform_staff = UserModel(
            tenant_id=ids["platform_id"],
            email="staff@platform.nura.io",
            full_name="Platform Staff",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        # Case 2: is_superadmin == True inside a non-platform tenant
        hybrid_user = UserModel(
            tenant_id=ids["tenant_a_id"],
            email="hybrid@alpha.nura.io",
            full_name="Hybrid Flag",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=True,
            is_active=True,
        )
        db.add_all([platform_staff, hybrid_user])
        db.commit()
    finally:
        db.close()

    c_staff, h_staff, _ = _login_client("staff@platform.nura.io", ids["password"])
    c_hybrid, h_hybrid, _ = _login_client("hybrid@alpha.nura.io", ids["password"])
    try:
        for cli, hdr in ((c_staff, h_staff), (c_hybrid, h_hybrid)):
            assert cli.get("/api/customers").status_code == 403
            assert cli.get("/api/dashboard/summary").status_code == 403
            assert cli.post("/api/conversations", headers=hdr, json={"title": "Blocked"}).status_code == 403
            # Neither should pass require_superadmin either
            assert cli.get("/api/platform/tenants").status_code == 403
    finally:
        c_staff.close()
        c_hybrid.close()


# ---------------------------------------------------------------------------
# B. PLATFORM GUARD PROTECTION (COMMERCIAL OWNER_ADMIN -> HTTP 403 + AUDIT)
# ---------------------------------------------------------------------------
def test_b_commercial_owner_receives_403_on_all_platform_routes_and_audited(p3_env):
    ids = _seed_actors(p3_env["SessionLocal"])
    client_a, hdr_a, _ = _login_client("owner.a@alpha.nura.io", ids["password"])

    try:
        platform_calls = [
            ("GET", "/api/admin/schema-status", {}),
            ("GET", "/api/platform/modules", {}),
            ("GET", "/api/platform/tenants", {}),
            ("POST", "/api/platform/tenants", {"json": {"name": "Rogue Org", "slug": "rogue-org", "owner_email": "r@rogue.io", "owner_password": "Password123!"}}),
            ("GET", f"/api/platform/tenants/{ids['tenant_a_id']}", {}),
            ("PATCH", f"/api/platform/tenants/{ids['tenant_b_id']}", {"json": {"name": "Hijacked"}}),
            ("PATCH", f"/api/platform/tenants/{ids['tenant_b_id']}/modules", {"json": {"modules": [{"module_code": "LENDING", "is_enabled": False}]}}),
            ("GET", f"/api/platform/tenants/{ids['tenant_a_id']}/users", {}),
            ("GET", "/api/platform/audit-logs", {}),
        ]

        for method, path, kwargs in platform_calls:
            req_kwargs = dict(kwargs)
            if method in ("POST", "PATCH", "PUT", "DELETE"):
                req_kwargs["headers"] = hdr_a
            resp = client_a.request(method, path, **req_kwargs)
            assert resp.status_code == 403, f"Expected 403 for OWNER_ADMIN on {method} {path}, got {resp.status_code}"

        # Verify PLATFORM_ACCESS_DENIED audit logs were recorded with scope='PLATFORM' & outcome='DENIED'
        db = p3_env["SessionLocal"]()
        try:
            denied_logs = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "PLATFORM_ACCESS_DENIED",
                    AuditLogModel.actor_user_id == ids["owner_a_id"],
                )
                .all()
            )
            assert len(denied_logs) == len(platform_calls)
            recorded_paths = {log.resource_id for log in denied_logs}
            expected_paths = {path for _, path, _ in platform_calls}
            assert recorded_paths == expected_paths
            for log in denied_logs:
                assert log.scope == "PLATFORM"
                assert log.outcome == "DENIED"
                assert log.tenant_id is None
        finally:
            db.close()
    finally:
        client_a.close()


# ---------------------------------------------------------------------------
# C. FASTAPI ROUTE INVENTORY & EFFECTIVE DEPENDENCY COVERAGE
# ---------------------------------------------------------------------------
def test_c_fastapi_route_inventory_and_effective_dependency_coverage(p3_env):
    """Inspect EVERY route registered on main.app (including inherited/nested dependencies) + verify with TestClient."""
    PUBLIC_ROUTES = {
        ("GET", "/"),
        ("GET", "/api/health"),
    }
    AUTH_OWN_ACCOUNT_ROUTES = {
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/password-reset/request"),
        ("POST", "/api/auth/password-reset/confirm"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("PATCH", "/api/auth/me/language"),
        ("POST", "/api/auth/me/password"),
    }
    PLATFORM_ROUTES = {
        ("GET", "/api/admin/schema-status"),
        ("GET", "/api/platform/modules"),
        ("GET", "/api/platform/tenants"),
        ("POST", "/api/platform/tenants"),
        ("GET", "/api/platform/tenants/{tenant_id}"),
        ("PATCH", "/api/platform/tenants/{tenant_id}"),
        ("PATCH", "/api/platform/tenants/{tenant_id}/modules"),
        ("GET", "/api/platform/tenants/{tenant_id}/users"),
        ("GET", "/api/platform/audit-logs"),
    }
    COMMERCIAL_ROUTES = {
        ("POST", "/api/auth/register"),
        ("GET", "/api/admin/users"),
        ("PATCH", "/api/admin/users/{target_user_id}"),
        ("GET", "/api/admin/audit-logs"),
        ("GET", "/api/customers"),
        ("POST", "/api/customers"),
        ("GET", "/api/customers/{customer_id}"),
        ("PATCH", "/api/customers/{customer_id}"),
        ("GET", "/api/customers/{customer_id}/interactions"),
        ("POST", "/api/customers/{customer_id}/interactions"),
        ("POST", "/api/customers/{customer_id}/documents"),
        ("GET", "/api/customers/{customer_id}/documents"),
        ("GET", "/api/customers/{customer_id}/documents/{doc_id}/download"),
        ("DELETE", "/api/customers/{customer_id}/documents/{doc_id}"),
        ("GET", "/api/leads"),
        ("GET", "/api/leads/calendar"),
        ("POST", "/api/leads"),
        ("GET", "/api/leads/{lead_id}"),
        ("PATCH", "/api/leads/{lead_id}"),
        ("POST", "/api/leads/{lead_id}/interactions"),
        ("POST", "/api/leads/{lead_id}/convert"),
        ("GET", "/api/loans"),
        ("GET", "/api/loans/dashboard-summary"),
        ("GET", "/api/loans/customers/{customer_id}/summary"),
        ("POST", "/api/loans"),
        ("GET", "/api/loans/{loan_id}"),
        ("POST", "/api/loans/{loan_id}/approve"),
        ("POST", "/api/loans/{loan_id}/activate"),
        ("POST", "/api/loans/{loan_id}/cancel"),
        ("POST", "/api/loans/{loan_id}/default"),
        ("POST", "/api/loans/{loan_id}/collections-log"),
        ("GET", "/api/collections/queue"),
        ("GET", "/api/loans/{loan_id}/payments"),
        ("POST", "/api/loans/{loan_id}/payments"),
        ("POST", "/api/loans/{loan_id}/payments/{payment_id}/reverse"),
        ("GET", "/api/broker/dashboard-summary"),
        ("GET", "/api/broker/customers/{customer_id}/summary"),
        ("GET", "/api/broker/lenders"),
        ("POST", "/api/broker/lenders"),
        ("PATCH", "/api/broker/lenders/{lender_id}"),
        ("GET", "/api/broker/cases"),
        ("POST", "/api/broker/cases"),
        ("GET", "/api/broker/cases/{case_id}"),
        ("PATCH", "/api/broker/cases/{case_id}"),
        ("POST", "/api/broker/cases/{case_id}/submissions"),
        ("PATCH", "/api/broker/cases/{case_id}/submissions/{submission_id}"),
        ("POST", "/api/broker/cases/{case_id}/submissions/{submission_id}/select"),
        ("POST", "/api/broker/cases/{case_id}/fund"),
        ("GET", "/api/broker/commissions"),
        ("PATCH", "/api/broker/commissions/{commission_id}"),
        ("GET", "/api/dashboard/summary"),
        ("GET", "/api/conversations"),
        ("POST", "/api/conversations"),
        ("GET", "/api/conversations/{conv_id}/messages"),
        ("DELETE", "/api/conversations/{conv_id}"),
        ("POST", "/chat-ejecutivo"),
    }

    all_expected = PUBLIC_ROUTES | AUTH_OWN_ACCOUNT_ROUTES | PLATFORM_ROUTES | COMMERCIAL_ROUTES
    discovered = {}
    for method, path, route in _iter_all_api_routes(main.app.routes):
        discovered[(method, path)] = _collect_resolved_dependencies(route.dependant)

    # Fail immediately if any route was added/removed without classification
    assert set(discovered.keys()) == all_expected, (
        f"Unclassified routes: {set(discovered.keys()) - all_expected}; "
        f"Missing routes: {all_expected - set(discovered.keys())}"
    )

    for key in PUBLIC_ROUTES:
        deps = discovered[key]
        assert main.require_commercial_tenant not in deps, f"Public route {key} must not require commercial tenant"
        assert main.require_superadmin not in deps, f"Public route {key} must not require superadmin"

    for key in AUTH_OWN_ACCOUNT_ROUTES:
        deps = discovered[key]
        assert main.require_commercial_tenant not in deps, f"Auth/own-account route {key} accidentally protected by require_commercial_tenant"
        assert main.require_superadmin not in deps, f"Auth/own-account route {key} accidentally protected by require_superadmin"

    for key in PLATFORM_ROUTES:
        deps = discovered[key]
        assert main.require_superadmin in deps, f"Platform route {key} missing require_superadmin"
        assert main.require_commercial_tenant not in deps, f"Platform route {key} must not have require_commercial_tenant"

    for key in COMMERCIAL_ROUTES:
        deps = discovered[key]
        assert main.require_commercial_tenant in deps, f"Commercial route {key} missing effective require_commercial_tenant guard"
        assert main.require_superadmin not in deps, f"Commercial route {key} must not have require_superadmin"

    # Complement static graph verification with live TestClient calls for SUPERADMIN on non-commercial routes
    ids = _seed_actors(p3_env["SessionLocal"])
    client_s, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    try:
        assert client_s.get("/api/health").status_code == 200
        assert client_s.get("/api/auth/me").status_code == 200
        assert client_s.patch("/api/auth/me/language", headers=hdr_s, json={"preferred_language": "es"}).status_code == 200
        assert client_s.get("/api/platform/tenants").status_code == 200
        assert client_s.get("/api/platform/audit-logs").status_code == 200
        assert client_s.post("/api/auth/logout", headers=hdr_s).status_code == 200
    finally:
        client_s.close()


# ---------------------------------------------------------------------------
# D. PRIVACY, METRICS ISOLATION, NO N+1, 404 & PLATFORM AUDIT LOGS
# ---------------------------------------------------------------------------
def test_d_platform_endpoints_metrics_isolation_privacy_and_404(p3_env):
    ids = _seed_actors(p3_env["SessionLocal"])
    client_a, hdr_a, _ = _login_client("owner.a@alpha.nura.io", ids["password"])
    client_b, hdr_b, _ = _login_client("owner.b@beta.nura.io", ids["password"])
    client_s, hdr_s, _ = _login_client("super@nura.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # Tenant A: 3 users (2 active, 1 inactive), 2 customers, 2 leads, 1 loan
        ca1 = client_a.post(
            "/api/customers",
            headers=hdr_a,
            json={"entity_type": "INDIVIDUAL", "first_name": "SecretAlice", "last_name": "Alpha", "phone_primary": "5512001111"},
        ).json()
        client_a.post(
            "/api/customers",
            headers=hdr_a,
            json={"entity_type": "INDIVIDUAL", "first_name": "SecretBob", "last_name": "Alpha", "phone_primary": "5512002222"},
        )
        client_a.post(
            "/api/leads",
            headers=hdr_a,
            json={"full_name": "Lead A1", "phone_primary": "5512003333", "product_interest": "Personal Loan", "requested_amount_pesos": 12000.0},
        )
        client_a.post(
            "/api/leads",
            headers=hdr_a,
            json={"full_name": "Lead A2", "phone_primary": "5512004444", "product_interest": "Personal Loan", "requested_amount_pesos": 18000.0},
        )
        assert (
            client_a.post(
                "/api/loans",
                headers=hdr_a,
                json={
                    "idempotency_key": "d-loan-a-0001",
                    "customer_id": ca1["id"],
                    "product_name": "Personal Loan",
                    "principal_pesos": 25000.0,
                    "monthly_interest_rate_percent": 10.0,
                    "assumed_interest_months": 2,
                    "term_installments_count": 4,
                    "start_date": today_iso,
                    "first_payment_date": today_iso,
                },
            ).status_code
            == 200
        )

        # Tenant B: 1 user (1 active), 1 customer, 3 leads, 2 loans
        cb1 = client_b.post(
            "/api/customers",
            headers=hdr_b,
            json={"entity_type": "INDIVIDUAL", "first_name": "SecretCarlos", "last_name": "Beta", "phone_primary": "5522001111"},
        ).json()
        for idx in range(3):
            client_b.post(
                "/api/leads",
                headers=hdr_b,
                json={"full_name": f"Lead B{idx}", "phone_primary": f"552200333{idx}", "product_interest": "Personal Loan", "requested_amount_pesos": 9000.0},
            )
        for idx in range(2):
            assert (
                client_b.post(
                    "/api/loans",
                    headers=hdr_b,
                    json={
                        "idempotency_key": f"d-loan-b-000{idx}",
                        "customer_id": cb1["id"],
                        "product_name": "Personal Loan",
                        "principal_pesos": 15000.0,
                        "monthly_interest_rate_percent": 10.0,
                        "assumed_interest_months": 2,
                        "term_installments_count": 4,
                        "start_date": today_iso,
                        "first_payment_date": today_iso,
                    },
                ).status_code
                == 200
            )

        # Trigger ANONYMOUS audit event + PLATFORM events with sentinel password
        anon = TestClient(main.app, base_url="http://localhost")
        assert anon.post("/api/auth/login", json={"email": "ghost@nura.io", "password": "WrongPassword999!"}).status_code == 401
        anon.close()

        sentinel_pw = "UltraSecretOnboardingPw2026!"
        r_created = client_s.post(
            "/api/platform/tenants",
            headers=hdr_s,
            json={
                "name": "Delta Credit",
                "slug": "delta-credit",
                "legal_name": "Delta Credit SA",
                "owner_email": "owner@delta.nura.io",
                "owner_password": sentinel_pw,
                "owner_full_name": "Delta Owner",
            },
        )
        assert r_created.status_code == 200, r_created.text
        assert sentinel_pw not in r_created.text
        assert "$2b$" not in r_created.text
        delta_id = r_created.json()["tenant"]["id"]

        r_upd = client_s.patch(
            f"/api/platform/tenants/{delta_id}",
            headers=hdr_s,
            json={"legal_name": "Delta Credit SAPI"},
        )
        assert r_upd.status_code == 200

        # 1) GET /api/platform/tenants — verify grouped counts & NO N+1 queries
        sql_statements = []

        def _count_sql(conn, cursor, statement, parameters, context, executemany):
            sql_statements.append(statement)

        event.listen(p3_env["engine"], "before_cursor_execute", _count_sql)
        try:
            r_list = client_s.get("/api/platform/tenants")
        finally:
            event.remove(p3_env["engine"], "before_cursor_execute", _count_sql)

        assert r_list.status_code == 200
        # 5 tenants exist; auth + 1 tenant list + 3 GROUP BY queries must be bounded (<< 5*3 N+1)
        assert len(sql_statements) <= 10, f"Possible N+1 detected ({len(sql_statements)} queries): {sql_statements}"

        by_slug = {t["slug"]: t for t in r_list.json()}
        assert by_slug["platform"]["users_count"] == 1
        assert by_slug["platform"]["customers_count"] == 0
        assert by_slug["platform"]["loans_count"] == 0

        assert by_slug["default-org"]["users_count"] == 3
        assert by_slug["default-org"]["customers_count"] == 2
        assert by_slug["default-org"]["loans_count"] == 1

        assert by_slug["beta-financial"]["users_count"] == 1
        assert by_slug["beta-financial"]["customers_count"] == 1
        assert by_slug["beta-financial"]["loans_count"] == 2

        assert by_slug["empty-org"]["users_count"] == 0
        assert by_slug["empty-org"]["customers_count"] == 0
        assert by_slug["empty-org"]["loans_count"] == 0

        assert by_slug["delta-credit"]["users_count"] == 1
        assert by_slug["delta-credit"]["customers_count"] == 0
        assert by_slug["delta-credit"]["loans_count"] == 0

        # 2) GET /api/platform/tenants/{tenant_id} — aggregate metrics only, isolated, 404 on missing
        r_det_a = client_s.get(f"/api/platform/tenants/{ids['tenant_a_id']}")
        assert r_det_a.status_code == 200
        det_a = r_det_a.json()
        assert set(det_a.keys()) == {
            "id",
            "name",
            "slug",
            "legal_name",
            "is_active",
            "created_at",
            "users_count",
            "active_users_count",
            "customers_count",
            "leads_count",
            "loans_count",
            "enabled_modules",
            "lending_entity_name",
            "modules",
        }
        assert det_a["users_count"] == 3
        assert det_a["active_users_count"] == 2
        assert det_a["customers_count"] == 2
        assert det_a["leads_count"] == 2
        assert det_a["loans_count"] == 1
        assert "SecretAlice" not in r_det_a.text
        assert "25000" not in r_det_a.text

        r_det_b = client_s.get(f"/api/platform/tenants/{ids['tenant_b_id']}")
        assert r_det_b.status_code == 200
        det_b = r_det_b.json()
        assert det_b["users_count"] == 1
        assert det_b["active_users_count"] == 1
        assert det_b["customers_count"] == 1
        assert det_b["leads_count"] == 3
        assert det_b["loans_count"] == 2
        assert "SecretCarlos" not in r_det_b.text

        assert client_s.get("/api/platform/tenants/999999").status_code == 404

        # 3) GET /api/platform/tenants/{tenant_id}/users — isolated, exact 6 fields, no password_hash, 404 on missing
        r_users_a = client_s.get(f"/api/platform/tenants/{ids['tenant_a_id']}/users")
        assert r_users_a.status_code == 200
        users_a = r_users_a.json()
        assert [u["email"] for u in users_a] == [
            "owner.a@alpha.nura.io",
            "sales.a@alpha.nura.io",
            "inactive.a@alpha.nura.io",
        ]
        for u in users_a:
            assert set(u.keys()) == {"id", "email", "full_name", "role", "is_active", "created_at"}
            assert "password_hash" not in u
            assert "is_superadmin" not in u
        assert "$2b$" not in r_users_a.text
        assert ids["password"] not in r_users_a.text

        r_users_b = client_s.get(f"/api/platform/tenants/{ids['tenant_b_id']}/users")
        assert r_users_b.status_code == 200
        assert [u["email"] for u in r_users_b.json()] == ["owner.b@beta.nura.io"]

        assert client_s.get("/api/platform/tenants/999999/users").status_code == 404

        # 4) GET /api/platform/audit-logs — scope='PLATFORM' only, ordered created_at DESC, id DESC, filters & limit bounds
        # Insert deterministic platform audit rows with explicit timestamps to verify ordering
        db = p3_env["SessionLocal"]()
        try:
            t0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
            db.add_all(
                [
                    AuditLogModel(
                        tenant_id=None,
                        scope="PLATFORM",
                        created_at=t0,
                        actor_user_id=ids["superadmin_id"],
                        actor_role="OWNER_ADMIN",
                        event_type="PLATFORM_ORDER_TEST",
                        resource_type="tenant",
                        resource_id="ord-1",
                        outcome="SUCCESS",
                    ),
                    AuditLogModel(
                        tenant_id=None,
                        scope="PLATFORM",
                        created_at=t0 + timedelta(seconds=10),
                        actor_user_id=ids["superadmin_id"],
                        actor_role="OWNER_ADMIN",
                        event_type="PLATFORM_ORDER_TEST",
                        resource_type="tenant",
                        resource_id="ord-2",
                        outcome="SUCCESS",
                    ),
                    AuditLogModel(
                        tenant_id=None,
                        scope="PLATFORM",
                        created_at=t0 + timedelta(seconds=10),
                        actor_user_id=ids["superadmin_id"],
                        actor_role="OWNER_ADMIN",
                        event_type="PLATFORM_ORDER_TEST",
                        resource_type="tenant",
                        resource_id="ord-3",
                        outcome="SUCCESS",
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()

        r_logs = client_s.get("/api/platform/audit-logs")
        assert r_logs.status_code == 200
        logs = r_logs.json()
        assert len(logs) >= 5
        assert all(entry["scope"] == "PLATFORM" for entry in logs)
        event_types = {entry["event_type"] for entry in logs}
        assert "PLATFORM_TENANT_CREATED" in event_types
        assert "PLATFORM_TENANT_UPDATED" in event_types
        assert "CUSTOMER_CREATED" not in event_types
        assert "LOGIN_FAILED" not in event_types
        assert sentinel_pw not in r_logs.text
        assert "$2b$" not in r_logs.text

        # Ordering check: created_at DESC, id DESC
        r_ord = client_s.get("/api/platform/audit-logs?event_type=PLATFORM_ORDER_TEST")
        assert r_ord.status_code == 200
        ord_items = r_ord.json()
        assert [i["resource_id"] for i in ord_items] == ["ord-3", "ord-2", "ord-1"]

        # Filtering by event_type + resource_id
        r_filt = client_s.get(f"/api/platform/audit-logs?event_type=PLATFORM_TENANT_CREATED&resource_id={delta_id}")
        assert r_filt.status_code == 200
        filt_items = r_filt.json()
        assert len(filt_items) == 1
        assert filt_items[0]["resource_id"] == str(delta_id)
        assert filt_items[0]["event_type"] == "PLATFORM_TENANT_CREATED"

        # Limit bounds: valid limit=2, invalid limit=0 and limit=201 -> 422
        assert len(client_s.get("/api/platform/audit-logs?limit=2").json()) == 2
        assert client_s.get("/api/platform/audit-logs?limit=0").status_code == 422
        assert client_s.get("/api/platform/audit-logs?limit=201").status_code == 422

        # Verify no password ever stored in audit_logs table
        db = p3_env["SessionLocal"]()
        try:
            all_details = [r[0] or "" for r in db.query(AuditLogModel.details_json).all()]
            combined = "\n".join(all_details)
            assert sentinel_pw not in combined
            assert ids["password"] not in combined
            assert "$2b$" not in combined
        finally:
            db.close()
    finally:
        client_a.close()
        client_b.close()
        client_s.close()


# ---------------------------------------------------------------------------
# E. AUTH CONTRACTS (tenant_slug & tenant_name) & COMMERCIAL REGRESSION
# ---------------------------------------------------------------------------
def test_e_auth_contracts_include_real_tenant_context_and_commercial_flow_works(p3_env):
    ids = _seed_actors(p3_env["SessionLocal"])

    c_super, h_super, login_super = _login_client("super@nura.nura.io", ids["password"])
    c_a, h_a, login_a = _login_client("owner.a@alpha.nura.io", ids["password"])
    c_b, h_b, login_b = _login_client("owner.b@beta.nura.io", ids["password"])

    try:
        # Login responses carry real tenant_slug & tenant_name plus all existing fields
        assert login_super["tenant_id"] == ids["platform_id"]
        assert login_super["tenant_slug"] == "platform"
        assert login_super["tenant_name"] == "NURA Platform"
        assert login_super["is_superadmin"] is True

        assert login_a["tenant_id"] == ids["tenant_a_id"]
        assert login_a["tenant_slug"] == "default-org"
        assert login_a["tenant_name"] == "Default Organization"
        assert login_a["is_superadmin"] is False

        assert login_b["tenant_id"] == ids["tenant_b_id"]
        assert login_b["tenant_slug"] == "beta-financial"
        assert login_b["tenant_name"] == "Beta Financial"
        assert login_b["is_superadmin"] is False

        # /api/auth/me responses carry real tenant_slug & tenant_name plus all existing fields
        me_super = c_super.get("/api/auth/me").json()
        assert me_super["tenant_slug"] == "platform"
        assert me_super["tenant_name"] == "NURA Platform"
        assert me_super["is_superadmin"] is True
        assert "supported_products" in me_super

        me_a = c_a.get("/api/auth/me").json()
        assert me_a["tenant_slug"] == "default-org"
        assert me_a["tenant_name"] == "Default Organization"
        assert me_a["is_superadmin"] is False

        # Updating tenant name via platform reflects dynamically on /api/auth/me (not hardcoded/cached)
        assert (
            c_super.patch(
                f"/api/platform/tenants/{ids['tenant_b_id']}",
                headers=h_super,
                json={"name": "Beta Capital Group"},
            ).status_code
            == 200
        )
        me_b = c_b.get("/api/auth/me").json()
        assert me_b["tenant_slug"] == "beta-financial"
        assert me_b["tenant_name"] == "Beta Capital Group"

        # Commercial users retain full normal CRM operation (default-org & beta-financial)
        r_cust = c_a.post(
            "/api/customers",
            headers=h_a,
            json={"entity_type": "INDIVIDUAL", "first_name": "Normal", "last_name": "User", "phone_primary": "5566778899"},
        )
        assert r_cust.status_code == 200
        assert c_a.get("/api/dashboard/summary").json()["counts"]["customers"] == 1
        assert c_a.get("/api/admin/users").status_code == 200
        assert c_a.get("/api/admin/audit-logs").status_code == 200
        r_conv = c_a.post("/api/conversations", headers=h_a, json={"title": "Normal Conv"})
        assert r_conv.status_code == 200
        assert c_a.get("/api/conversations").status_code == 200
        assert c_a.delete(f"/api/conversations/{r_conv.json()['id']}", headers=h_a).status_code == 200
    finally:
        c_super.close()
        c_a.close()
        c_b.close()
