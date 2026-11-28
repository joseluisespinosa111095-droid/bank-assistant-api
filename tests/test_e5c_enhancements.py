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
    with patch.dict(
        os.environ,
        {"GEMINI_API_KEY": "test-fake-key-123", "GEMINI_MODEL": "", "GEMINI_API_VERSION": ""},
        clear=False,
    ), patch.object(
        gemini_mod.genai, "Client", return_value=mock_client
    ) as client_ctor:
        reply = AIService.generate_reply("Resumen del expediente", [prior_msg], "OWNER_ADMIN")
        assert reply == "Respuesta generada por Gemini 2.5"
        client_ctor.assert_called_once()
        ctor_kwargs = client_ctor.call_args.kwargs
        assert ctor_kwargs["api_key"] == "test-fake-key-123"
        assert ctor_kwargs["http_options"].api_version == "v1beta"
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        assert call_kwargs["model"] == "gemini-2.5-flash"
        assert len(call_kwargs["contents"]) == 2
        assert "OWNER_ADMIN" in call_kwargs["config"].system_instruction

    # 4. Verify clean fallback when primary model returns 404 NOT_FOUND
    class _FakeNotFoundError(Exception):
        code = 404
        status = "NOT_FOUND"

    mock_fallback_client = MagicMock()
    mock_fallback_resp = MagicMock()
    mock_fallback_resp.text = "Respuesta desde fallback"
    mock_fallback_client.models.generate_content.side_effect = [
        _FakeNotFoundError("404 NOT_FOUND: model not found"),
        mock_fallback_resp,
    ]

    clear_gemini_client_cache()
    with patch.dict(
        os.environ,
        {
            "GEMINI_API_KEY": "test-fake-key-456",
            "GEMINI_MODEL": "gemini-2.5-flash",
            "GEMINI_API_VERSION": "",
        },
        clear=False,
    ), patch.object(gemini_mod.genai, "Client", return_value=mock_fallback_client):
        reply_fb = AIService.generate_reply("Hola", [], "OWNER_ADMIN")
        assert reply_fb == "Respuesta desde fallback"
        called_models = [c.kwargs["model"] for c in mock_fallback_client.models.generate_content.call_args_list]
        assert called_models == ["gemini-2.5-flash", "gemini-3.8-flash"]
    clear_gemini_client_cache()


def test_02b_r1b_assistant_system_rules_all_4_module_profiles():
    from services.external import AIService

    lending_only_tokens = ("Personal Loan", "Business Loan", "LN-", "PAY-", "disburse", "amortization", "collections")
    broker_only_tokens = ("BRK-", "COM-", "partner financial institutions", "multi-lender")

    # 1. enabled_modules=None -> defensive CORE-only fallback (never assumes LENDING)
    rules_default = AIService.build_system_rules("SALES_AGENT")
    assert "SALES_AGENT" in rules_default
    assert "CORE CRM" in rules_default
    assert "read-only mode" in rules_default.lower()
    for tok in (*lending_only_tokens, *broker_only_tokens):
        assert tok not in rules_default, f"Unexpected token {tok!r} in default CORE rules"

    # 2. Explicit CORE-only
    rules_core = AIService.build_system_rules("OWNER_ADMIN", enabled_modules=["CORE"])
    assert "Active organization modules: CORE." in rules_core
    for tok in (*lending_only_tokens, *broker_only_tokens):
        assert tok not in rules_core, f"Unexpected token {tok!r} in CORE-only rules"

    # 3. CORE + BROKER (no LENDING leakage)
    rules_broker = AIService.build_system_rules("CREDIT_ADVISOR", enabled_modules=["core", "broker"])
    assert "Active organization modules: BROKER, CORE." in rules_broker
    assert "BRK-" in rules_broker and "COM-" in rules_broker and "partner financial institutions" in rules_broker
    for tok in lending_only_tokens:
        assert tok not in rules_broker, f"Unexpected LENDING token {tok!r} in CORE+BROKER rules"

    # 4. CORE + LENDING (no BROKER leakage)
    rules_lending = AIService.build_system_rules("OPERATIONS_OFFICER", enabled_modules=["CORE", "LENDING"])
    assert "Active organization modules: CORE, LENDING." in rules_lending
    assert "Personal Loan" in rules_lending and "Business Loan" in rules_lending and "LN-" in rules_lending
    for tok in broker_only_tokens:
        assert tok not in rules_lending, f"Unexpected BROKER token {tok!r} in CORE+LENDING rules"

    # 5. CORE + BROKER + LENDING (both verticals + explicit distinction)
    rules_hybrid = AIService.build_system_rules("OWNER_ADMIN", enabled_modules=["CORE", "BROKER", "LENDING"])
    assert "Active organization modules: BROKER, CORE, LENDING." in rules_hybrid
    assert "Personal Loan" in rules_hybrid and "LN-" in rules_hybrid
    assert "BRK-" in rules_hybrid and "COM-" in rules_hybrid
    assert "distinguish clearly" in rules_hybrid.lower()


