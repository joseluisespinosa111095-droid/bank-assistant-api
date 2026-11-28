"""Comprehensive tests for the 5 production enhancements (e5c):

1. Migration 015 (`015_leads_curp_name_and_followup_type.py`):
   - Clean upgrade to `head` (`015`), upgrade `014 -> 015` preserving existing data,
     clean downgrade `015 -> 014`, re-upgrade `014 -> 015`, and offline PostgreSQL SQL DDL check.
2. Gemini SDK migration to `google-genai` inside `services/external/`:
   - AST verification: zero `google.generativeai` imports in `main.py` and `services/external/`.
   - Unit test verifying `AIService.generate_reply` invokes `genai.Client().models.generate_content`
     with `system_instruction` and handles missing API key cleanly.
3. New Lead Split Name & Mandatory CURP (`INDIVIDUAL` vs `BUSINESS`) + Conversion carryover:
   - `INDIVIDUAL` with `first_name`, `middle_name`, `last_name` + valid 18-char `curp` (and NO RFC) -> 200.
   - `INDIVIDUAL` with split-name fields and missing or malformed `curp` -> 422.
   - `BUSINESS` with `full_name` and NO `curp` -> 200.
   - Lead conversion carries `first_name`, `middle_name`, `last_name`, and `curp` to `CustomerModel`.
4. Visual Calendar API (`GET /api/leads/calendar`):
   - Distinguishes `CALL` (Phone Call) vs `MEETING` (In-Person Appointment) with person names and times.
   - Enforces strict multi-tenant isolation.
5. Headless Chrome E2E verification:
   - Complete Spanish (`es`) translation across Dashboard (Core + Broker KPIs, Overdue Calls,
     Upcoming 7d, Recent CRM Activity, Recent Broker Cases), Sidebar (`Broker de Crédito`),
     and Profile/Language modal (`Idioma y Preferencias Regionales`).
   - Visual monthly calendar rendering person names inside day cells with distinct `CALL` vs `MEETING` visuals.
   - New Lead modal showing Split Name + mandatory CURP for `INDIVIDUAL` and hiding them for `BUSINESS`.
"""
import ast
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import MagicMock, patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("RATE_LIMIT_HMAC_SECRET", "test-secret-key-at-least-32-chars-long-abc123")

from tests.test_broker_frontend import _run_cdp_eval


def _make_alembic_config(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_01_migration_015_upgrade_downgrade_and_pg_offline_sql(tmp_path):
    db_file = tmp_path / "mig015.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    env_backup = os.environ.get("DATABASE_URL")
    mig_backup = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    os.environ["MIGRATION_DATABASE_URL"] = db_url
    try:
        cfg = _make_alembic_config(db_url)
        # Upgrade to 014 first and seed a lead to confirm non-destructive upgrade
        command.upgrade(cfg, "014")
        eng = create_engine(db_url)
        with eng.begin() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, full_name, password_hash, role, is_superadmin, is_active, created_at) "
                    "VALUES (1, 1, 'mig@nura.io', 'Mig User', 'x', 'OWNER_ADMIN', 0, 1, :t)"
                ),
                {"t": now},
            )
            conn.execute(
                text(
                    "INSERT INTO leads (tenant_id, lead_number, entity_type, full_name, phone_primary, "
                    "phone_primary_normalized, source, product_interest, requested_amount_cents, currency, "
                    "stage, priority, owner_user_id, created_by_user_id, created_at, updated_at) "
                    "VALUES (1, 'LEAD-00001', 'INDIVIDUAL', 'Legacy Lead', '5511001100', '5511001100', "
                    "'OTHER', 'PERSONAL_LOAN', 0, 'MXN', 'NEW', 'MEDIUM', 1, 1, :t, :t)"
                ),
                {"t": now},
            )
        eng.dispose()

        # Upgrade 014 -> head (015)
        command.upgrade(cfg, "head")
        eng = create_engine(db_url)
        insp = inspect(eng)
        lead_cols = {c["name"] for c in insp.get_columns("leads")}
        cust_cols = {c["name"] for c in insp.get_columns("customers")}
        int_cols = {c["name"] for c in insp.get_columns("interactions")}
        assert {"first_name", "middle_name", "last_name", "curp", "next_follow_up_type"}.issubset(lead_cols)
        assert "curp" in cust_cols
        assert "next_follow_up_type" in int_cols

        with eng.connect() as conn:
            row = conn.execute(text("SELECT full_name, curp FROM leads WHERE lead_number='LEAD-00001'")).fetchone()
            assert row is not None and row[0] == "Legacy Lead" and row[1] is None
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert rev == "015"
        eng.dispose()

        # Downgrade 015 -> 014 and verify columns removed & row preserved
        command.downgrade(cfg, "014")
        eng = create_engine(db_url)
        insp = inspect(eng)
        assert "curp" not in {c["name"] for c in insp.get_columns("leads")}
        assert "curp" not in {c["name"] for c in insp.get_columns("customers")}
        assert "next_follow_up_type" not in {c["name"] for c in insp.get_columns("interactions")}
        with eng.connect() as conn:
            assert conn.execute(text("SELECT full_name FROM leads WHERE lead_number='LEAD-00001'")).scalar() == "Legacy Lead"
        eng.dispose()

        # Re-upgrade 014 -> 015
        command.upgrade(cfg, "head")

        # Offline PostgreSQL SQL verification
        env_pg = os.environ.copy()
        env_pg["DATABASE_URL"] = "postgresql://user:pass@localhost:5432/nura_test"
        env_pg["MIGRATION_DATABASE_URL"] = "postgresql://user:pass@localhost:5432/nura_test"
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "014:015", "--sql"],
            cwd=str(REPO_ROOT),
            env=env_pg,
            capture_output=True,
            text=True,
            check=True,
        )
        sql_out = proc.stdout
        assert "ALTER TABLE leads ADD COLUMN first_name" in sql_out
        assert "ALTER TABLE leads ADD COLUMN curp" in sql_out
        assert "ALTER TABLE leads ADD COLUMN next_follow_up_type" in sql_out
        assert "ALTER TABLE customers ADD COLUMN curp" in sql_out
        assert "ALTER TABLE interactions ADD COLUMN next_follow_up_type" in sql_out
        assert "CREATE INDEX ix_leads_curp" in sql_out
        assert "CREATE INDEX ix_customers_curp" in sql_out
    finally:
        if env_backup is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = env_backup
        if mig_backup is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = mig_backup


