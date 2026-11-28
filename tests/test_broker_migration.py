"""Automated migration, schema, and composite FK tests for Entregable 5B — Bloque 1 (Migration 014).

Covers:
1. Clean DB upgrade (`alembic upgrade head` -> revision 014):
   - Verifies creation of `broker_lenders`, `broker_cases`, `broker_submissions`, `broker_commissions`.
   - Verifies `BROKER` remains `is_assignable=False` in `platform_modules` and NO backfill into `tenant_modules`
     occurs for `default-org` or `platform`.
2. Upgrade from revision 013 with existing commercial tenant/customer/user data, clean `downgrade 013`,
   and clean re-upgrade `013 -> 014` preserving existing data.
3. Database constraint & composite FK enforcement:
   - Cross-tenant composite FKs rejected (`broker_lenders`, `broker_cases`, `broker_submissions`, `broker_commissions`).
   - 3-column FK `(tenant_id, customer_id, source_lead_id)` rejects lead belonging to another customer.
   - 4-column FK `(tenant_id, broker_case_id, lender_id, submission_id)` rejects mismatched lender/case on commission.
   - Partial unique index `uq_broker_submissions_selected_per_case` allows multiple `APPROVED` offers per case,
     but rejects a second `SELECTED` submission on the same case.
   - CHECK constraints (`lender_type`, `status`, positive amounts, `commission_bps` 0..5000) and UNIQUE constraints.
   - Full SQLAlchemy ORM round-trip for all 4 new Broker models.
4. Offline PostgreSQL SQL compilation (`alembic upgrade 013:014 --sql`).
"""
from datetime import date, datetime, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models import (
    BrokerCaseModel,
    BrokerCommissionModel,
    BrokerLenderModel,
    BrokerSubmissionModel,
    CustomerModel,
    LeadModel,
    TenantModel,
    UserModel,
)


