"""Automated tests for Bloque J (Login Rate Limiting, Collections Queue & Default Transition).

Adapted to isolated Alembic 013 multitenant SQLite databases so pytest runs cleanly
without relying on external local DB state or hardcoded passwords.
"""
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
from models import TenantModel, UserModel


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
def migrated_bloque_j_template_db(tmp_path_factory) -> Path:
    tmpl_dir = tmp_path_factory.mktemp("bloque_j_template")
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
def bloque_j_env(migrated_bloque_j_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "bloque_j_case.db"
    shutil.copy2(migrated_bloque_j_template_db, db_file)

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


def _seed_owner_admin(SessionLocal) -> tuple[str, str]:
    db = SessionLocal()
    try:
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        email = "owner.bloquej@default.nura.io"
        pw = "Password123!"
        db.add(
            UserModel(
                tenant_id=default_t.id,
                email=email,
                full_name="Bloque J Owner",
                password_hash=main.pwd_context.hash(pw),
                role="OWNER_ADMIN",
                is_superadmin=False,
                is_active=True,
            )
        )
        db.commit()
        return email, pw
    finally:
        db.close()


def test_login_rate_limiting_ip_and_email(bloque_j_env):
    """Valida que después de 5 intentos fallidos consecutivos se devuelva HTTP 429."""
    client = TestClient(main.app, base_url="http://localhost")
    try:
        email_test = "brute_force_check@test.com"
        for _ in range(5):
            res = client.post("/api/auth/login", json={"email": email_test, "password": "wrongpassword"})
            assert res.status_code == 401

        res_limited = client.post("/api/auth/login", json={"email": email_test, "password": "wrongpassword"})
        assert res_limited.status_code == 429
        assert "Retry-After" in res_limited.headers
    finally:
        client.close()


def test_collections_queue_and_default_transition(bloque_j_env):
    """Valida el ciclo de cobranza, transición a DEFAULTED y bloqueo de reversiones."""
    email, pw = _seed_owner_admin(bloque_j_env["SessionLocal"])
    client = TestClient(main.app, base_url="http://localhost")
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        login_res = client.post("/api/auth/login", json={"email": email, "password": pw})
        assert login_res.status_code == 200, login_res.text
        csrf_token = client.cookies.get("csrf_token")
        assert csrf_token
        headers = {"Origin": "http://localhost", "X-CSRF-Token": csrf_token}

        # 1. Crear cliente y préstamo ACTIVE con 1ra cuota venciendo hoy (aparece en cola de cobranza)
        r_cust = client.post(
            "/api/customers",
            headers=headers,
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Roberto",
                "last_name": "Cobranza",
                "phone_primary": "5519001900",
            },
        )
        assert r_cust.status_code == 200, r_cust.text

        r_loan = client.post(
            "/api/loans",
            headers=headers,
            json={
                "idempotency_key": "bloque-j-loan-001",
                "customer_id": r_cust.json()["id"],
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

        assert client.post(f"/api/loans/{loan_id}/approve", headers=headers, json={}).status_code == 200
        assert (
            client.post(
                f"/api/loans/{loan_id}/activate",
                headers=headers,
                json={
                    "confirm_disbursement": True,
                    "disbursement_method": "SPEI_TRANSFER",
                    "disbursement_reference": "BJ-DISB-001",
                },
            ).status_code
            == 200
        )

        # Registrar un pago parcial para luego verificar que tras DEFAULTED se bloquea la reversión
        r_pay = client.post(
            f"/api/loans/{loan_id}/payments",
            headers=headers,
            json={
                "idempotency_key": "bloque-j-pay-001",
                "amount_pesos": 500.0,
                "payment_method": "CASH_RECEIPT",
            },
        )
        assert r_pay.status_code == 200, r_pay.text
        pay_id = r_pay.json()["payment"]["id"]

        # 2. Registrar llamada de cobranza
        col_res = client.post(
            f"/api/loans/{loan_id}/collections-log",
            headers=headers,
            json={
                "interaction_type": "CALL",
                "outcome": "INTERESTED",
                "promised_amount_pesos": 1500.0,
                "notes": "Acuerdo de pago para el próximo viernes en sucursal.",
            },
        )
        assert col_res.status_code == 200, col_res.text

        # 3. Consultar la cola de cobranza
        q_res = client.get("/api/collections/queue?bucket=ALL")
        assert q_res.status_code == 200, q_res.text
        data = q_res.json()
        assert data["counts"]["total_actionable"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == loan_id

        # 4. Transicionar préstamo a DEFAULTED y verificar bloqueo de reversión de pagos (409)
        def_res = client.post(
            f"/api/loans/{loan_id}/default",
            headers=headers,
            json={
                "confirm_default": True,
                "default_reason": "Incumplimiento reiterado confirmado por cobranza",
            },
        )
        assert def_res.status_code == 200, def_res.text
        assert def_res.json()["status"] == "DEFAULTED"

        rev_res = client.post(
            f"/api/loans/{loan_id}/payments/{pay_id}/reverse",
            headers=headers,
            json={"reversal_reason": "Intento de reversa sobre cartera vencida"},
        )
        assert rev_res.status_code == 409
    finally:
        client.close()
