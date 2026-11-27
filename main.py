import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import google.generativeai as genai

BASE_DIR = Path(__file__).resolve().parent


class CacheControlStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

# --- COMPATIBILITY RE-EXPORTS (preserves exact object identity for tests, scripts, and dependency_overrides) ---
from db_config import (
    DATABASE_URL,
    SessionLocal,
    connect_args,
    engine,
    get_engine_connect_args,
    get_runtime_database_url,
    is_production,
)
from models import (
    AuditLogModel,
    Base,
    ChatMessageModel,
    ConversationModel,
    CustomerDocumentModel,
    CustomerModel,
    InteractionModel,
    LeadModel,
    LeadStageHistoryModel,
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentAllocationModel,
    LoanPaymentModel,
    PasswordResetTokenModel,
    RateLimitModel,
    SessionModel,
    TenantModel,
    TenantSequenceModel,
    UserModel,
)
from core.config import (
    ALLOWED_MIME_TYPES,
    DUMMY_BCRYPT_HASH,
    EXPECTED_ALEMBIC_REVISION,
    IS_PRODUCTION,
    MAX_FILE_SIZE_BYTES,
    MULTIPART_OVERHEAD_BYTES,
    PLATFORM_TENANT_SLUG,
    QUALIFIED_OR_LATER_STAGES,
    SUPPORTED_PRODUCTS,
    UPLOAD_READ_CHUNK_BYTES,
    VALID_CALL_OUTCOMES,
    VALID_CUSTOMER_STATUSES,
    VALID_DISBURSEMENT_METHODS,
    VALID_DOCUMENT_TYPES,
    VALID_ENTITY_TYPES,
    VALID_INTERACTION_DIRECTIONS,
    VALID_INTERACTION_TYPES,
    VALID_INTERACTION_VISIBILITIES,
    VALID_LANGUAGES,
    VALID_LEAD_PRIORITIES,
    VALID_LEAD_SOURCES,
    VALID_LEAD_STAGES,
    VALID_LOAN_STATUSES,
    VALID_PAYMENT_METHODS,
    VALID_PAYMENT_STATUSES,
    VALID_ROLES,
    VALID_VERIFICATION_STATUSES,
    logger,
    pwd_context,
)
from core.middleware import DocumentUploadSizeGuard
from core.schema_guard import (
    is_auth_security_schema_ready,
    verify_database_schema_compatibility,
)
from core.security import (
    check_rate_limit,
    cleanup_expired_auth_security_records,
    compute_email_hmac_buckets,
    consume_rate_limit_atomic,
    get_client_ip,
    hash_token,
    mask_email_for_audit,
    reset_rate_limit_bucket,
)
from core.utils import (
    generate_next_customer_number,
    generate_next_lead_number,
    generate_next_loan_number,
    generate_next_payment_number,
    get_crm_timezone,
    get_local_day_bounds_utc,
    next_tenant_sequence,
    normalize_phone_number,
    parse_optional_datetime_utc,
    parse_pesos_to_cents,
    parse_rebate_detail,
    serialize_rebate_detail,
)
from schemas.auth import (
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    UserChangePasswordRequest,
    UserLanguageUpdateRequest,
    UserLogin,
    UserRegister,
    UserUpdateByAdmin,
)
from schemas.core_crm import (
    ChatRequest,
    CustomerCreateRequest,
    CustomerUpdateRequest,
    InteractionCreateRequest,
    LeadConvertRequest,
    LeadCreateRequest,
    LeadUpdateRequest,
)
from schemas.lending import (
    LoanActivateRequest,
    LoanApproveRequest,
    LoanCancelRequest,
    LoanCollectionLogRequest,
    LoanCreateRequest,
    LoanDefaultRequest,
    LoanPaymentCreateRequest,
    LoanPaymentReverseRequest,
)
from schemas.platform import (
    PlatformTenantCreateRequest,
    PlatformTenantUpdateRequest,
)
from services.audit import SENSITIVE_AUDIT_KEYS, record_audit_event
from services.customer_detail import get_customer_detail_payload
from services.external import (
    AIService,
    EmailDeliveryService,
    GENERIC_PASSWORD_RESET_MESSAGE,
)
from services.financial_engine import LoanFinancialEngine, LoanPaymentEngine
from services.serializers_core import (
    compose_full_legal_name,
    compute_call_bucket,
    normalize_name_part,
    resolve_customer_name_for_create,
    resolve_customer_name_for_update,
    serialize_customer,
    serialize_interaction,
    serialize_lead,
    serialize_tenant,
)
from services.serializers_lending import (
    compute_early_payoff_breakdown,
    serialize_loan_detail,
    serialize_loan_payment,
    serialize_loan_summary,
)
from dependencies.db import get_db
from dependencies.auth import (
    get_current_user,
    require_commercial_tenant,
    require_roles,
    require_superadmin,
    resolve_tenant_owner_user_id,
    verify_csrf,
)
from routers import (
    admin as admin_router,
    assistant as assistant_router,
    auth as auth_router,
    customers as customers_router,
    dashboard as dashboard_router,
    health as health_router,
    leads as leads_router,
    loans as loans_router,
    platform as platform_router,
)


# --- LIFESPAN & FASTAPI COMPOSITION ROOT ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_database_schema_compatibility(engine)
    yield


app = FastAPI(title="NURA CRM | Credit & Pipeline System", version="7.4.0", lifespan=lifespan)

if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

app.add_middleware(DocumentUploadSizeGuard)

# --- DOMAIN ROUTERS (exact registration order preserved) ---
app.include_router(health_router.router)
app.include_router(platform_router.router)
app.include_router(auth_router.router)
app.include_router(admin_router.router)
app.include_router(customers_router.router)
app.include_router(leads_router.router)
app.include_router(loans_router.router)
app.include_router(dashboard_router.router)
app.include_router(assistant_router.router)

# --- STATIC ASSETS (cwd-independent absolute path) ---
app.mount("/static", CacheControlStaticFiles(directory=str(BASE_DIR / "static")), name="static")
