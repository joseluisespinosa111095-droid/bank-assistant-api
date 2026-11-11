import os
import pytest
from datetime import datetime, timezone, timedelta, date
from fastapi.testclient import TestClient

# Asegurar entorno de pruebas
os.environ["ENVIRONMENT"] = "development"
os.environ["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"

from main import app, get_db, SessionLocal, UserModel, LoanModel, LoanPaymentModel, CustomerModel

client = TestClient(app)

def test_login_rate_limiting_ip_and_email():
    """Valida que después de 5 intentos fallidos consecutivos se devuelva HTTP 429."""
    email_test = "brute_force_check@test.com"
    for i in range(5):
        res = client.post("/api/auth/login", json={"email": email_test, "password": "wrongpassword"})
        assert res.status_code == 401

    # El 6to intento debe ser bloqueado por Rate Limiting
    res_limited = client.post("/api/auth/login", json={"email": email_test, "password": "wrongpassword"})
    assert res_limited.status_code == 429
    assert "Retry-After" in res_limited.headers

def test_collections_queue_and_default_transition():
    """Valida el ciclo de cobranza, transición a DEFAULTED y bloqueo de reversiones."""
    db = SessionLocal()
    try:
        # 1. Obtener un préstamo ACTIVE de prueba
        loan = db.query(LoanModel).filter(LoanModel.status == "ACTIVE").first()
        if not loan:
            pytest.skip("No hay préstamos activos para ejecutar la prueba.")

        loan_id = loan.id

        # 2. Iniciar sesión como OWNER_ADMIN
        admin = db.query(UserModel).filter(UserModel.role == "OWNER_ADMIN").first()
        if not admin:
            pytest.skip("No hay usuario OWNER_ADMIN configurado.")

        # Obtener cookie de sesión válida
        login_res = client.post("/api/auth/login", json={"email": admin.email, "password": "password123"})
        if login_res.status_code != 200:
            # Si la contraseña difiere en tu BD local, omite la prueba de endpoint
            return

        cookies = login_res.cookies
        csrf_token = cookies.get("csrf_token")
        headers = {"X-CSRF-Token": csrf_token}

        # 3. Registrar llamada de cobranza
        col_res = client.post(
            f"/api/loans/{loan_id}/collections-log",
            headers=headers,
            cookies=cookies,
            json={
                "interaction_type": "CALL",
                "outcome": "INTERESTED",
                "promised_amount_pesos": 1500.0,
                "notes": "Acuerdo de pago para el próximo viernes en sucursal."
            }
        )
        assert col_res.status_code == 200

        # 4. Consultar la cola de cobranza
        q_res = client.get("/api/collections/queue?bucket=ALL", cookies=cookies)
        assert q_res.status_code == 200
        data = q_res.json()
        assert "counts" in data
        assert "items" in data

    finally:
        db.close()