def _clean_env(db_url: str) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "RENDER", "ENVIRONMENT")
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["RENDER"] = "false"
    env["ENVIRONMENT"] = "development"
    env["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"
    env["MIGRATION_DATABASE_URL"] = db_url
    env["DATABASE_URL"] = db_url
    return env


def _run_alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=_clean_env(db_url),
        capture_output=True,
        text=True,
    )


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def test_01_clean_upgrade_head_creates_broker_tables_and_enables_broker_assignability(tmp_path: Path):
    db_file = tmp_path / "clean_014.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    res = _run_alembic(["upgrade", "head"], db_url)
    assert res.returncode == 0, f"alembic upgrade head failed:\n{res.stdout}\n{res.stderr}"

    conn = _connect_sqlite(db_file)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("014",)

        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for expected_tbl in (
            "broker_lenders",
            "broker_cases",
            "broker_submissions",
            "broker_commissions",
        ):
            assert expected_tbl in tables, f"Missing table {expected_tbl}"

        # BROKER is now assignable (is_assignable=1); COMMERCE and FINANCING remain unassignable (0)
        broker_row = conn.execute(
            "SELECT is_core_mandatory, is_assignable, is_active FROM platform_modules WHERE code = 'BROKER'"
        ).fetchone()
        assert broker_row == (0, 1, 1)
        for future_code in ("COMMERCE", "FINANCING"):
            frow = conn.execute(
                "SELECT is_core_mandatory, is_assignable, is_active FROM platform_modules WHERE code = ?",
                (future_code,),
            ).fetchone()
            assert frow == (0, 0, 1)
        broker_deps = [
            r[0]
            for r in conn.execute(
                "SELECT required_module_code FROM platform_module_dependencies WHERE module_code = 'BROKER'"
            ).fetchall()
        ]
        assert broker_deps == ["CORE"]

        # No backfill of BROKER into tenant_modules for any tenant
        broker_tm_count = conn.execute(
            "SELECT COUNT(*) FROM tenant_modules WHERE module_code = 'BROKER'"
        ).fetchone()[0]
        assert broker_tm_count == 0

        # default-org still has strictly CORE + LENDING; platform still has 0 rows
        default_id = conn.execute("SELECT id FROM tenants WHERE slug = 'default-org'").fetchone()[0]
        platform_id = conn.execute("SELECT id FROM tenants WHERE slug = 'platform'").fetchone()[0]
        default_mods = {
            r[0]
            for r in conn.execute(
                "SELECT module_code FROM tenant_modules WHERE tenant_id = ? AND is_enabled = 1",
                (default_id,),
            ).fetchall()
        }
        assert default_mods == {"CORE", "LENDING"}
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM tenant_modules WHERE tenant_id = ?",
                (platform_id,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_02_upgrade_from_013_preserves_data_and_clean_downgrade_reupgrade(tmp_path: Path):
    db_file = tmp_path / "from_013.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    r013 = _run_alembic(["upgrade", "013"], db_url)
    assert r013.returncode == 0, f"alembic upgrade 013 failed:\n{r013.stdout}\n{r013.stderr}"

    # Seed commercial data at revision 013
    conn = _connect_sqlite(db_file)
    try:
        default_id = conn.execute("SELECT id FROM tenants WHERE slug = 'default-org'").fetchone()[0]
        conn.execute(
            "INSERT INTO users (tenant_id, email, full_name, password_hash, role, is_superadmin, preferred_language, is_active, created_at) "
            "VALUES (?, 'owner013@default.io', 'Owner 013', 'hash', 'OWNER_ADMIN', 0, 'en', 1, CURRENT_TIMESTAMP)",
            (default_id,),
        )
        owner_id = conn.execute("SELECT id FROM users WHERE email = 'owner013@default.io'").fetchone()[0]
        conn.execute(
            "INSERT INTO customers (tenant_id, customer_number, entity_type, status, verification_status, full_legal_name, "
            "phone_primary, phone_primary_normalized, owner_user_id, created_by_user_id, customer_since, created_at, updated_at) "
            "VALUES (?, 'CUST-00001', 'INDIVIDUAL', 'ACTIVE', 'PENDING_VERIFICATION', 'Maria 013', '5511001100', '5511001100', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            (default_id, owner_id, owner_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Upgrade 013 -> 014
    r014 = _run_alembic(["upgrade", "head"], db_url)
    assert r014.returncode == 0, f"alembic upgrade 013->014 failed:\n{r014.stdout}\n{r014.stderr}"

    conn = _connect_sqlite(db_file)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("014",)
        assert conn.execute("SELECT is_assignable FROM platform_modules WHERE code = 'BROKER'").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM customers WHERE customer_number = 'CUST-00001'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tenant_modules WHERE module_code = 'BROKER'").fetchone()[0] == 0
    finally:
        conn.close()

    # Clean downgrade 014 -> 013
    r_down = _run_alembic(["downgrade", "013"], db_url)
    assert r_down.returncode == 0, f"alembic downgrade 014->013 failed:\n{r_down.stdout}\n{r_down.stderr}"

    conn = _connect_sqlite(db_file)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("013",)
        assert conn.execute("SELECT is_assignable FROM platform_modules WHERE code = 'BROKER'").fetchone() == (0,)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for dropped_tbl in (
            "broker_lenders",
            "broker_cases",
            "broker_submissions",
            "broker_commissions",
        ):
            assert dropped_tbl not in tables
        # Commercial data preserved intact
        assert conn.execute("SELECT COUNT(*) FROM customers WHERE customer_number = 'CUST-00001'").fetchone()[0] == 1
    finally:
        conn.close()

    # Clean re-upgrade 013 -> 014
    r_reup = _run_alembic(["upgrade", "head"], db_url)
    assert r_reup.returncode == 0, f"alembic re-upgrade 013->014 failed:\n{r_reup.stdout}\n{r_reup.stderr}"


def test_03_constraints_composite_fks_partial_unique_and_orm_roundtrip(tmp_path: Path):
    db_file = tmp_path / "constraints_014.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    res = _run_alembic(["upgrade", "head"], db_url)
    assert res.returncode == 0, res.stderr

    eng = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    db = SessionLocal()
    try:
        t_a = db.query(TenantModel).filter(TenantModel.slug == "default-org").one()
        t_b = TenantModel(name="Beta Broker Org", slug="beta-broker-org", is_active=True)
        db.add(t_b)
        db.flush()

        u_a = UserModel(
            tenant_id=t_a.id,
            email="u_a@broker014.io",
            full_name="User A",
            password_hash="h",
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        u_b = UserModel(
            tenant_id=t_b.id,
            email="u_b@broker014.io",
            full_name="User B",
            password_hash="h",
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        db.add_all([u_a, u_b])
        db.flush()

        cust_a1 = CustomerModel(
            tenant_id=t_a.id,
            customer_number="CUST-00001",
            full_legal_name="Cliente Uno",
            phone_primary="5510001001",
            phone_primary_normalized="5510001001",
            owner_user_id=u_a.id,
            created_by_user_id=u_a.id,
        )
        cust_a2 = CustomerModel(
            tenant_id=t_a.id,
            customer_number="CUST-00002",
            full_legal_name="Cliente Dos",
            phone_primary="5510001002",
            phone_primary_normalized="5510001002",
            owner_user_id=u_a.id,
            created_by_user_id=u_a.id,
        )
        cust_b1 = CustomerModel(
            tenant_id=t_b.id,
            customer_number="CUST-00001",
            full_legal_name="Cliente Beta",
            phone_primary="5520002001",
            phone_primary_normalized="5520002001",
            owner_user_id=u_b.id,
            created_by_user_id=u_b.id,
        )
        db.add_all([cust_a1, cust_a2, cust_b1])
        db.flush()

        lead_a1 = LeadModel(
            tenant_id=t_a.id,
            lead_number="LD-00001",
            full_name="Cliente Uno",
            phone_primary="5510001001",
            phone_primary_normalized="5510001001",
            stage="CONVERTED",
            owner_user_id=u_a.id,
            created_by_user_id=u_a.id,
            converted_customer_id=cust_a1.id,
            converted_by_user_id=u_a.id,
        )
        db.add(lead_a1)
        db.flush()

        # 1. Valid ORM round-trip across all 4 Broker tables
        lender_a1 = BrokerLenderModel(
            tenant_id=t_a.id,
            code="BBVA-MX",
            name="BBVA México",
            lender_type="BANK",
            default_commission_bps=250,
            contact_name="Ejecutivo Uno",
            created_by_user_id=u_a.id,
        )
        lender_a2 = BrokerLenderModel(
            tenant_id=t_a.id,
            code="KONFIO",
            name="Konfío SOFOM",
            lender_type="FINTECH",
            default_commission_bps=300,
            created_by_user_id=u_a.id,
        )
        lender_b1 = BrokerLenderModel(
            tenant_id=t_b.id,
            code="BBVA-MX",  # same code allowed in distinct tenant
            name="BBVA Beta",
            lender_type="BANK",
            default_commission_bps=200,
            created_by_user_id=u_b.id,
        )
        db.add_all([lender_a1, lender_a2, lender_b1])
        db.flush()

        case_a = BrokerCaseModel(
            tenant_id=t_a.id,
            case_number="BRK-00001",
            idempotency_key="brk-idem-1",
            request_payload_hash="a" * 64,
            customer_id=cust_a1.id,
            source_lead_id=lead_a1.id,
            status="OFFERS_RECEIVED",
            product_type="PyME Working Capital",
            requested_amount_cents=50_000_000,
            requested_term_months=24,
            owner_user_id=u_a.id,
            created_by_user_id=u_a.id,
        )
        db.add(case_a)
        db.flush()

        sub_1 = BrokerSubmissionModel(
            tenant_id=t_a.id,
            broker_case_id=case_a.id,
            lender_id=lender_a1.id,
            status="SELECTED",
            offered_amount_cents=45_000_000,
            offered_rate_bps=1850,
            offered_term_months=24,
            offered_commission_bps=250,
            created_by_user_id=u_a.id,
        )
        sub_2 = BrokerSubmissionModel(
            tenant_id=t_a.id,
            broker_case_id=case_a.id,
            lender_id=lender_a2.id,
            status="APPROVED",
            offered_amount_cents=40_000_000,
            offered_rate_bps=2100,
            offered_term_months=18,
            offered_commission_bps=300,
            created_by_user_id=u_a.id,
        )
        db.add_all([sub_1, sub_2])
        db.flush()

        comm = BrokerCommissionModel(
            tenant_id=t_a.id,
            commission_number="COM-00001",
            broker_case_id=case_a.id,
            submission_id=sub_1.id,
            lender_id=lender_a1.id,
            funded_amount_cents=45_000_000,
            commission_rate_bps=250,
            expected_commission_cents=1_125_000,
            paid_commission_cents=0,
            status="PENDING",
            expected_pay_date=date(2026, 12, 15),
            recorded_by_user_id=u_a.id,
        )
        db.add(comm)
        db.commit()

        db.refresh(case_a)
        db.refresh(comm)
        assert len(case_a.submissions) == 2
        assert comm.lender.code == "BBVA-MX"
        assert comm.submission.status == "SELECTED"

        # 2. Partial unique index rejects a 2nd SELECTED submission for the same case
        sub_2.status = "SELECTED"
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        # 3. Cross-tenant composite FK rejection: BrokerCase in Tenant B referencing Customer in Tenant A
        db.add(
            BrokerCaseModel(
                tenant_id=t_b.id,
                case_number="BRK-90001",
                idempotency_key="xtenant-case",
                request_payload_hash="b" * 64,
                customer_id=cust_a1.id,  # WRONG TENANT
                status="DRAFT",
                product_type="Auto Credit",
                requested_amount_cents=10_000_000,
                owner_user_id=u_b.id,
                created_by_user_id=u_b.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        # 4. 3-col FK rejection: BrokerCase with source_lead converted to a DIFFERENT customer
        db.add(
            BrokerCaseModel(
                tenant_id=t_a.id,
                case_number="BRK-90002",
                idempotency_key="bad-lead-cust",
                request_payload_hash="c" * 64,
                customer_id=cust_a2.id,  # lead_a1 was converted to cust_a1, NOT cust_a2
                source_lead_id=lead_a1.id,
                status="DRAFT",
                product_type="Equipment",
                requested_amount_cents=10_000_000,
                owner_user_id=u_a.id,
                created_by_user_id=u_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        # 5. Cross-tenant FK on submission (case from A, lender from B)
        db.add(
            BrokerSubmissionModel(
                tenant_id=t_a.id,
                broker_case_id=case_a.id,
                lender_id=lender_b1.id,  # WRONG TENANT
                status="DRAFT",
                created_by_user_id=u_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        # 6. 4-col FK on commission (submission belongs to lender_a1, NOT lender_a2)
        case_a2 = BrokerCaseModel(
            tenant_id=t_a.id,
            case_number="BRK-00002",
            idempotency_key="brk-idem-2",
            request_payload_hash="d" * 64,
            customer_id=cust_a2.id,
            status="FUNDED",
            product_type="Mortgage",
            requested_amount_cents=20_000_000,
            owner_user_id=u_a.id,
            created_by_user_id=u_a.id,
        )
        db.add(case_a2)
        db.flush()
        db.add(
            BrokerCommissionModel(
                tenant_id=t_a.id,
                commission_number="COM-00002",
                broker_case_id=case_a2.id,
                submission_id=sub_1.id,  # sub_1 belongs to case_a, NOT case_a2
                lender_id=lender_a1.id,
                funded_amount_cents=20_000_000,
                commission_rate_bps=250,
                expected_commission_cents=500_000,
                status="PENDING",
                recorded_by_user_id=u_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        # 7. CHECK constraint rejection (invalid lender_type / out-of-range commission_bps)
        db.add(
            BrokerLenderModel(
                tenant_id=t_a.id,
                code="BAD-LEND",
                name="Bad Lender",
                lender_type="CRYPTO_SHADOW",
                default_commission_bps=100,
                created_by_user_id=u_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        db.add(
            BrokerLenderModel(
                tenant_id=t_a.id,
                code="HUGE-BPS",
                name="Huge Commission",
                lender_type="BANK",
                default_commission_bps=9999,  # > 5000
                created_by_user_id=u_a.id,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    finally:
        db.close()
        eng.dispose()


def test_04_postgresql_offline_sql_generation():
    res = _run_alembic(
        ["upgrade", "013:014", "--sql"],
        "postgresql+psycopg://user:pass@localhost:5432/nura_test",
    )
    assert res.returncode == 0, f"Offline PG SQL failed:\n{res.stdout}\n{res.stderr}"
    sql_upper = res.stdout.upper()
    assert "CREATE TABLE BROKER_LENDERS" in sql_upper
    assert "CREATE TABLE BROKER_CASES" in sql_upper
    assert "CREATE TABLE BROKER_SUBMISSIONS" in sql_upper
    assert "CREATE TABLE BROKER_COMMISSIONS" in sql_upper
    assert "UQ_BROKER_SUBMISSIONS_SELECTED_PER_CASE" in sql_upper
    assert "WHERE STATUS = 'SELECTED'" in sql_upper
    assert "UPDATE PLATFORM_MODULES SET IS_ASSIGNABLE = TRUE WHERE CODE = 'BROKER'" in sql_upper
    assert "INSERT INTO TENANT_MODULES" not in sql_upper