def test_02c_r1b_chat_ejecutivo_injects_live_enabled_modules_and_hot_updates(tmp_path):
    from models import TenantModel, TenantModuleModel
    from services.external import clear_gemini_client_cache
    from services.external import gemini as gemini_mod

    main_mod, engine, prev_override = _bootstrap_test_app(tmp_path)
    SessionCheck = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        # Tenant A (default-org -> CORE + LENDING)
        client_a, headers_a = _login_client(main_mod.app, "owner-a@e5c.nura.io", "StrongPass123!")
        # Tenant B (tenant-b-e5c -> CORE + BROKER)
        client_b, headers_b = _login_client(main_mod.app, "owner-b@e5c.nura.io", "StrongPass123!")

        captured_instructions: list[str] = []

        def _fake_generate_content(**kwargs):
            captured_instructions.append(kwargs["config"].system_instruction)
            resp = MagicMock()
            resp.text = "OK respuesta de prueba"
            return resp

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = _fake_generate_content

        clear_gemini_client_cache()
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-r1b-key", "GEMINI_MODEL": "gemini-2.5-flash", "GEMINI_API_VERSION": "v1beta"},
            clear=False,
        ), patch.object(gemini_mod.genai, "Client", return_value=mock_client):
            # 1. Tenant A (CORE + LENDING)
            r_a = client_a.post(
                "/chat-ejecutivo",
                json={"mensaje": "¿Cómo revisar pagos de un préstamo?"},
                headers=headers_a,
            )
            assert r_a.status_code == 200, r_a.text
            assert len(captured_instructions) == 1
            assert "Active organization modules: CORE, LENDING." in captured_instructions[0]
            assert "Personal Loan" in captured_instructions[0]
            assert "BRK-" not in captured_instructions[0]

            # 2. Tenant B (CORE + BROKER)
            r_b1 = client_b.post(
                "/chat-ejecutivo",
                json={"mensaje": "¿Cómo comparar ofertas de financieras?"},
                headers=headers_b,
            )
            assert r_b1.status_code == 200, r_b1.text
            assert len(captured_instructions) == 2
            assert "Active organization modules: BROKER, CORE." in captured_instructions[1]
            assert "BRK-" in captured_instructions[1] and "COM-" in captured_instructions[1]
            assert "Personal Loan" not in captured_instructions[1]

            # 3. Hot module change in DB (disable BROKER for Tenant B without restarting session)
            db = SessionCheck()
            try:
                t_b = db.query(TenantModel).filter(TenantModel.slug == "tenant-b-e5c").one()
                tm = (
                    db.query(TenantModuleModel)
                    .filter(
                        TenantModuleModel.tenant_id == t_b.id,
                        TenantModuleModel.module_code == "BROKER",
                    )
                    .one()
                )
                tm.is_enabled = False
                db.commit()
            finally:
                db.close()

            r_b2 = client_b.post(
                "/chat-ejecutivo",
                json={"mensaje": "Resumen de seguimiento CRM", "conversation_id": r_b1.json()["conversation_id"]},
                headers=headers_b,
            )
            assert r_b2.status_code == 200, r_b2.text
            assert len(captured_instructions) == 3
            assert "Active organization modules: CORE." in captured_instructions[2]
            assert "BRK-" not in captured_instructions[2]
            assert "Personal Loan" not in captured_instructions[2]
        clear_gemini_client_cache()
    finally:
        if prev_override is None:
            main_mod.app.dependency_overrides.pop(main_mod.get_db, None)
        else:
            main_mod.app.dependency_overrides[main_mod.get_db] = prev_override
        engine.dispose()


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