def test_02_google_genai_service_and_zero_deprecated_imports():
    # 1. Verify old services/external.py is replaced by services/external/ package
    assert not (REPO_ROOT / "services" / "external.py").exists()
    assert (REPO_ROOT / "services" / "external" / "__init__.py").is_file()
    assert (REPO_ROOT / "services" / "external" / "gemini.py").is_file()
    assert (REPO_ROOT / "services" / "external" / "email.py").is_file()

    # 2. AST verification: ZERO references to deprecated google.generativeai
    files_to_check = [
        REPO_ROOT / "main.py",
        REPO_ROOT / "services" / "external" / "__init__.py",
        REPO_ROOT / "services" / "external" / "gemini.py",
        REPO_ROOT / "services" / "external" / "email.py",
    ]
    for path in files_to_check:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "google.generativeai" not in alias.name, f"Deprecated import in {path}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "google.generativeai" not in mod, f"Deprecated import in {path}"

    # 3. Runtime check of AIService with mocked google.genai.Client
    from services.external import AIService, clear_gemini_client_cache
    from services.external import gemini as gemini_mod

    clear_gemini_client_cache()
    with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
        res_missing = AIService.generate_reply("Hello", [], "OWNER_ADMIN")
        assert "not configured" in res_missing

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Respuesta generada por Gemini 2.5"
    mock_client.models.generate_content.return_value = mock_resp

    prior_msg = MagicMock()
    prior_msg.role = "user"
    prior_msg.content = "Hola Nura"

    clear_gemini_client_cache()
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test-fake-key-123"}, clear=False), patch.object(
        gemini_mod.genai, "Client", return_value=mock_client
    ) as client_ctor:
        reply = AIService.generate_reply("Resumen del expediente", [prior_msg], "OWNER_ADMIN")
        assert reply == "Respuesta generada por Gemini 2.5"
        client_ctor.assert_called_once_with(api_key="test-fake-key-123")
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert len(call_kwargs["contents"]) == 2
        assert "OWNER_ADMIN" in call_kwargs["config"].system_instruction
    clear_gemini_client_cache()


