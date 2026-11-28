# NURA CRM (`bank-assistant-api`)

Multi-tenant SaaS CRM platform for financial institutions, direct lenders, and credit brokers built with **FastAPI**, **SQLAlchemy**, **Alembic**, **PostgreSQL**, and a modular **Tailwind CSS / Vanilla JS** SPA.

---

## Architecture & Modular Verticals

NURA enforces strict tenant isolation (composite `(tenant_id, ...)` foreign keys, atomic per-tenant sequences, and server-side RBAC + CSRF protection) with dynamically assignable verticals controlled per organization via `tenant_modules` and `require_module(...)`:

| Module | Scope | Description |
|---|---|---|
| **`CORE`** | Mandatory on all commercial tenants | Leads & commercial pipeline, visual calendar (calls & in-person appointments), customers directory, KYC document storage, team RBAC, append-only audit trail, and NURA AI Assistant (`google-genai`). |
| **`LENDING`** | Assignable (`CORE` required) | Direct credit origination (`LN-`), cent-exact flat/declining amortization engine, waterfall payment ledger (`PAY-`) with LIFO reversals, collections queue, and strict legal `lending_entity_name` contract printing. |
| **`BROKER`** | Assignable (`CORE` required) | Multi-lender credit brokerage: partner lender catalog, brokerage cases (`BRK-`), parallel lender submissions & offer comparison, single-offer selection, atomic funding + commission generation (`COM-`), and decoupled KPIs. |
| **`PLATFORM`** | System tenant (`slug="platform"`, `SUPERADMIN`) | Tenant onboarding, organization status control, module entitlement management (`GET /api/platform/modules`, `PATCH /api/platform/tenants/{id}/modules`), and platform-scoped audit trail. Never accesses commercial tenant data. |

> Roadmap catalog entries `COMMERCE` and `FINANCING` (`FINANCING -> COMMERCE, CORE`) remain seeded with `is_assignable = false`.

### Project Structure

```text
core/             # Config, security (PBKDF2/bcrypt, CSRF, rate limits), schema_guard, utils
dependencies/     # FastAPI auth, RBAC, tenant & require_module guards, DB session provider
routers/          # HTTP routers (auth, admin, customers, leads, loans, broker, dashboard, assistant, platform)
schemas/          # Pydantic v2 request contracts
services/         # HTTP-agnostic domain services (broker, modules, financial_engine, audit, customer_detail, serializers_*)
services/external/# External providers: Gemini AI (google-genai) & transactional email interface
alembic/          # Versioned database migrations (001 .. 015)
static/           # Modular frontend assets (CSS + JS 01..11) served by index.html
tests/            # Automated pytest suite (API, migrations, multi-tenant, AST purity, headless Chrome UI)
```

---

## Key Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (prod) | Runtime SQLAlchemy connection string (PostgreSQL in production; SQLite in local dev). |
| `MIGRATION_DATABASE_URL` | For Alembic | Direct PostgreSQL connection string used by `alembic` (falls back to `DATABASE_URL`). |
| `ENVIRONMENT` | Recommended | `production` or `development`. Production enforces HTTPS cookies, strict `schema_guard`, and no dev email logs. |
| `RENDER` | Set by Render | `true` on Render deployments; enables production safety checks. |
| `RATE_LIMIT_HMAC_SECRET` | Yes (prod) | High-entropy secret (≥ 32 chars) for HMAC-hashed login rate-limit keys. |
| `GEMINI_API_KEY` | Optional | Google AI API key used by `services/external/gemini.py` (`google-genai`). |
| `GEMINI_MODEL` | Optional | Gemini model identifier (default: `gemini-2.5-flash`). |
| `CRM_TIMEZONE` | Optional | Local business timezone for call buckets, calendar dates, and collections (default: `America/Mexico_City`). |
| `EMAIL_PROVIDER` | Optional | Transactional email provider for password reset (`NONE` fails closed in production). |
| `PASSWORD_RESET_BASE_URL` | Optional | Public base URL used to build password reset links. |

---

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\uvicorn.exe main:app --reload
```

---

## Database Migrations & `schema_guard` on Render

`main.py` runs `verify_database_schema_compatibility(engine)` inside FastAPI's `lifespan` hook (`core/schema_guard.py`). It is **strictly fail-closed** in production:

- The app does **not** run Alembic automatically on startup.
- On boot, it verifies all required tables exist and checks `SELECT version_num FROM alembic_version` against `EXPECTED_ALEMBIC_REVISION` in `core/config.py`.
- If the database revision does not match `EXPECTED_ALEMBIC_REVISION`, startup aborts with `RuntimeError` rather than serving against an inconsistent schema.

### Safe Production Deployment Sequence (Render)

1. Take a manual PostgreSQL backup in Render.
2. Apply migrations explicitly against the production database **before** pushing a new `EXPECTED_ALEMBIC_REVISION` to `origin/main`:
   ```powershell
   $env:MIGRATION_DATABASE_URL = "<render-external-postgres-url>"
   .\.venv\Scripts\python.exe -m alembic upgrade head
   .\.venv\Scripts\python.exe -m alembic current
   ```
3. Confirm `alembic current` matches `EXPECTED_ALEMBIC_REVISION` (`(head)`).
4. Push `main` to trigger the Render deploy and run a read-only smoke test (`GET /api/health` → `200 OK`).

---

## Running Tests (`pytest`)

All suites run against isolated Alembic-migrated SQLite databases (plus headless Chrome/Edge CDP tests for responsive module visibility and i18n):

```powershell
.\.venv\Scripts\pytest.exe -v
```

- Postgres-specific concurrency/trigger test (`tests/test_stage3_loans.py::test_06_postgres_triggers_and_concurrency`) is automatically skipped unless `TEST_POSTGRES_URL` is configured.