def test_03_lead_split_name_optional_curp_and_conversion_carryover(tmp_path):
    main_mod, engine, prev_override = _bootstrap_test_app(tmp_path)
    try:
        client, headers = _login_client(main_mod.app, "owner-a@e5c.nura.io", "StrongPass123!")

        # 1. INDIVIDUAL with split name and NO CURP -> 200 (R1A: CURP is optional in CORE)
        r_no_curp = client.post(
            "/api/leads",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Elena",
                "last_name": "Torres",
                "phone_primary": "5511223300",
                "source": "REFERRAL",
                "product_interest": "Personal Loan",
            },
            headers=headers,
        )
        assert r_no_curp.status_code == 200, r_no_curp.text
        lead_no_curp = r_no_curp.json()
        assert lead_no_curp["curp"] is None
        assert lead_no_curp["full_name"] == "Elena Torres"

        # 1b. Historical compatibility: PATCH another field on lead with curp=NULL without sending curp -> 200
        r_patch_hist = client.patch(
            f"/api/leads/{lead_no_curp['id']}",
            json={"summary_notes": "Updated notes without touching CURP"},
            headers=headers,
        )
        assert r_patch_hist.status_code == 200, r_patch_hist.text
        assert r_patch_hist.json()["curp"] is None
        assert r_patch_hist.json()["summary_notes"] == "Updated notes without touching CURP"

        # 1c. Convert lead with curp=NULL -> 200 (does not block conversion)
        r_conv_null = client.post(
            f"/api/leads/{lead_no_curp['id']}/convert",
            json={"notes": "Converted without CURP"},
            headers=headers,
        )
        assert r_conv_null.status_code == 200, r_conv_null.text
        assert r_conv_null.json()["customer"]["curp"] is None

        # 2. INDIVIDUAL with malformed CURP (bad syntax or internal spaces) -> 422
        for bad_curp in ("BADCURP123", "GODE 561231HDFRRL09", "123456789012345678"):
            r_bad_curp = client.post(
                "/api/leads",
                json={
                    "entity_type": "INDIVIDUAL",
                    "first_name": "Carlos",
                    "last_name": "Mendoza Ruiz",
                    "curp": bad_curp,
                    "phone_primary": "5511223344",
                    "source": "REFERRAL",
                    "product_interest": "Personal Loan",
                },
                headers=headers,
            )
            assert r_bad_curp.status_code == 422, f"Expected 422 for {bad_curp!r}: {r_bad_curp.text}"

        # 3. INDIVIDUAL with valid split name + 18-char CURP (with surrounding spaces & lowercase) -> 200 normalized
        valid_curp = "  gode561231hdfrrl09  "
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

        # 5. BUSINESS lead -> CURP stored as NULL even if omitted or passed
        r_biz = client.post(
            "/api/leads",
            json={
                "entity_type": "BUSINESS",
                "full_name": "Constructora Norte SA de CV",
                "curp": "GODE561231HDFRRL09",
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


def test_03b_r1a_customers_curp_contract_and_historical_compat(tmp_path):
    main_mod, engine, prev_override = _bootstrap_test_app(tmp_path)
    try:
        client, headers = _login_client(main_mod.app, "owner-a@e5c.nura.io", "StrongPass123!")

        # 1. Create INDIVIDUAL customer WITHOUT CURP -> 200, curp=None
        r_no_curp = client.post(
            "/api/customers",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Laura",
                "last_name": "Sánchez",
                "phone_primary": "5544001101",
            },
            headers=headers,
        )
        assert r_no_curp.status_code == 200, r_no_curp.text
        c1 = r_no_curp.json()
        assert c1["curp"] is None

        # 2. Historical compatibility: PATCH another field on customer with curp=NULL without sending curp -> 200
        r_patch_other = client.patch(
            f"/api/customers/{c1['id']}",
            json={"address_full": "Av. Reforma 100"},
            headers=headers,
        )
        assert r_patch_other.status_code == 200, r_patch_other.text
        assert r_patch_other.json()["curp"] is None
        assert r_patch_other.json()["address_full"] == "Av. Reforma 100"

        # 3. PATCH customer with INVALID CURP -> 422
        r_patch_bad = client.patch(
            f"/api/customers/{c1['id']}",
            json={"curp": "INVALIDO123"},
            headers=headers,
        )
        assert r_patch_bad.status_code == 422, r_patch_bad.text

        # 4. PATCH customer with VALID CURP (lowercase + surrounding whitespace) -> 200 normalized
        r_patch_ok = client.patch(
            f"/api/customers/{c1['id']}",
            json={"curp": "  sall910202mdfrrl05  "},
            headers=headers,
        )
        assert r_patch_ok.status_code == 200, r_patch_ok.text
        assert r_patch_ok.json()["curp"] == "SALL910202MDFRRL05"

        # 5. GET customer detail & search by CURP return persisted value
        r_det = client.get(f"/api/customers/{c1['id']}")
        assert r_det.status_code == 200
        assert r_det.json()["curp"] == "SALL910202MDFRRL05"
        r_search = client.get("/api/customers?search=SALL910202MDFRRL05")
        assert r_search.status_code == 200
        assert r_search.json()["total"] == 1 and r_search.json()["items"][0]["id"] == c1["id"]

        # 6. PATCH customer clearing CURP with empty string -> 200, curp=None
        r_clear = client.patch(
            f"/api/customers/{c1['id']}",
            json={"curp": ""},
            headers=headers,
        )
        assert r_clear.status_code == 200, r_clear.text
        assert r_clear.json()["curp"] is None

        # 7. Create INDIVIDUAL customer WITH valid CURP -> 200 normalized
        r_create_ok = client.post(
            "/api/customers",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Mateo",
                "last_name": "Gómez",
                "curp": " gomm890303hdfrrl07 ",
                "phone_primary": "5544001102",
            },
            headers=headers,
        )
        assert r_create_ok.status_code == 200, r_create_ok.text
        assert r_create_ok.json()["curp"] == "GOMM890303HDFRRL07"

        # 8. Create INDIVIDUAL customer WITH invalid CURP -> 422
        r_create_bad = client.post(
            "/api/customers",
            json={
                "entity_type": "INDIVIDUAL",
                "first_name": "Mateo",
                "last_name": "Gómez",
                "curp": "NO_ES_CURP",
                "phone_primary": "5544001103",
            },
            headers=headers,
        )
        assert r_create_bad.status_code == 422, r_create_bad.text

        # 9. BUSINESS customer: curp stored as NULL even if provided
        r_biz = client.post(
            "/api/customers",
            json={
                "entity_type": "BUSINESS",
                "full_legal_name": "Servicios Delta SA de CV",
                "legal_representative_name": "Ana López",
                "curp": "GOMM890303HDFRRL07",
                "phone_primary": "5544001104",
            },
            headers=headers,
        )
        assert r_biz.status_code == 200, r_biz.text
        assert r_biz.json()["curp"] is None

        # 10. Switching INDIVIDUAL customer with CURP -> BUSINESS clears curp to NULL
        c_with_curp_id = r_create_ok.json()["id"]
        r_to_biz = client.patch(
            f"/api/customers/{c_with_curp_id}",
            json={
                "entity_type": "BUSINESS",
                "full_legal_name": "Comercializadora Mateo SA",
                "legal_representative_name": "Mateo Gómez",
            },
            headers=headers,
        )
        assert r_to_biz.status_code == 200, r_to_biz.text
        assert r_to_biz.json()["entity_type"] == "BUSINESS"
        assert r_to_biz.json()["curp"] is None
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

        // Open New Customer Modal and test INDIVIDUAL vs BUSINESS CURP visibility (optional in R1A)
        openCustomerFormModal('create');
        const custIndState = {
            curpVisible: !document.getElementById('cf-curp-group').classList.contains('hidden'),
            curpRequired: document.getElementById('cf-curp').required,
        };
        document.getElementById('cf-entity-type').value = 'BUSINESS';
        toggleCustomerModalEntityFields();
        const custBizState = {
            curpHidden: document.getElementById('cf-curp-group').classList.contains('hidden'),
            curpRequired: document.getElementById('cf-curp').required,
        };
        closeCustomerFormModal();

        // Check dynamic Assistant welcome message by module profile in Spanish (R1B)
        const iaWelcomeBrokerEs = getAssistantWelcomeMessage();
        currentUser.enabled_modules = ['CORE'];
        const iaWelcomeCoreEs = getAssistantWelcomeMessage();
        currentUser.enabled_modules = ['CORE', 'LENDING'];
        const iaWelcomeLendingEs = getAssistantWelcomeMessage();
        currentUser.enabled_modules = ['CORE', 'BROKER'];

        return {
            sidebarBrokerText,
            dashCoreHtml,
            dashBrokerHtml,
            profileModalText,
            calText,
            callChips,
            meetingChips,
            indState,
            bizState,
            custIndState,
            custBizState,
            iaWelcomeBrokerEs,
            iaWelcomeCoreEs,
            iaWelcomeLendingEs
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

    # 3. New Lead & New Customer Modals: Split Name & Optional CURP verification (R1A)
    assert out["indState"] == {
        "splitVisible": True,
        "bizHidden": True,
        "curpVisible": True,
        "curpRequired": False,
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
    assert out["custIndState"] == {
        "curpVisible": True,
        "curpRequired": False,
    }, out["custIndState"]
    assert out["custBizState"] == {
        "curpHidden": True,
        "curpRequired": False,
    }, out["custBizState"]

    # 4. Dynamic Assistant welcome message by module profile (R1B)
    assert "expedientes de broker" in out["iaWelcomeBrokerEs"].lower(), out["iaWelcomeBrokerEs"]
    assert "simulaciones de crédito" not in out["iaWelcomeBrokerEs"].lower(), out["iaWelcomeBrokerEs"]
    assert "simulaciones de crédito" not in out["iaWelcomeCoreEs"].lower(), out["iaWelcomeCoreEs"]
    assert "expedientes de broker" not in out["iaWelcomeCoreEs"].lower(), out["iaWelcomeCoreEs"]
    assert "simulaciones de crédito" in out["iaWelcomeLendingEs"].lower(), out["iaWelcomeLendingEs"]