def _bootstrap_test_app(tmp_path):
    """Create a fresh SQLite DB upgraded to head (015) and wire main.app overrides."""
    db_file = tmp_path / "e5c_api.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    env_backup = os.environ.get("DATABASE_URL")
    mig_backup = os.environ.get("MIGRATION_DATABASE_URL")
    os.environ["DATABASE_URL"] = db_url
    os.environ["MIGRATION_DATABASE_URL"] = db_url
    try:
        cfg = _make_alembic_config(db_url)
        command.upgrade(cfg, "head")
    finally:
        if env_backup is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = env_backup
        if mig_backup is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = mig_backup

    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    import main as main_mod
    from models import TenantModel, TenantModuleModel, UserModel
    from services.modules import ensure_tenant_sequences_for_modules

    db = SessionLocal()
    try:
        pw_hash = main_mod.pwd_context.hash("StrongPass123!")
        t1 = db.query(TenantModel).filter(TenantModel.slug == "default-org").one()
        u1 = UserModel(
            tenant_id=t1.id,
            full_name="Tenant A Owner",
            email="owner-a@e5c.nura.io",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        t2 = TenantModel(
            name="Tenant B Broker",
            slug="tenant-b-e5c",
            is_active=True,
        )
        db.add(t2)
        db.flush()
        db.add_all(
            [
                TenantModuleModel(tenant_id=t2.id, module_code="CORE", is_enabled=True),
                TenantModuleModel(tenant_id=t2.id, module_code="BROKER", is_enabled=True),
            ]
        )
        ensure_tenant_sequences_for_modules(db, t2.id, ["CORE", "BROKER"])
        u2 = UserModel(
            tenant_id=t2.id,
            full_name="Tenant B Owner",
            email="owner-b@e5c.nura.io",
            password_hash=pw_hash,
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True,
        )
        db.add_all([u1, u2])
        db.commit()
    finally:
        db.close()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    prev_override = main_mod.app.dependency_overrides.get(main_mod.get_db)
    main_mod.app.dependency_overrides[main_mod.get_db] = override_get_db
    return main_mod, engine, prev_override


def _login_client(app, email: str, password: str) -> tuple[TestClient, dict]:
    client = TestClient(app, base_url="http://localhost:8000")
    resp = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://localhost:8000"},
    )
    assert resp.status_code == 200, resp.text
    csrf = client.cookies.get("csrf_token", "")
    return client, {"X-CSRF-Token": csrf, "Origin": "http://localhost:8000"}


def test_03_lead_split_name_mandatory_curp_and_conversion_carryover(tmp_path):
    main_mod, engine, prev_override = _bootstrap_test_app(tmp_path)
    try:
        client, headers = _login_client(main_mod.app, "owner-a@e5c.nura.io", "StrongPass123!")

        # 1. INDIVIDUAL with split name and missing CURP -> 422
        r_missing_curp = client.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Carlos",
                "middle_name": "Eduardo",
                "last_name": "Mendoza Ruiz",
                "phone_primary": "5511223344",
                "source": "REFERRAL",
                "product_interest": "Personal Loan",
            },
            headers=headers,
        )
        assert r_missing_curp.status_code == 422, r_missing_curp.text
        assert "CURP" in r_missing_curp.text

        # 2. INDIVIDUAL with malformed CURP -> 422
        r_bad_curp = client.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Carlos",
                "last_name": "Mendoza Ruiz",
                "curp": "BADCURP123",
                "phone_primary": "5511223344",
                "source": "REFERRAL",
                "product_interest": "Personal Loan",
            },
            headers=headers,
        )
        assert r_bad_curp.status_code == 422, r_bad_curp.text

        # 3. INDIVIDUAL with valid split name + 18-char CURP and NO RFC -> 200
        valid_curp = "gode561231hdfrrl09"
        r_ok = client.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Carlos",
                "middle_name": "Eduardo",
                "last_name": "Mendoza Ruiz",
                "curp": valid_curp,
                "phone_primary": "5511223344",
                "source": "REFERRAL",
                "product_interest": "Personal Loan",
                "next_follow_up_type": "MEETING",
                "next_follow_up_at": datetime.now(timezone.utc).isoformat(),
            },
            headers=headers,
        )
        assert r_ok.status_code == 200, r_ok.text
        lead = r_ok.json()
        assert lead["first_name"] == "Carlos"
        assert lead["middle_name"] == "Eduardo"
        assert lead["last_name"] == "Mendoza Ruiz"
        assert lead["full_name"] == "Carlos Eduardo Mendoza Ruiz"
        assert lead["curp"] == "GODE561231HDFRRL09"
        assert lead["tax_id_rfc"] is None
        assert lead["next_follow_up_type"] == "MEETING"

        # 4. Convert lead -> Customer inherits split name + CURP
        r_conv = client.post(
            f"/api/leads/{lead['id']}/convert",
            json={"notes": "Converted after meeting"},
            headers=headers,
        )
        assert r_conv.status_code == 200, r_conv.text
        cust = r_conv.json()["customer"]
        assert cust["first_name"] == "Carlos"
        assert cust["middle_name"] == "Eduardo"
        assert cust["last_name"] == "Mendoza Ruiz"
        assert cust["full_legal_name"] == "Carlos Eduardo Mendoza Ruiz"
        assert cust["curp"] == "GODE561231HDFRRL09"

        # 5. BUSINESS without CURP -> 200
        r_biz = client.post(
            "/api/leads",
            json={
                "entity_type": "BUSINESS",
                "full_name": "Constructora Norte SA de CV",
                "phone_primary": "5599887766",
                "source": "WEBSITE",
                "product_interest": "Business Loan",
            },
            headers=headers,
        )
        assert r_biz.status_code == 200, r_biz.text
        biz = r_biz.json()
        assert biz["full_name"] == "Constructora Norte SA de CV"
        assert biz["curp"] is None
        assert biz["first_name"] is None
    finally:
        if prev_override is None:
            main_mod.app.dependency_overrides.pop(main_mod.get_db, None)
        else:
            main_mod.app.dependency_overrides[main_mod.get_db] = prev_override
        engine.dispose()


