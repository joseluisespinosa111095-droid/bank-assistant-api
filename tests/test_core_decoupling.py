"""Automated Core vs Lending decoupling tests for Entregable 5A (Bloque 4).

Covers:
1. AST purity check confirming 0 Lending imports in routers/dashboard.py, routers/customers.py,
   routers/leads.py, and services/customer_detail.py.
2. GET /api/dashboard/summary returns strictly Core metrics (customers, leads, call_list, recent_activity)
   with 200 OK for both a CORE-only tenant and default-org (CORE + LENDING), with zero Lending keys.
3. GET /api/loans/dashboard-summary returns exact portfolio KPIs and weekly collections for default-org
   (CORE + LENDING) and 403 Forbidden + MODULE_ACCESS_DENIED audit for a CORE-only tenant.
4. GET /api/customers, GET/PATCH /api/customers/{id}, and POST /api/leads/{id}/convert return strictly
   Core customer profiles without depending on Lending tables, while GET /api/loans/customers/{id}/summary
   serves Lending credit_summary + loans for LENDING tenants and returns 403 + MODULE_ACCESS_DENIED for CORE-only.
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
    tmpl_dir = tmp_path_factory.mktemp("e5a_b4_template")
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
def b4_env(migrated_template_db: Path, tmp_path: Path):
    db_file = tmp_path / "b4_case.db"
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


def _seed_actors(SessionLocal) -> dict:
    db = SessionLocal()
    try:
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
        core_t = TenantModel(
            name="Core Broker Co",
            slug="core-broker-co",
            legal_name="Core Broker SAPI de CV",
            is_active=True,
        )
        db.add(core_t)
        db.flush()

        for et in ("customers", "leads"):
            db.add(TenantSequenceModel(tenant_id=core_t.id, entity_type=et, last_value=0))
        db.add(TenantModuleModel(tenant_id=core_t.id, module_code="CORE", is_enabled=True))

        pw = "Password123!"
        pw_hash = main.pwd_context.hash(pw)

        default_owner = UserModel(
            tenant_id=default_t.id,
            email="owner@default.nura.io",
            full_name="Default Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        core_owner = UserModel(
            tenant_id=core_t.id,
            email="owner@corebroker.nura.io",
            full_name="Core Broker Owner",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        db.add_all([default_owner, core_owner])
        db.commit()
        return {
            "default_tenant_id": default_t.id,
            "core_tenant_id": core_t.id,
            "default_owner_id": default_owner.id,
            "core_owner_id": core_owner.id,
            "password": pw,
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


def test_01_ast_zero_lending_imports_in_core_modules():
    core_files = [
        REPO_ROOT / "routers" / "dashboard.py",
        REPO_ROOT / "routers" / "customers.py",
        REPO_ROOT / "routers" / "leads.py",
        REPO_ROOT / "services" / "customer_detail.py",
        REPO_ROOT / "services" / "serializers_core.py",
    ]
    forbidden_symbols = {
        "LoanModel",
        "LoanInstallmentModel",
        "LoanPaymentModel",
        "LoanPaymentAllocationModel",
        "LoanFinancialEngine",
        "LoanPaymentEngine",
        "serialize_loan_summary",
        "serialize_loan_detail",
        "serialize_loan_payment",
        "get_customers_loan_stats",
        "get_customer_active_loan_summary",
    }
    forbidden_modules = {
        "services.financial_engine",
        "services.serializers_lending",
        "schemas.lending",
        "routers.loans",
    }

    for path in core_files:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in forbidden_modules, f"Forbidden import {alias.name} in {path}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert mod not in forbidden_modules, f"Forbidden import from {mod} in {path}"
                for alias in node.names:
                    assert alias.name not in forbidden_symbols, f"Forbidden symbol {alias.name} imported in {path}"


def test_02_core_dashboard_summary_and_lending_dashboard_summary_decoupling(b4_env):
    ids = _seed_actors(b4_env["SessionLocal"])
    c_def, hdr_def = _login_client("owner@default.nura.io", ids["password"])
    c_core, hdr_core = _login_client("owner@corebroker.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # 1. Seed CORE data in CORE-only tenant (customer + lead + interaction)
        r_core_cust = c_core.post(
            "/api/customers",
            headers=hdr_core,
            json={"entity_type": "INDIVIDUAL", "first_name": "Elena", "last_name": "Core", "phone_primary": "5510009001"},
        )
        assert r_core_cust.status_code == 200
        core_cust_id = r_core_cust.json()["id"]

        r_core_lead = c_core.post(
            "/api/leads",
            headers=hdr_core,
            json={
                "full_name": "Prospect Core",
                "phone_primary": "5510009002",
                "product_interest": "Personal Loan",
                "requested_amount_pesos": 15000.0,
            },
        )
        assert r_core_lead.status_code == 200
        assert (
            c_core.post(
                f"/api/customers/{core_cust_id}/interactions",
                headers=hdr_core,
                json={"interaction_type": "NOTE", "content": "Core onboarding call completed"},
            ).status_code
            == 200
        )

        # GET /api/dashboard/summary succeeds with strictly Core fields (NO Lending keys)
        r_dash_core = c_core.get("/api/dashboard/summary")
        assert r_dash_core.status_code == 200, r_dash_core.text
        dash_core = r_dash_core.json()
        assert dash_core["counts"]["customers"] == 1
        assert dash_core["counts"]["active_customers"] == 1
        assert dash_core["counts"]["open_leads"] == 1
        assert dash_core["call_list"]["open_total"] == 1
        assert dash_core["call_list"]["today"] == 1
        assert len(dash_core["recent_activity"]) == 1
        for lending_key in (
            "active_loan_portfolio_cents",
            "due_today_cents",
            "overdue_amount_cents",
            "total_outstanding_principal_cents",
            "total_amount_due_cents",
            "collected_this_week_cents",
        ):
            assert lending_key not in dash_core
        assert "active_loans" not in dash_core["counts"]

        # GET /api/loans/dashboard-summary returns 403 + MODULE_ACCESS_DENIED for CORE-only tenant
        r_ldash_core = c_core.get("/api/loans/dashboard-summary")
        assert r_ldash_core.status_code == 403

        # 2. Seed customer + active loan + payment in default-org (CORE + LENDING)
        r_def_cust = c_def.post(
            "/api/customers",
            headers=hdr_def,
            json={"entity_type": "INDIVIDUAL", "first_name": "Marco", "last_name": "Lend", "phone_primary": "5520008001"},
        )
        assert r_def_cust.status_code == 200
        def_cust_id = r_def_cust.json()["id"]

        r_loan = c_def.post(
            "/api/loans",
            headers=hdr_def,
            json={
                "idempotency_key": "b4-loan-0001",
                "customer_id": def_cust_id,
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
        assert c_def.post(f"/api/loans/{loan_id}/approve", headers=hdr_def, json={}).status_code == 200
        assert (
            c_def.post(
                f"/api/loans/{loan_id}/activate",
                headers=hdr_def,
                json={"confirm_disbursement": True, "disbursement_method": "SPEI_TRANSFER", "disbursement_reference": "B4-DISB-1"},
            ).status_code
            == 200
        )
        assert (
            c_def.post(
                f"/api/loans/{loan_id}/payments",
                headers=hdr_def,
                json={
                    "idempotency_key": "b4-pay-0001",
                    "amount_pesos": 1000.0,
                    "payment_method": "SPEI_TRANSFER",
                    "payment_reference": "B4-PAY-1",
                },
            ).status_code
            == 200
        )

        # Core dashboard for default-org also returns strictly Core metrics
        r_dash_def = c_def.get("/api/dashboard/summary")
        assert r_dash_def.status_code == 200
        assert r_dash_def.json()["counts"]["customers"] == 1
        assert "active_loan_portfolio_cents" not in r_dash_def.json()

        # Lending dashboard summary for default-org returns exact portfolio calculations
        r_ldash_def = c_def.get("/api/loans/dashboard-summary")
        assert r_ldash_def.status_code == 200, r_ldash_def.text
        ld = r_ldash_def.json()
        # 10,000 principal + 2,000 interest = 12,000 total; 4 weekly installments of 3,000 (2,500 P + 500 I).
        # 1,000 payment on inst #1 pays 500 interest + 500 principal -> unpaid principal = 9,500 (950,000 cents),
        # remaining contract total = 11,000 (1,100,000 cents), due today remaining on inst #1 = 2,000 (200,000 cents).
        assert ld["active_loan_portfolio_cents"] == 950_000
        assert ld["total_outstanding_principal_cents"] == 950_000
        assert ld["total_amount_due_cents"] == 1_100_000
        assert ld["due_today_cents"] == 200_000
        assert ld["overdue_amount_cents"] == 0
        assert ld["collected_this_week_enabled"] is True
        assert ld["collected_this_week_cents"] == 100_000
        assert ld["counts"] == {
            "active_loans": 1,
            "current_loans": 1,
            "overdue_loans": 0,
            "paid_off_loans": 0,
            "defaulted_loans": 0,
            "due_today_installments": 1,
            "overdue_installments": 0,
            "collected_week_payments": 1,
        }

        # Verify MODULE_ACCESS_DENIED audit was recorded for CORE-only tenant
        db = b4_env["SessionLocal"]()
        try:
            audit = (
                db.query(AuditLogModel)
                .filter(
                    AuditLogModel.event_type == "MODULE_ACCESS_DENIED",
                    AuditLogModel.tenant_id == ids["core_tenant_id"],
                )
                .first()
            )
            assert audit is not None
            assert audit.scope == "TENANT"
            d = json.loads(audit.details_json)
            assert d["module_code"] == "LENDING"
            assert d["path"] == "/api/loans/dashboard-summary"
        finally:
            db.close()
    finally:
        c_def.close()
        c_core.close()


def test_03_customer_endpoints_are_core_only_and_lending_customer_summary_is_gated(b4_env):
    ids = _seed_actors(b4_env["SessionLocal"])
    c_def, hdr_def = _login_client("owner@default.nura.io", ids["password"])
    c_core, hdr_core = _login_client("owner@corebroker.nura.io", ids["password"])
    _, _, _, today_local = main.get_local_day_bounds_utc()
    today_iso = today_local.isoformat()

    try:
        # CORE-only tenant: create lead, convert lead, list customers, get & patch customer -> all strictly Core
        r_lead = c_core.post(
            "/api/leads",
            headers=hdr_core,
            json={
                "full_name": "Valeria Broker",
                "phone_primary": "5533004400",
                "product_interest": "Personal Loan",
                "requested_amount_pesos": 25000.0,
                "stage": "QUALIFIED",
            },
        )
        assert r_lead.status_code == 200
        r_conv = c_core.post(f"/api/leads/{r_lead.json()['id']}/convert", headers=hdr_core, json={})
        assert r_conv.status_code == 200, r_conv.text
        core_cust = r_conv.json()["customer"]
        core_cust_id = core_cust["id"]

        for forbidden_lending_field in (
            "credit_summary",
            "loans",
            "active_loans_count",
            "current_outstanding_balance_cents",
            "current_outstanding_principal_cents",
            "last_payment_date",
        ):
            assert forbidden_lending_field not in core_cust

        r_list = c_core.get("/api/customers")
        assert r_list.status_code == 200
        assert r_list.json()["total"] == 1
        assert "active_loans_count" not in r_list.json()["items"][0]

        r_det = c_core.get(f"/api/customers/{core_cust_id}")
        assert r_det.status_code == 200
        assert "credit_summary" not in r_det.json()
        assert "loans" not in r_det.json()

        r_patch = c_core.patch(
            f"/api/customers/{core_cust_id}",
            headers=hdr_core,
            json={"email": "valeria@broker.mx"},
        )
        assert r_patch.status_code == 200
        assert r_patch.json()["email"] == "valeria@broker.mx"
        assert "active_loans_count" not in r_patch.json()

        # CORE-only tenant blocked 403 on Lending customer summary
        assert c_core.get(f"/api/loans/customers/{core_cust_id}/summary").status_code == 403

        # LENDING-enabled tenant (default-org): customer detail is Core, while /api/loans/customers/{id}/summary serves Lending
        r_def_c = c_def.post(
            "/api/customers",
            headers=hdr_def,
            json={"entity_type": "INDIVIDUAL", "first_name": "Diego", "last_name": "Credito", "phone_primary": "5544005500"},
        )
        assert r_def_c.status_code == 200
        def_cust_id = r_def_c.json()["id"]
        assert "credit_summary" not in c_def.get(f"/api/customers/{def_cust_id}").json()

        r_loan = c_def.post(
            "/api/loans",
            headers=hdr_def,
            json={
                "idempotency_key": "b4-cust-sum-loan-1",
                "customer_id": def_cust_id,
                "product_name": "Personal Loan",
                "principal_pesos": 8000.0,
                "monthly_interest_rate_percent": 10.0,
                "assumed_interest_months": 1,
                "term_installments_count": 4,
                "start_date": today_iso,
                "first_payment_date": today_iso,
            },
        )
        assert r_loan.status_code == 200
        loan_id = r_loan.json()["loan"]["id"]
        assert c_def.post(f"/api/loans/{loan_id}/approve", headers=hdr_def, json={}).status_code == 200
        assert (
            c_def.post(
                f"/api/loans/{loan_id}/activate",
                headers=hdr_def,
                json={"confirm_disbursement": True, "disbursement_method": "SPEI_TRANSFER", "disbursement_reference": "B4-CS-1"},
            ).status_code
            == 200
        )

        r_sum = c_def.get(f"/api/loans/customers/{def_cust_id}/summary")
        assert r_sum.status_code == 200, r_sum.text
        summary = r_sum.json()
        assert summary["customer_id"] == def_cust_id
        assert summary["credit_summary"]["active_loans_count"] == 1
        assert summary["credit_summary"]["total_loans_count"] == 1
        assert summary["credit_summary"]["current_outstanding_principal_cents"] == 800_000
        assert summary["credit_summary"]["total_amount_due_cents"] == 880_000
        assert summary["credit_summary"]["remaining_installments"] == 4
        assert len(summary["loans"]) == 1
        assert summary["loans"][0]["id"] == loan_id

        # Non-existent or cross-tenant customer returns 404
        assert c_def.get(f"/api/loans/customers/{core_cust_id}/summary").status_code == 404
    finally:
        c_def.close()
        c_core.close()


def test_04_frontend_responsive_lending_visibility_for_core_only_and_lending():
    """Regression for 5A audit finding D1:
    Verify #top-btn-simulator (hidden md:flex) and #cd-qs-balance-sep (hidden sm:inline)
    stay strictly hidden (display: none) on desktop when LENDING is NOT enabled, and remain
    visible on desktop when LENDING IS enabled.
    """
    import base64
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    import socket
    import struct
    import tempfile
    import threading
    import time
    import urllib.request

    app_css = (REPO_ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    index_html = (REPO_ROOT / "index.html").read_text(encoding="utf-8")
    plat_js = (REPO_ROOT / "static" / "js" / "04-platform.js").read_text(encoding="utf-8")
    dash_js = (REPO_ROOT / "static" / "js" / "05-dashboard-customers.js").read_text(encoding="utf-8")

    # 1. Static structural assertions (always run even without browser)
    assert ".module-hidden { display: none !important; }" in app_css
    assert 'id="top-btn-simulator" data-responsive-display="true"' in index_html
    assert 'id="cd-qs-balance-sep" data-responsive-display="true"' in index_html
    assert "el.classList.toggle('module-hidden', !hasLending)" in plat_js
    assert "document.getElementById('cd-qs-balance-sep')?.classList.toggle('module-hidden', !hasLending)" in dash_js

    # 2. Real headless Chrome computed-style check at desktop 1280x800 (offline deterministic CSS rules)
    chrome_bin = next(
        (
            cand
            for cand in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                "/usr/bin/google-chrome",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser",
            )
            if os.path.exists(cand)
        ),
        None,
    )
    if not chrome_bin:
        pytest.skip("Headless Chrome/Chromium binary not available in this environment.")

    class _QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(REPO_ROOT)))
    http_port = server.server_address[1]
    srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
    srv_thread.start()

    user_dir = tempfile.mkdtemp(prefix="nura_e5a_css_test_")
    sock_tmp = socket.socket()
    sock_tmp.bind(("127.0.0.1", 0))
    cdp_port = sock_tmp.getsockname()[1]
    sock_tmp.close()

    proc = subprocess.Popen(
        [
            chrome_bin,
            "--headless=new",
            "--disable-gpu",
            f"--remote-debugging-port={cdp_port}",
            f"--user-data-dir={user_dir}",
            "--window-size=1280,800",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        ws_url = None
        for _ in range(40):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2).read().decode())
                for t in tabs:
                    if t.get("type") == "page":
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                time.sleep(0.15)
        assert ws_url, "Could not connect to headless Chrome CDP."

        host_port, ws_path = ws_url.replace("ws://", "").split("/", 1)
        ws_host, ws_port = host_port.split(":")
        ws_sock = socket.create_connection((ws_host, int(ws_port)), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        ws_sock.sendall(
            (
                f"GET /{ws_path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += ws_sock.recv(4096)
        assert b"101" in buf.splitlines()[0]
        rem = [buf.split(b"\r\n\r\n", 1)[1]]
        cid = [0]

        def _recv_exact(n: int) -> bytes:
            while len(rem[0]) < n:
                chunk = ws_sock.recv(65536)
                if not chunk:
                    raise EOFError("CDP socket closed")
                rem[0] += chunk
            out, rem[0] = rem[0][:n], rem[0][n:]
            return out

        def _cdp(method: str, params: dict | None = None) -> dict:
            cid[0] += 1
            data = json.dumps({"id": cid[0], "method": method, "params": params or {}}).encode("utf-8")
            mask = os.urandom(4)
            hdr = bytearray([0x81])
            if len(data) < 126:
                hdr.append(0x80 | len(data))
            elif len(data) < 65536:
                hdr.append(0x80 | 126)
                hdr.extend(struct.pack("!H", len(data)))
            else:
                hdr.append(0x80 | 127)
                hdr.extend(struct.pack("!Q", len(data)))
            hdr.extend(mask)
            ws_sock.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
            while True:
                b1, b2 = _recv_exact(2)
                opcode = b1 & 0x0F
                length = b2 & 0x7F
                if length == 126:
                    length = struct.unpack("!H", _recv_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", _recv_exact(8))[0]
                payload = _recv_exact(length)
                if opcode == 1:
                    msg = json.loads(payload.decode("utf-8"))
                    if msg.get("id") == cid[0]:
                        return msg.get("result", {})

        _cdp("Page.enable")
        _cdp("Runtime.enable")
        _cdp("Page.navigate", {"url": f"http://127.0.0.1:{http_port}/index.html"})
        for _ in range(40):
            ready = _cdp("Runtime.evaluate", {"expression": "typeof applyShellMode === 'function'", "returnByValue": True})
            if ready.get("result", {}).get("value") is True:
                break
            time.sleep(0.15)

        # Inject deterministic Tailwind responsive utility rules (exact Tailwind cascade: .hidden before @media md:flex / sm:inline)
        # so the test verifies the real cascade even in offline environments without Tailwind CDN.
        eval_expr = """
        (() => {
            const style = document.createElement('style');
            style.textContent = `
                .hidden { display: none; }
                .flex { display: flex; }
                .grid { display: grid; }
                @media (min-width: 640px) { .sm\\\\:inline { display: inline; } }
                @media (min-width: 768px) { .md\\\\:flex { display: flex; } }
            `;
            document.head.insertBefore(style, document.head.firstChild);

            document.getElementById('auth-view')?.classList.add('hidden');
            document.getElementById('app-view')?.classList.remove('hidden');
            document.getElementById('tab-customers')?.classList.add('active');
            document.getElementById('customers-master-view')?.classList.add('hidden');
            document.getElementById('customer-detail-view')?.classList.remove('hidden');

            const probe = (ids) => {
                const res = {};
                for (const id of ids) {
                    const el = document.getElementById(id);
                    const cs = getComputedStyle(el);
                    res[id] = { display: cs.display, classes: el.className };
                }
                return res;
            };
            const targetIds = [
                'top-btn-simulator',
                'cd-qs-balance-sep',
                'cd-qs-balance-wrap',
                'side-btn-loans',
                'side-btn-simulator',
                'dash-btn-simulator',
                'privacy-btn',
                'dash-lending-kpis-grid',
                'dash-lending-distribution-card',
                'ctab-btn-summary',
                'ctab-btn-loans'
            ];

            // Case 1: CORE-only tenant -> ALL lending UI must compute display: none on desktop
            applyShellMode(false, { role: 'OWNER_ADMIN', tenant_name: 'Core Org', tenant_slug: 'core-org', enabled_modules: ['CORE'] });
            const coreOnly = probe(targetIds);

            // Case 2: CORE + LENDING tenant -> top-btn-simulator & cd-qs-balance-sep visible on desktop, keeping responsive hidden class
            applyShellMode(false, { role: 'OWNER_ADMIN', tenant_name: 'Lend Org', tenant_slug: 'lend-org', enabled_modules: ['CORE', 'LENDING'] });
            const coreAndLending = probe(targetIds);

            return { coreOnly, coreAndLending };
        })()
        """
        out = _cdp("Runtime.evaluate", {"expression": eval_expr, "returnByValue": True})["result"]["value"]
        ws_sock.close()

        for elem_id, state in out["coreOnly"].items():
            assert state["display"] == "none", f"CORE-only leak on #{elem_id}: {state}"

        assert out["coreAndLending"]["top-btn-simulator"]["display"] == "flex"
        assert "hidden" in out["coreAndLending"]["top-btn-simulator"]["classes"].split()
        assert "module-hidden" not in out["coreAndLending"]["top-btn-simulator"]["classes"].split()
        assert out["coreAndLending"]["cd-qs-balance-sep"]["display"] != "none"
        assert "hidden" in out["coreAndLending"]["cd-qs-balance-sep"]["classes"].split()
        assert "module-hidden" not in out["coreAndLending"]["cd-qs-balance-sep"]["classes"].split()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
        server.shutdown()
        server.server_close()
        shutil.rmtree(user_dir, ignore_errors=True)
