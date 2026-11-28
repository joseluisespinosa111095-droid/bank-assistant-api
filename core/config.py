import logging
import os
import re

from passlib.context import CryptContext

from db_config import is_production

_GIT_COMMIT_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def get_build_git_commit() -> str:
    """Return a sanitized 7-char lowercase git commit SHA from env vars, or 'local'."""
    for env_key in ("RENDER_GIT_COMMIT", "GIT_COMMIT"):
        raw = os.getenv(env_key, "").strip()
        if not raw:
            continue
        if _GIT_COMMIT_HEX_RE.match(raw):
            return raw[:7].lower()
        return "local"
    return "local"

# --- SERVER LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nura.crm")

# --- SECURITY & RUNTIME CONFIG ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
DUMMY_BCRYPT_HASH = pwd_context.hash("nura-constant-time-mitigation-baseline-secret")

IS_PRODUCTION = is_production()
EXPECTED_ALEMBIC_REVISION = "015"  # al crear una nueva migracion, actualizar solo esta constante
PLATFORM_TENANT_SLUG = "platform"

# --- DOCUMENT UPLOAD LIMITS ---
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 512 * 1024  # holgura para boundaries y campos del formulario

# --- ROLES, PRODUCTS & CRM CATALOGS ---
VALID_ROLES = {
    "OWNER_ADMIN",         # Owners
    "SALES_AGENT",         # Salespersons / Vendedores
    "CREDIT_ADVISOR",      # Credit Analysts
    "OPERATIONS_OFFICER"   # Collections / Operations
}

VALID_LANGUAGES = {"en", "es"}

SUPPORTED_PRODUCTS = ["Personal Loan", "Business Loan"]

VALID_ENTITY_TYPES = {"INDIVIDUAL", "BUSINESS"}
VALID_FOLLOW_UP_TYPES = {"CALL", "MEETING"}
VALID_CUSTOMER_STATUSES = {"ACTIVE", "INACTIVE", "PENDING_VERIFICATION"}
VALID_VERIFICATION_STATUSES = {"NOT_PROVIDED", "PENDING_VERIFICATION", "VERIFIED"}

VALID_LEAD_STAGES = {
    "NEW", "CONTACT_ATTEMPTED", "CONTACTED", "QUALIFIED",
    "APPLICATION_STARTED", "UNDER_REVIEW", "APPROVED",
    "CONVERTED", "NURTURE", "LOST"
}

QUALIFIED_OR_LATER_STAGES = {
    "QUALIFIED", "APPLICATION_STARTED", "UNDER_REVIEW", "APPROVED"
}

VALID_LEAD_SOURCES = {
    "REFERRAL", "INBOUND_CALL", "CAMPAIGN", "DIRECT_OUTREACH",
    "WEBSITE", "REPEAT_BORROWER", "OTHER"
}

VALID_LEAD_PRIORITIES = {"LOW", "MEDIUM", "HIGH"}

VALID_INTERACTION_TYPES = {"CALL", "NOTE", "WHATSAPP", "EMAIL", "MEETING"}
VALID_INTERACTION_DIRECTIONS = {"OUTBOUND", "INBOUND", "INTERNAL"}
VALID_INTERACTION_VISIBILITIES = {"INTERNAL", "CUSTOMER_FACING"}

VALID_CALL_OUTCOMES = {
    "ANSWERED", "NO_ANSWER", "VOICEMAIL", "REQUESTED_CALLBACK",
    "INTERESTED", "NOT_INTERESTED", "FOLLOW_UP_REQUIRED", "WRONG_NUMBER"
}

VALID_LOAN_STATUSES = {"DRAFT", "APPROVED", "ACTIVE", "PAID_OFF", "CANCELLED", "DEFAULTED"}
VALID_DISBURSEMENT_METHODS = {"SPEI_TRANSFER", "BANK_DEPOSIT", "CHECK", "CASH_RECEIPT"}
VALID_PAYMENT_STATUSES = {"POSTED", "REVERSED"}
VALID_PAYMENT_METHODS = {"SPEI_TRANSFER", "BANK_DEPOSIT", "CHECK", "CASH_RECEIPT"}
VALID_DOCUMENT_TYPES = {"INE_ID", "PROOF_OF_ADDRESS", "BANK_STATEMENT", "SIGNED_CONTRACT", "OTHER"}