def test_04_leads_calendar_endpoint_calls_vs_meetings_and_tenant_isolation(tmp_path):
    main_mod, engine, prev_override = _bootstrap_test_app(tmp_path)
    try:
        client_a, headers_a = _login_client(main_mod.app, "owner-a@e5c.nura.io", "StrongPass123!")
        client_b, headers_b = _login_client(main_mod.app, "owner-b@e5c.nura.io", "StrongPass123!")

        target_dt_call = datetime(2026, 12, 10, 10, 30, tzinfo=timezone.utc)
        target_dt_meeting = datetime(2026, 12, 15, 16, 0, tzinfo=timezone.utc)
        target_dt_b = datetime(2026, 12, 12, 11, 0, tzinfo=timezone.utc)

        # Seed Tenant A: 1 CALL + 1 MEETING in Dec 2026
        r1 = client_a.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Sofía",
                "last_name": "Ramírez",
                "curp": "RAMS900101MDFRRL01",
                "phone_primary": "5510001001",
                "source": "WEBSITE",
                "product_interest": "Personal Loan",
                "next_follow_up_at": target_dt_call.isoformat(),
                "next_follow_up_type": "CALL",
            },
            headers=headers_a,
        )
        assert r1.status_code == 200, r1.text

        r2 = client_a.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Diego",
                "last_name": "Vargas",
                "curp": "VARD850505HDFRRL02",
                "phone_primary": "5510001002",
                "source": "REFERRAL",
                "product_interest": "Business Loan",
                "next_follow_up_at": target_dt_meeting.isoformat(),
                "next_follow_up_type": "MEETING",
            },
            headers=headers_a,
        )
        assert r2.status_code == 200, r2.text

        # Seed Tenant B: 1 MEETING in Dec 2026 (must NEVER leak into Tenant A)
        r3 = client_b.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Secret",
                "last_name": "TenantB",
                "curp": "SECT880808HDFRRL03",
                "phone_primary": "5520002002",
                "source": "OTHER",
                "product_interest": "Personal Loan",
                "next_follow_up_at": target_dt_b.isoformat(),
                "next_follow_up_type": "MEETING",
            },
            headers=headers_b,
        )
        assert r3.status_code == 200, r3.text

        cal_a = client_a.get("/api/leads/calendar?year=2026&month=12")
        assert cal_a.status_code == 200, cal_a.text
        data_a = cal_a.json()
        assert data_a["year"] == 2026 and data_a["month"] == 12
        assert data_a["counts"] == {"total": 2, "calls": 1, "meetings": 1}
        names_a = {(ev["person_name"], ev["activity_type"], ev["date"]) for ev in data_a["events"]}
        assert ("Sofía Ramírez", "CALL", "2026-12-10") in names_a
        assert ("Diego Vargas", "MEETING", "2026-12-15") in names_a
        assert all("TenantB" not in ev["person_name"] for ev in data_a["events"])

        cal_b = client_b.get("/api/leads/calendar?year=2026&month=12")
        assert cal_b.status_code == 200
        data_b = cal_b.json()
        assert data_b["counts"] == {"total": 1, "calls": 0, "meetings": 1}
        assert data_b["events"][0]["person_name"] == "Secret TenantB"
    finally:
        if prev_override is None:
            main_mod.app.dependency_overrides.pop(main_mod.get_db, None)
        else:
            main_mod.app.dependency_overrides[main_mod.get_db] = prev_override
        engine.dispose()


def test_05_headless_chrome_spanish_i18n_calendar_and_new_lead_curp_ui():
    js_test = """
    (async () => {
        document.getElementById('auth-view')?.classList.add('hidden');
        document.getElementById('app-view')?.classList.remove('hidden');

        // Mock API endpoints for Dashboard, Broker Summary, and Visual Calendar
        window.fetch = async (url) => {
            const u = String(url);
            let body = {};
            if (u.startsWith('/api/dashboard/summary')) {
                body = {
                    counts: { customers: 5, open_leads: 3 },
                    call_list: { today: 2, overdue: 1, upcoming: 4 },
                    recent_activity: []
                };
            } else if (u.startsWith('/api/broker/dashboard-summary')) {
                body = {
                    active_pipeline_cents: 50000000,
                    funded_volume_cents: 120000000,
                    active_lenders_count: 3,
                    counts: { active_cases: 2, funded_cases: 4, offers_received_cases: 1 },
                    commissions: { total_earned_cents: 2500000, paid_cents: 500000, pending_cents: 1000000, invoiced_cents: 1000000 },
                    recent_cases: []
                };
            } else if (u.startsWith('/api/leads/calendar')) {
                const params = new URLSearchParams(u.split('?')[1] || '');
                const yr = Number(params.get('year')) || new Date().getFullYear();
                const mo = Number(params.get('month')) || (new Date().getMonth() + 1);
                const moStr = String(mo).padStart(2, '0');
                body = {
                    year: yr,
                    month: mo,
                    counts: { total: 2, calls: 1, meetings: 1 },
                    events: [
                        {
                            event_id: 'lead-1',
                            source: 'LEAD',
                            lead_id: 1,
                            customer_id: null,
                            person_name: 'Sofía Ramírez',
                            record_number: 'LEAD-00001',
                            phone: '5510001001',
                            activity_type: 'CALL',
                            scheduled_at: `${yr}-${moStr}-10T10:30:00`,
                            date: `${yr}-${moStr}-10`,
                            time: '10:30',
                            status: 'CONTACTED',
                            notes: 'Seguimiento telefónico'
                        },
                        {
                            event_id: 'lead-2',
                            source: 'LEAD',
                            lead_id: 2,
                            customer_id: null,
                            person_name: 'Diego Vargas',
                            record_number: 'LEAD-00002',
                            phone: '5510001002',
                            activity_type: 'MEETING',
                            scheduled_at: `${yr}-${moStr}-15T16:00:00`,
                            date: `${yr}-${moStr}-15`,
                            time: '16:00',
                            status: 'QUALIFIED',
                            notes: 'Cita en oficina'
                        }
                    ]
                };
            } else if (u.startsWith('/api/broker/')) {
                body = { total: 0, page: 1, page_size: 20, items: [] };
            }
            return {
                ok: true,
                status: 200,
                json: async () => body,
                text: async () => JSON.stringify(body)
            };
        };

        currentUser = {
            id: 1,
            full_name: 'Broker Admin',
            email: 'admin@broker.mx',
            role: 'OWNER_ADMIN',
            tenant_name: 'Broker MX',
            tenant_slug: 'broker-mx',
            enabled_modules: ['CORE', 'BROKER'],
            preferred_language: 'es'
        };

        applyShellMode(false, currentUser);
        await loadDashboardSummary();

        // Switch UI language to Spanish
        await NuraI18n.setLanguage('es', { persist: false });

        // Inspect Dashboard + Sidebar Spanish texts
        const sidebarBrokerText = document.getElementById('side-btn-broker')?.innerText.trim() || '';
        const dashCoreHtml = document.getElementById('tab-home')?.innerText || '';
        const dashBrokerHtml = document.getElementById('dash-broker-section')?.innerText || '';

        // Open Profile -> Personal & Language modal and verify Spanish heading
        openProfileSection('personal');
        const profileModalText = document.getElementById('profile-detail-modal')?.innerText || '';
        document.getElementById('profile-detail-modal')?.classList.add('hidden');

        // Render visual calendar in Broker Calendar view
        if (typeof window.crmCalendarState === 'object') {
            window.crmCalendarState.year = 2026;
            window.crmCalendarState.month = 12;
        }
        await loadCrmCalendar('broker-calendar-root');
        const calRoot = document.getElementById('broker-calendar-root');
        const calText = calRoot?.innerText || '';
        const callChips = Array.from(calRoot.querySelectorAll('[data-cal-activity-type="CALL"]')).map(el => el.innerText.trim());
        const meetingChips = Array.from(calRoot.querySelectorAll('[data-cal-activity-type="MEETING"]')).map(el => el.innerText.trim());

        // Open New Lead Modal and test INDIVIDUAL vs BUSINESS toggling
        openLeadFormModal('create');
        const indState = {
            splitVisible: !document.getElementById('lf-individual-name-group').classList.contains('hidden'),
            bizHidden: document.getElementById('lf-business-name-group').classList.contains('hidden'),
            curpVisible: !document.getElementById('lf-curp-group').classList.contains('hidden'),
            curpRequired: document.getElementById('lf-curp').required,
            firstRequired: document.getElementById('lf-first-name').required,
            lastRequired: document.getElementById('lf-last-name').required,
            rfcRequired: document.getElementById('lf-rfc').required,
        };
        document.getElementById('lf-entity-type').value = 'BUSINESS';
        toggleLeadModalEntityFields();
        const bizState = {
            splitHidden: document.getElementById('lf-individual-name-group').classList.contains('hidden'),
            bizVisible: !document.getElementById('lf-business-name-group').classList.contains('hidden'),
            curpHidden: document.getElementById('lf-curp-group').classList.contains('hidden'),
            curpRequired: document.getElementById('lf-curp').required,
            fullRequired: document.getElementById('lf-full-name').required,
        };

        return {
            sidebarBrokerText,
            dashCoreHtml,
            dashBrokerHtml,
            profileModalText,
            calText,
            callChips,
            meetingChips,
            indState,
            bizState
        };
    })()
    """
    out = _run_cdp_eval(js_test)

    # 1. Spanish i18n verification across Sidebar, Dashboard (Core + Broker), and Profile Modal
    assert "Broker de Crédito" in out["sidebarBrokerText"], out["sidebarBrokerText"]
    assert "Credit Broker" not in out["sidebarBrokerText"], out["sidebarBrokerText"]

    assert "Llamadas Vencidas" in out["dashCoreHtml"], out["dashCoreHtml"]
    assert "Próximas (7d)" in out["dashCoreHtml"], out["dashCoreHtml"]
    assert "Actividad Reciente del CRM" in out["dashCoreHtml"], out["dashCoreHtml"]
    assert "Overdue Calls" not in out["dashCoreHtml"]
    assert "Upcoming (7d)" not in out["dashCoreHtml"]
    assert "Recent CRM Activity" not in out["dashCoreHtml"]

    assert "Pipeline de Broker de Crédito y Comisiones" in out["dashBrokerHtml"], out["dashBrokerHtml"]
    assert "Pipeline Broker Activo" in out["dashBrokerHtml"], out["dashBrokerHtml"]
    assert "Expedientes Recientes de Broker" in out["dashBrokerHtml"], out["dashBrokerHtml"]
    assert "Recent Broker Cases" not in out["dashBrokerHtml"]

    assert "idioma y preferencias regionales" in out["profileModalText"].lower(), out["profileModalText"]
    assert "language & regional preferences" not in out["profileModalText"].lower()

    # 2. Visual Calendar verification (person names inside day cells + CALL vs MEETING visuals)
    assert len(out["callChips"]) == 1 and "Sofía Ramírez" in out["callChips"][0], out["callChips"]
    assert len(out["meetingChips"]) == 1 and "Diego Vargas" in out["meetingChips"][0], out["meetingChips"]
    assert "Llamadas (1)" in out["calText"], out["calText"]
    assert "Citas en persona (1)" in out["calText"], out["calText"]

    # 3. New Lead Modal Split Name & Mandatory CURP verification
    assert out["indState"] == {
        "splitVisible": True,
        "bizHidden": True,
        "curpVisible": True,
        "curpRequired": True,
        "firstRequired": True,
        "lastRequired": True,
        "rfcRequired": False,
    }, out["indState"]
    assert out["bizState"] == {
        "splitHidden": True,
        "bizVisible": True,
        "curpHidden": True,
        "curpRequired": False,
        "fullRequired": True,
    }, out["bizState"]
