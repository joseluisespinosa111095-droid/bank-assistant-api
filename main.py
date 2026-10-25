import os
import re
import time
import hashlib
import json
import logging
import secrets
from collections import defaultdict
from datetime import datetime, date, timezone, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
import google.generativeai as genai
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text,
    Date, DateTime, Boolean, ForeignKey, event, text, or_
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from passlib.context import CryptContext

# --- SERVER LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nura.crm")

# --- DATABASE & SECURITY CONFIG ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

IS_PRODUCTION = (
    os.getenv("RENDER", "false").lower() == "true"
    or os.getenv("ENVIRONMENT", "development").lower() == "production"
)
DATABASE_URL = os.getenv("DATABASE_URL")

if IS_PRODUCTION and not DATABASE_URL:
    raise RuntimeError("CRITICAL: DATABASE_URL is required in production.")

if not DATABASE_URL:
    DATABASE_URL = "sqlite:///./bank_assistant.db"
elif DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- ROLES & INITIAL PRODUCT CATALOG ---
VALID_ROLES = {
    "OWNER_ADMIN",         # Owners (You, your wife, brother-in-law, sister-in-law)
    "SALES_AGENT",         # Future Salespersons / Vendedores
    "CREDIT_ADVISOR",      # Future Credit Analysts
    "OPERATIONS_OFFICER"   # Future Collections / Operations
}

SUPPORTED_PRODUCTS = ["Personal Loan", "Business Loan"]

VALID_CUSTOMER_ENTITY_TYPES = {"INDIVIDUAL", "BUSINESS"}
VALID_CUSTOMER_STATUSES = {"ACTIVE", "INACTIVE", "PENDING_VERIFICATION"}
VALID_VERIFICATION_STATUSES = {"NOT_PROVIDED", "PENDING_VERIFICATION", "VERIFIED"}


# --- SQLALCHEMY MODELS ---
class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String(120), nullable=True)
    password_hash = Column(String, nullable=False)
    role = Column(String(30), default="SALES_AGENT", nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversations = relationship("ConversationModel", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("SessionModel", back_populates="user", cascade="all, delete-orphan")
    audit_events = relationship("AuditLogModel", back_populates="actor")


class SessionModel(Base):
    __tablename__ = "sessions"
    token = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user = relationship("UserModel", back_populates="sessions")


class ConversationModel(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title = Column(String, default="New Consultation")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    user = relationship("UserModel", back_populates="conversations")
    messages = relationship("ChatMessageModel", back_populates="conversation", cascade="all, delete-orphan")


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("ConversationModel", back_populates="messages")


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True)
    actor_role = Column(String(30), nullable=True)
    event_type = Column(String(60), nullable=False, index=True)
    resource_type = Column(String(40), nullable=False)
    resource_id = Column(String(60), nullable=True)
    outcome = Column(String(20), nullable=False)
    ip_address = Column(String(45), nullable=True)
    details_json = Column(Text, nullable=True)

    actor = relationship("UserModel", back_populates="audit_events")


class CustomerModel(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    customer_number = Column(String(24), unique=True, index=True, nullable=False)
    entity_type = Column(String(20), default="INDIVIDUAL", nullable=False)
    status = Column(String(30), default="ACTIVE", index=True, nullable=False)
    verification_status = Column(String(30), default="PENDING_VERIFICATION", nullable=False)
    full_legal_name = Column(String(180), index=True, nullable=False)
    trade_name = Column(String(160), nullable=True)
    legal_representative_name = Column(String(160), nullable=True)
    tax_id_rfc = Column(String(20), index=True, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    phone_primary = Column(String(25), nullable=False)
    phone_primary_normalized = Column(String(20), unique=True, index=True, nullable=False)
    phone_secondary = Column(String(25), nullable=True)
    phone_secondary_normalized = Column(String(20), nullable=True)
    email = Column(String(255), index=True, nullable=True)
    address_full = Column(Text, nullable=True)
    employment_status = Column(String(60), nullable=True)
    employer_or_business_name = Column(String(160), nullable=True)
    occupation_or_industry = Column(String(120), nullable=True)
    employment_address = Column(Text, nullable=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    customer_since = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    owner = relationship("UserModel", foreign_keys=[owner_user_id])
    creator = relationship("UserModel", foreign_keys=[created_by_user_id])


@event.listens_for(AuditLogModel, "before_update")
def _prevent_audit_update(mapper, connection, target):
    raise RuntimeError("AuditLogModel is append-only: UPDATE is forbidden.")


@event.listens_for(AuditLogModel, "before_delete")
def _prevent_audit_delete(mapper, connection, target):
    raise RuntimeError("AuditLogModel is append-only: DELETE is forbidden.")


# --- RATE LIMITING, PHONE NORMALIZATION & IP HELPERS ---
_RATE_BUCKETS = defaultdict(list)


def get_client_ip(request: Request) -> str:
    if IS_PRODUCTION:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:45]
    if request.client and request.client.host:
        return request.client.host[:45]
    return "unknown"


def check_rate_limit(bucket_key: str, max_attempts: int = 6, window_seconds: int = 300) -> bool:
    now = time.time()
    _RATE_BUCKETS[bucket_key] = [t for t in _RATE_BUCKETS[bucket_key] if now - t < window_seconds]
    if len(_RATE_BUCKETS[bucket_key]) >= max_attempts:
        return False
    _RATE_BUCKETS[bucket_key].append(now)
    return True


def mask_email_for_audit(raw_email: str) -> str:
    clean = (raw_email or "").strip().lower()
    if "@" not in clean:
        return "invalid_identifier"
    local, domain = clean.split("@", 1)
    masked_local = (local[:2] + "***") if len(local) > 2 else "***"
    return f"{masked_local}@{domain}"[:60]


def normalize_phone_number(raw_phone: Optional[str], required: bool = True) -> Optional[str]:
    if not raw_phone or not raw_phone.strip():
        if required:
            raise HTTPException(status_code=422, detail="Primary phone number is required.")
        return None
    digits = re.sub(r"\D", "", raw_phone)
    # Normalize +52 (12 digits) or 521 (13 digits) Mexican numbers to standard 10-digit national number
    if len(digits) == 12 and digits.startswith("52"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("521"):
        digits = digits[3:]
    if len(digits) < 10 or len(digits) > 15:
        raise HTTPException(
            status_code=422,
            detail="Phone number must contain between 10 and 15 valid digits."
        )
    return digits


def generate_next_customer_number(db: Session) -> str:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(9002001)"))

    last_customer = db.query(CustomerModel.id, CustomerModel.customer_number).order_by(CustomerModel.id.desc()).first()
    next_seq = 1
    if last_customer:
        match = re.search(r"(\d+)$", last_customer.customer_number or "")
        if match:
            next_seq = max(int(match.group(1)) + 1, (last_customer.id or 0) + 1)
        else:
            next_seq = (last_customer.id or 0) + 1
    return f"CUST-{next_seq:05d}"


# --- TRANSACTIONAL & STANDALONE AUDIT SERVICE ---
SENSITIVE_AUDIT_KEYS = {
    "password", "password_hash", "token", "session_token",
    "csrf_token", "secret", "bootstrap_secret", "api_key",
    "phone", "phone_primary", "phone_secondary", "address_full",
    "tax_id_rfc", "date_of_birth", "full_legal_name"
}


def record_audit_event(
    db: Session,
    event_type: str,
    resource_type: str,
    outcome: str,
    actor: Optional[UserModel] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[dict] = None,
    commit: bool = True
):
    """
    If commit=False, participates in the caller's transaction via db.flush() (fail-closed).
    If commit=True, commits immediately so security/denial events persist before HTTPException.
    """
    sanitized_details = {}
    if details:
        for k, v in details.items():
            if str(k).lower() in SENSITIVE_AUDIT_KEYS:
                continue
            sanitized_details[str(k)] = str(v)[:255]

    entry = AuditLogModel(
        actor_user_id=actor.id if actor else None,
        actor_role=actor.role if actor else None,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=str(resource_id)[:60] if resource_id is not None else None,
        outcome=outcome,
        ip_address=ip_address,
        details_json=json.dumps(sanitized_details) if sanitized_details else None
    )

    if not commit:
        db.add(entry)
        db.flush()
        return

    try:
        db.add(entry)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Audit log standalone write failed: {type(e).__name__}")


# --- LIFESPAN & FASTAPI INIT ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database schema is managed by Alembic
    yield

app = FastAPI(title="NURA CRM | Credit & Pipeline System", version="7.2.0", lifespan=lifespan)

if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- PYDANTIC SCHEMAS ---
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: str = Field(default="OWNER_ADMIN")


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserUpdateByAdmin(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = None
    is_active: Optional[bool] = None


class BootstrapAdminRequest(BaseModel):
    secret: str = Field(..., min_length=12)


class ChatRequest(BaseModel):
    mensaje: str = Field(..., min_length=1, max_length=1500)
    conversation_id: Optional[int] = None


class TransaccionInput(BaseModel):
    descripcion: str
    monto: float
    tipo: str


class CustomerCreateRequest(BaseModel):
    entity_type: str = Field(default="INDIVIDUAL")
    status: str = Field(default="ACTIVE")
    verification_status: str = Field(default="PENDING_VERIFICATION")
    full_legal_name: str = Field(..., min_length=3, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    date_of_birth: Optional[date] = None
    phone_primary: str = Field(..., min_length=10, max_length=25)
    phone_secondary: Optional[str] = Field(default=None, max_length=25)
    email: Optional[EmailStr] = None
    address_full: Optional[str] = Field(default=None, max_length=1000)
    employment_status: Optional[str] = Field(default=None, max_length=60)
    employer_or_business_name: Optional[str] = Field(default=None, max_length=160)
    occupation_or_industry: Optional[str] = Field(default=None, max_length=120)
    employment_address: Optional[str] = Field(default=None, max_length=1000)
    owner_user_id: Optional[int] = None


class CustomerUpdateRequest(BaseModel):
    entity_type: Optional[str] = None
    status: Optional[str] = None
    verification_status: Optional[str] = None
    full_legal_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    date_of_birth: Optional[date] = None
    phone_primary: Optional[str] = Field(default=None, min_length=10, max_length=25)
    phone_secondary: Optional[str] = Field(default=None, max_length=25)
    email: Optional[EmailStr] = None
    address_full: Optional[str] = Field(default=None, max_length=1000)
    employment_status: Optional[str] = Field(default=None, max_length=60)
    employer_or_business_name: Optional[str] = Field(default=None, max_length=160)
    occupation_or_industry: Optional[str] = Field(default=None, max_length=120)
    employment_address: Optional[str] = Field(default=None, max_length=1000)
    owner_user_id: Optional[int] = None


# --- CSRF PROTECTION WITH AUDIT ---
def verify_csrf(request: Request, db: Session = Depends(get_db)):
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        ip = get_client_ip(request)
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        allowed_origins = [o.strip().rstrip('/') for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
        is_dev = (
            os.getenv("ENVIRONMENT", "development").lower() != "production"
            and os.getenv("RENDER", "false").lower() != "true"
        )
        if is_dev:
            allowed_origins.extend([
                "http://localhost", "http://127.0.0.1",
                "http://localhost:8000", "http://127.0.0.1:8000"
            ])

        valid_origin = False
        if origin:
            if origin.rstrip('/') in allowed_origins:
                valid_origin = True
        elif referer:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            base_referer = f"{parsed.scheme}://{parsed.netloc}"
            if base_referer in allowed_origins:
                valid_origin = True

        if not valid_origin:
            record_audit_event(
                db, "CSRF_ORIGIN_DENIED", "security", "DENIED",
                resource_id=request.url.path, ip_address=ip,
                details={"origin": origin or referer or "missing"}, commit=True
            )
            raise HTTPException(status_code=403, detail="CSRF: Unauthorized origin.")

        csrf_cookie = request.cookies.get("csrf_token")
        csrf_header = request.headers.get("X-CSRF-Token")

        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            record_audit_event(
                db, "CSRF_TOKEN_INVALID", "security", "DENIED",
                resource_id=request.url.path, ip_address=ip, commit=True
            )
            raise HTTPException(status_code=403, detail="CSRF: Missing or invalid token.")


# --- AUTHENTICATION & RBAC DEPENDENCIES ---
def get_current_user(
    request: Request,
    response: Response,
    session_token: Optional[str] = Cookie(None, alias="session_token"),
    db: Session = Depends(get_db)
) -> UserModel:
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token_hash = hash_token(session_token)
    db_session = db.query(SessionModel).filter(SessionModel.token == token_hash).first()

    if not db_session or db_session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        if db_session:
            db.delete(db_session)
            db.commit()
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.query(UserModel).filter(UserModel.id == db_session.user_id).first()
    if not user:
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        db.query(SessionModel).filter(SessionModel.user_id == user.id).delete()
        db.commit()
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        record_audit_event(
            db, "AUTH_INACTIVE_USER_BLOCKED", "auth", "DENIED",
            actor=user, resource_id=str(user.id), ip_address=get_client_ip(request), commit=True
        )
        raise HTTPException(status_code=403, detail="Account has been deactivated.")

    return user


def require_roles(*allowed_roles: str):
    def role_checker(
        request: Request,
        user: UserModel = Depends(get_current_user),
        db: Session = Depends(get_db)
    ) -> UserModel:
        if user.role not in allowed_roles:
            record_audit_event(
                db, "RBAC_ACCESS_DENIED", "endpoint", "DENIED",
                actor=user, resource_id=request.url.path, ip_address=get_client_ip(request),
                details={"required": ",".join(allowed_roles), "actual": user.role}, commit=True
            )
            raise HTTPException(status_code=403, detail="Access denied: insufficient permissions.")
        return user
    return role_checker


# --- DECOUPLED AI SERVICE (NURA ASSISTANT) ---
class AIService:
    @staticmethod
    def generate_reply(user_message: str, prior_messages: List[ChatMessageModel], user_role: str) -> str:
        if not os.getenv("GEMINI_API_KEY"):
            return "⚠️ Nura Assistant is temporarily unavailable (API key not configured)."

        model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        system_rules = (
            "You are NURA, the internal intelligent assistant for a credit & pipeline CRM team. "
            f"Current authenticated team member role: {user_role}. "
            "Supported credit products: Personal Loan, Business Loan.\n"
            "STRICT SECURITY RULES:\n"
            "1. You operate strictly in advisory and read-only mode. You cannot approve loans, "
            "disburse money, modify balances, or execute financial transactions.\n"
            "2. Never invent customer balances, payments, or approval confirmations.\n"
            "3. Respond clearly and professionally in the same language used by the user."
        )

        try:
            structured_contents = []
            for m in prior_messages[-10:]:
                mapped_role = "user" if m.role == "user" else "model"
                structured_contents.append({"role": mapped_role, "parts": [m.content[:2000]]})

            structured_contents.append({"role": "user", "parts": [user_message.strip()]})

            try:
                model = genai.GenerativeModel(model_name, system_instruction=system_rules)
                res = model.generate_content(structured_contents)
            except TypeError:
                model = genai.GenerativeModel(model_name)
                fallback_prompt = f"[SYSTEM INSTRUCTIONS]\n{system_rules}\n\n[CONVERSATION HISTORY]\n"
                for m in prior_messages[-10:]:
                    fallback_prompt += f"{m.role}: {m.content[:1500]}\n"
                fallback_prompt += f"user: {user_message.strip()}\n"
                res = model.generate_content(fallback_prompt)

            text_out = res.text.strip() if res and getattr(res, "text", None) else "No response received."
            return text_out
        except Exception as e:
            logger.error(f"Gemini provider error: {type(e).__name__}")
            return "⚠️ Nura Assistant is busy right now. Your message has been saved."


# --- AUTHENTICATION & ATOMIC BOOTSTRAP ENDPOINTS ---
@app.post("/api/auth/login")
def login(user_data: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    email_clean = user_data.email.strip().lower()

    if not check_rate_limit(f"login:{ip}:{email_clean}", max_attempts=6, window_seconds=300):
        record_audit_event(
            db, "LOGIN_RATE_LIMITED", "auth", "DENIED",
            resource_id=mask_email_for_audit(email_clean), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait 5 minutes.")

    user = db.query(UserModel).filter(UserModel.email == email_clean).first()
    if not user or not pwd_context.verify(user_data.password, user.password_hash):
        record_audit_event(
            db, "LOGIN_FAILED", "auth", "ERROR",
            resource_id=mask_email_for_audit(email_clean), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        record_audit_event(
            db, "LOGIN_BLOCKED_INACTIVE", "auth", "DENIED",
            actor=user, resource_id=str(user.id), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=403, detail="Account is deactivated")

    token = secrets.token_hex(32)
    csrf_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)

    db.add(SessionModel(token=hash_token(token), user_id=user.id, expires_at=expires))
    db.commit()

    cookie_secure = os.getenv("COOKIE_SECURE", str(IS_PRODUCTION)).lower() == "true"
    response.set_cookie(
        key="session_token", value=token, httponly=True,
        secure=cookie_secure, samesite="lax", max_age=604800, path="/"
    )
    response.set_cookie(
        key="csrf_token", value=csrf_token, httponly=False,
        secure=cookie_secure, samesite="lax", max_age=604800, path="/"
    )

    record_audit_event(
        db, "LOGIN_SUCCESS", "auth", "SUCCESS",
        actor=user, resource_id=str(user.id), ip_address=ip, commit=True
    )
    return {
        "message": "Login successful",
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role
    }


@app.post("/api/auth/bootstrap-admin", dependencies=[Depends(verify_csrf)])
def bootstrap_admin(
    payload: BootstrapAdminRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)

    if not check_rate_limit(
        f"bootstrap:{ip}",
        max_attempts=5,
        window_seconds=600
    ):
        record_audit_event(
            db,
            "BOOTSTRAP_RATE_LIMITED",
            "admin",
            "DENIED",
            actor=current_user,
            ip_address=ip,
            commit=True
        )
        raise HTTPException(
            status_code=429,
            detail="Too many bootstrap attempts."
        )

    expected_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    expected_secret = os.getenv("BOOTSTRAP_ADMIN_SECRET", "").strip()

    if not expected_email or not expected_secret:
        record_audit_event(
            db,
            "BOOTSTRAP_NOT_CONFIGURED",
            "admin",
            "DENIED",
            actor=current_user,
            ip_address=ip,
            commit=True
        )
        raise HTTPException(
            status_code=403,
            detail="Bootstrap is disabled in environment."
        )

    if (
        current_user.email.strip().lower() != expected_email
        or not secrets.compare_digest(payload.secret, expected_secret)
    ):
        record_audit_event(
            db,
            "BOOTSTRAP_INVALID_CREDENTIALS",
            "admin",
            "DENIED",
            actor=current_user,
            ip_address=ip,
            commit=True
        )
        raise HTTPException(
            status_code=403,
            detail="Invalid bootstrap credentials."
        )

    try:
        if db.bind and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(9001001)"))

        active_owners = (
            db.query(UserModel.id)
            .filter(
                UserModel.role == "OWNER_ADMIN",
                UserModel.is_active.is_(True)
            )
            .first()
        )

        already_bootstrapped = (
            db.query(AuditLogModel.id)
            .filter(
                AuditLogModel.event_type == "BOOTSTRAP_ADMIN_SUCCESS"
            )
            .first()
        )

        if active_owners is not None or already_bootstrapped is not None:
            db.rollback()

            record_audit_event(
                db,
                "BOOTSTRAP_ALREADY_USED",
                "admin",
                "DENIED",
                actor=current_user,
                ip_address=ip,
                commit=True
            )

            raise HTTPException(
                status_code=403,
                detail="Initial Owner Admin has already been initialized."
            )

        current_user.role = "OWNER_ADMIN"
        current_user.is_active = True

        record_audit_event(
            db,
            "BOOTSTRAP_ADMIN_SUCCESS",
            "admin",
            "SUCCESS",
            actor=current_user,
            resource_id=str(current_user.id),
            ip_address=ip,
            details={"promoted_role": "OWNER_ADMIN"},
            commit=False
        )

        db.commit()

        logger.info("Bootstrap completed successfully.")

        return {
            "message": "Account promoted to OWNER_ADMIN",
            "email": current_user.email,
            "role": current_user.role
        }

    except HTTPException:
        raise

    except Exception:
        db.rollback()
        logger.exception("Bootstrap failed with an unexpected error")
        raise HTTPException(
            status_code=500,
            detail="Could not complete bootstrap."
        )


@app.post("/api/auth/logout", dependencies=[Depends(verify_csrf)])
def logout(
    request: Request,
    response: Response,
    session_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if session_token:
        token_h = hash_token(session_token)
        s = db.query(SessionModel).filter(SessionModel.token == token_h).first()
        if s:
            u = db.query(UserModel).filter(UserModel.id == s.user_id).first()
            record_audit_event(
                db, "LOGOUT", "auth", "SUCCESS",
                actor=u, ip_address=get_client_ip(request), commit=True
            )
            db.delete(s)
            db.commit()

    response.delete_cookie(key="session_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "Logged out"}


@app.get("/api/auth/me")
def get_me(current_user: UserModel = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "supported_products": SUPPORTED_PRODUCTS
    }


# --- TEAM MANAGEMENT ENDPOINTS (CLOSED PUBLIC REGISTRATION) ---
@app.post("/api/auth/register", dependencies=[Depends(verify_csrf)])
def register(
    user_data: UserRegister,
    request: Request,
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    email_clean = user_data.email.strip().lower()

    if user_data.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {', '.join(sorted(VALID_ROLES))}")

    if db.query(UserModel).filter(UserModel.email == email_clean).first():
        record_audit_event(
            db, "USER_CREATE_DUPLICATE", "user", "ERROR",
            actor=admin, resource_id=mask_email_for_audit(email_clean), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=400, detail="Email is already registered")

    try:
        new_user = UserModel(
            email=email_clean,
            full_name=user_data.full_name.strip() if user_data.full_name else None,
            password_hash=pwd_context.hash(user_data.password),
            role=user_data.role,
            is_active=True
        )
        db.add(new_user)
        db.flush()

        record_audit_event(
            db, "USER_CREATED", "user", "SUCCESS",
            actor=admin, resource_id=str(new_user.id), ip_address=ip,
            details={"assigned_role": new_user.role}, commit=False
        )
        db.commit()
        db.refresh(new_user)
        return {
            "message": "Team member created successfully",
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "role": new_user.role
        }
    except Exception as e:
        db.rollback()
        logger.error(f"User creation rollback: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Failed to create user with audit trail.")


@app.get("/api/admin/users")
def list_team_users(
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    users = db.query(UserModel).order_by(UserModel.id.asc()).all()
    return [{
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at
    } for u in users]


@app.patch("/api/admin/users/{target_user_id}", dependencies=[Depends(verify_csrf)])
def update_team_user(
    target_user_id: int,
    payload: UserUpdateByAdmin,
    request: Request,
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        target = db.query(UserModel).filter(UserModel.id == target_user_id).with_for_update().first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        new_role = payload.role if payload.role is not None else target.role
        new_active = payload.is_active if payload.is_active is not None else target.is_active

        if new_role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role specified")

        # Prevent self-demotion or self-deactivation
        if target.id == admin.id and (new_role != "OWNER_ADMIN" or not new_active):
            raise HTTPException(
                status_code=400,
                detail="You cannot demote or deactivate your own OWNER_ADMIN account."
            )

        # PostgreSQL-safe check: lock rows on UserModel.id without combining FOR UPDATE + COUNT()
        if target.role == "OWNER_ADMIN" and (new_role != "OWNER_ADMIN" or not new_active):
            active_owner_rows = (
                db.query(UserModel.id)
                .filter(UserModel.role == "OWNER_ADMIN", UserModel.is_active.is_(True))
                .with_for_update()
                .all()
            )
            if len(active_owner_rows) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote or deactivate the last active OWNER_ADMIN."
                )

        if payload.full_name is not None:
            target.full_name = payload.full_name.strip() or None
        target.role = new_role
        target.is_active = new_active

        if not new_active:
            db.query(SessionModel).filter(SessionModel.user_id == target.id).delete()

        record_audit_event(
            db, "USER_UPDATED", "user", "SUCCESS",
            actor=admin, resource_id=str(target.id), ip_address=ip,
            details={"role": target.role, "is_active": target.is_active}, commit=False
        )
        db.commit()
        return {
            "message": "Team member updated",
            "id": target.id,
            "role": target.role,
            "is_active": target.is_active
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"User update failed: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not update user.")


@app.get("/api/admin/audit-logs")
def get_audit_logs(
    limit: int = 40,
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    safe_limit = min(max(1, limit), 100)
    logs = db.query(AuditLogModel).order_by(AuditLogModel.id.desc()).limit(safe_limit).all()
    return [{
        "id": l.id,
        "created_at": l.created_at,
        "actor_user_id": l.actor_user_id,
        "actor_role": l.actor_role,
        "event_type": l.event_type,
        "resource_type": l.resource_type,
        "resource_id": l.resource_id,
        "outcome": l.outcome,
        "ip_address": l.ip_address,
        "details": json.loads(l.details_json) if l.details_json else None
    } for l in logs]


# --- CUSTOMERS MODULE ENDPOINTS (STAGE 1 CREDIT BACKEND) ---
def serialize_customer_item(c: CustomerModel) -> dict:
    return {
        "id": c.id,
        "customer_number": c.customer_number,
        "entity_type": c.entity_type,
        "status": c.status,
        "verification_status": c.verification_status,
        "full_legal_name": c.full_legal_name,
        "trade_name": c.trade_name,
        "legal_representative_name": c.legal_representative_name,
        "tax_id_rfc": c.tax_id_rfc,
        "date_of_birth": c.date_of_birth.isoformat() if c.date_of_birth else None,
        "phone_primary": c.phone_primary,
        "phone_primary_normalized": c.phone_primary_normalized,
        "phone_secondary": c.phone_secondary,
        "email": c.email,
        "address_full": c.address_full,
        "employment_status": c.employment_status,
        "employer_or_business_name": c.employer_or_business_name,
        "occupation_or_industry": c.occupation_or_industry,
        "employment_address": c.employment_address,
        "owner_user_id": c.owner_user_id,
        "created_by_user_id": c.created_by_user_id,
        "customer_since": c.customer_since.isoformat() if c.customer_since else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        # Accounting indicators remain 0 until the loans table is enabled in Stage 3
        "current_outstanding_balance_cents": 0,
        "active_loans_count": 0,
        "last_payment_date": None
    }


@app.get("/api/customers")
def list_customers(
    search: Optional[str] = Query(default=None, max_length=120),
    status: Optional[str] = Query(default="ALL"),
    entity_type: Optional[str] = Query(default="ALL"),
    sort: str = Query(default="CREATED_DESC"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    query = db.query(CustomerModel)

    if status and status.upper() != "ALL":
        query = query.filter(CustomerModel.status == status.upper())

    if entity_type and entity_type.upper() != "ALL":
        query = query.filter(CustomerModel.entity_type == entity_type.upper())

    if search and search.strip():
        term = f"%{search.strip()}%"
        digits_only = re.sub(r"\D", "", search)
        filters = [
            CustomerModel.customer_number.ilike(term),
            CustomerModel.full_legal_name.ilike(term),
            CustomerModel.trade_name.ilike(term),
            CustomerModel.email.ilike(term),
            CustomerModel.tax_id_rfc.ilike(term)
        ]
        if digits_only:
            filters.append(CustomerModel.phone_primary_normalized.ilike(f"%{digits_only}%"))
        query = query.filter(or_(*filters))

    total = query.count()

    sort_key = (sort or "CREATED_DESC").upper()
    if sort_key == "NAME_ASC":
        query = query.order_by(CustomerModel.full_legal_name.asc(), CustomerModel.id.asc())
    elif sort_key == "NUMBER_ASC":
        query = query.order_by(CustomerModel.customer_number.asc())
    else:
        query = query.order_by(CustomerModel.id.desc())

    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()
    total_pages = max(1, (total + page_size - 1) // page_size)

    return {
        "items": [serialize_customer_item(c) for c in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }


@app.post("/api/customers", dependencies=[Depends(verify_csrf)])
def create_customer(
    payload: CustomerCreateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    entity_type = (payload.entity_type or "INDIVIDUAL").strip().upper()
    if entity_type not in VALID_CUSTOMER_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail="entity_type must be INDIVIDUAL or BUSINESS.")

    status_val = (payload.status or "ACTIVE").strip().upper()
    if status_val not in VALID_CUSTOMER_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status. Allowed: {sorted(VALID_CUSTOMER_STATUSES)}")

    verif_val = (payload.verification_status or "PENDING_VERIFICATION").strip().upper()
    if verif_val not in VALID_VERIFICATION_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid verification_status. Allowed: {sorted(VALID_VERIFICATION_STATUSES)}")

    full_name_clean = payload.full_legal_name.strip()
    if len(full_name_clean) < 3:
        raise HTTPException(status_code=422, detail="Full Legal Name must have at least 3 characters.")

    legal_rep_clean = payload.legal_representative_name.strip() if payload.legal_representative_name else None
    if entity_type == "BUSINESS" and not legal_rep_clean:
        raise HTTPException(
            status_code=422,
            detail="Legal Representative Name is required for Business (Persona Moral) customers."
        )

    phone_norm = normalize_phone_number(payload.phone_primary, required=True)
    phone_sec_norm = normalize_phone_number(payload.phone_secondary, required=False)
    email_clean = payload.email.strip().lower() if payload.email else None
    rfc_clean = payload.tax_id_rfc.strip().upper() if payload.tax_id_rfc and payload.tax_id_rfc.strip() else None

    try:
        # Deduplication check by primary phone
        dup_phone = db.query(CustomerModel).filter(CustomerModel.phone_primary_normalized == phone_norm).first()
        if dup_phone:
            raise HTTPException(
                status_code=409,
                detail=f"Primary phone number is already registered under customer {dup_phone.customer_number}."
            )

        # Deduplication check by email (if provided)
        if email_clean:
            dup_email = db.query(CustomerModel).filter(CustomerModel.email == email_clean).first()
            if dup_email:
                raise HTTPException(
                    status_code=409,
                    detail=f"Email address is already registered under customer {dup_email.customer_number}."
                )

        # Deduplication check by RFC (if provided)
        if rfc_clean:
            dup_rfc = db.query(CustomerModel).filter(CustomerModel.tax_id_rfc == rfc_clean).first()
            if dup_rfc:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tax ID (RFC) is already registered under customer {dup_rfc.customer_number}."
                )

        customer_number = generate_next_customer_number(db)
        owner_id = payload.owner_user_id if payload.owner_user_id else current_user.id

        new_customer = CustomerModel(
            customer_number=customer_number,
            entity_type=entity_type,
            status=status_val,
            verification_status=verif_val,
            full_legal_name=full_name_clean,
            trade_name=payload.trade_name.strip() if payload.trade_name and payload.trade_name.strip() else None,
            legal_representative_name=legal_rep_clean,
            tax_id_rfc=rfc_clean,
            date_of_birth=payload.date_of_birth,
            phone_primary=payload.phone_primary.strip(),
            phone_primary_normalized=phone_norm,
            phone_secondary=payload.phone_secondary.strip() if payload.phone_secondary and payload.phone_secondary.strip() else None,
            phone_secondary_normalized=phone_sec_norm,
            email=email_clean,
            address_full=payload.address_full.strip() if payload.address_full and payload.address_full.strip() else None,
            employment_status=payload.employment_status.strip() if payload.employment_status and payload.employment_status.strip() else None,
            employer_or_business_name=payload.employer_or_business_name.strip() if payload.employer_or_business_name and payload.employer_or_business_name.strip() else None,
            occupation_or_industry=payload.occupation_or_industry.strip() if payload.occupation_or_industry and payload.occupation_or_industry.strip() else None,
            employment_address=payload.employment_address.strip() if payload.employment_address and payload.employment_address.strip() else None,
            owner_user_id=owner_id,
            created_by_user_id=current_user.id
        )
        db.add(new_customer)
        db.flush()

        # Transactional fail-closed audit log without PII
        record_audit_event(
            db, "CUSTOMER_CREATED", "customer", "SUCCESS",
            actor=current_user, resource_id=new_customer.customer_number, ip_address=ip,
            details={
                "customer_id": new_customer.id,
                "customer_number": new_customer.customer_number,
                "entity_type": new_customer.entity_type,
                "status": new_customer.status
            },
            commit=False
        )
        db.commit()
        db.refresh(new_customer)
        return serialize_customer_item(new_customer)

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"Error creating customer: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not create customer record.")


@app.get("/api/customers/{customer_id}")
def get_customer_detail(
    customer_id: int,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    data = serialize_customer_item(customer)
    data["owner_name"] = (customer.owner.full_name or customer.owner.email) if customer.owner else "Assigned Team Member"
    data["credit_summary"] = {
        "current_outstanding_principal_cents": 0,
        "total_amount_due_cents": 0,
        "overdue_amount_cents": 0,
        "active_loans_count": 0,
        "total_loans_count": 0,
        "last_payment_date": None,
        "next_payment_due_date": None,
        "remaining_installments_count": 0
    }
    data["loans"] = []
    return data


@app.patch("/api/customers/{customer_id}", dependencies=[Depends(verify_csrf)])
def update_customer(
    customer_id: int,
    payload: CustomerUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).with_for_update().first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found.")

        updated_fields = []

        if payload.entity_type is not None:
            et = payload.entity_type.strip().upper()
            if et not in VALID_CUSTOMER_ENTITY_TYPES:
                raise HTTPException(status_code=422, detail="Invalid entity_type.")
            customer.entity_type = et
            updated_fields.append("entity_type")

        if payload.status is not None:
            st = payload.status.strip().upper()
            if st not in VALID_CUSTOMER_STATUSES:
                raise HTTPException(status_code=422, detail="Invalid status.")
            customer.status = st
            updated_fields.append("status")

        if payload.verification_status is not None:
            vs = payload.verification_status.strip().upper()
            if vs not in VALID_VERIFICATION_STATUSES:
                raise HTTPException(status_code=422, detail="Invalid verification_status.")
            customer.verification_status = vs
            updated_fields.append("verification_status")

        if payload.full_legal_name is not None:
            name_clean = payload.full_legal_name.strip()
            if len(name_clean) < 3:
                raise HTTPException(status_code=422, detail="Full Legal Name must have at least 3 characters.")
            customer.full_legal_name = name_clean
            updated_fields.append("full_legal_name")

        if payload.phone_primary is not None:
            phone_norm = normalize_phone_number(payload.phone_primary, required=True)
            dup_phone = (
                db.query(CustomerModel)
                .filter(CustomerModel.phone_primary_normalized == phone_norm, CustomerModel.id != customer.id)
                .first()
            )
            if dup_phone:
                raise HTTPException(
                    status_code=409,
                    detail=f"Primary phone number already belongs to customer {dup_phone.customer_number}."
                )
            customer.phone_primary = payload.phone_primary.strip()
            customer.phone_primary_normalized = phone_norm
            updated_fields.append("phone_primary")

        if payload.phone_secondary is not None:
            sec_clean = payload.phone_secondary.strip()
            customer.phone_secondary = sec_clean or None
            customer.phone_secondary_normalized = normalize_phone_number(sec_clean, required=False) if sec_clean else None
            updated_fields.append("phone_secondary")

        if payload.email is not None:
            email_clean = payload.email.strip().lower() if payload.email else None
            if email_clean:
                dup_email = (
                    db.query(CustomerModel)
                    .filter(CustomerModel.email == email_clean, CustomerModel.id != customer.id)
                    .first()
                )
                if dup_email:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Email address already belongs to customer {dup_email.customer_number}."
                    )
            customer.email = email_clean
            updated_fields.append("email")

        if payload.tax_id_rfc is not None:
            rfc_clean = payload.tax_id_rfc.strip().upper() or None
            if rfc_clean:
                dup_rfc = (
                    db.query(CustomerModel)
                    .filter(CustomerModel.tax_id_rfc == rfc_clean, CustomerModel.id != customer.id)
                    .first()
                )
                if dup_rfc:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Tax ID (RFC) already belongs to customer {dup_rfc.customer_number}."
                    )
            customer.tax_id_rfc = rfc_clean
            updated_fields.append("tax_id_rfc")

        if payload.trade_name is not None:
            customer.trade_name = payload.trade_name.strip() or None
            updated_fields.append("trade_name")

        if payload.legal_representative_name is not None:
            customer.legal_representative_name = payload.legal_representative_name.strip() or None
            updated_fields.append("legal_representative_name")

        if customer.entity_type == "BUSINESS" and not customer.legal_representative_name:
            raise HTTPException(
                status_code=422,
                detail="Legal Representative Name is required for Business (Persona Moral) customers."
            )

        if payload.date_of_birth is not None:
            customer.date_of_birth = payload.date_of_birth
            updated_fields.append("date_of_birth")

        if payload.address_full is not None:
            customer.address_full = payload.address_full.strip() or None
            updated_fields.append("address_full")

        if payload.employment_status is not None:
            customer.employment_status = payload.employment_status.strip() or None
            updated_fields.append("employment_status")

        if payload.employer_or_business_name is not None:
            customer.employer_or_business_name = payload.employer_or_business_name.strip() or None
            updated_fields.append("employer_or_business_name")

        if payload.occupation_or_industry is not None:
            customer.occupation_or_industry = payload.occupation_or_industry.strip() or None
            updated_fields.append("occupation_or_industry")

        if payload.employment_address is not None:
            customer.employment_address = payload.employment_address.strip() or None
            updated_fields.append("employment_address")

        if payload.owner_user_id is not None:
            customer.owner_user_id = payload.owner_user_id
            updated_fields.append("owner_user_id")

        customer.updated_at = datetime.now(timezone.utc)

        record_audit_event(
            db, "CUSTOMER_UPDATED", "customer", "SUCCESS",
            actor=current_user, resource_id=customer.customer_number, ip_address=ip,
            details={
                "customer_id": customer.id,
                "customer_number": customer.customer_number,
                "updated_fields": ",".join(updated_fields)[:240]
            },
            commit=False
        )
        db.commit()
        db.refresh(customer)
        return serialize_customer_item(customer)

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception(f"Error updating customer: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not update customer record.")


# --- CONVERSATIONS & NURA ASSISTANT ENDPOINTS ---
@app.get("/api/conversations")
def listar_conversaciones(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = (
        db.query(ConversationModel)
        .filter(ConversationModel.user_id == current_user.id)
        .order_by(ConversationModel.updated_at.desc())
        .all()
    )
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in convs]


@app.post("/api/conversations", dependencies=[Depends(verify_csrf)])
def crear_conversacion(payload: dict, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    nueva = ConversationModel(user_id=current_user.id, title=str(payload.get("title", "New Consultation"))[:60])
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return {"id": nueva.id, "title": nueva.title}


@app.get("/api/conversations/{conv_id}/messages")
def obtener_mensajes(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = (
        db.query(ConversationModel)
        .filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = (
        db.query(ChatMessageModel)
        .filter(ChatMessageModel.conversation_id == conv_id)
        .order_by(ChatMessageModel.created_at.asc())
        .all()
    )
    return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in msgs]


@app.delete("/api/conversations/{conv_id}", dependencies=[Depends(verify_csrf)])
def eliminar_conversacion(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = (
        db.query(ConversationModel)
        .filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"message": "Deleted"}


@app.post("/chat-ejecutivo", dependencies=[Depends(verify_csrf)])
def chat_ejecutivo(
    req: ChatRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        conv_id = req.conversation_id
        if conv_id:
            conv = (
                db.query(ConversationModel)
                .filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id)
                .first()
            )
            if not conv:
                raise HTTPException(status_code=403, detail="Unauthorized conversation")
        else:
            conv = ConversationModel(user_id=current_user.id, title=req.mensaje[:30] + "...")
            db.add(conv)
            db.commit()
            db.refresh(conv)
            conv_id = conv.id

        prior_messages = (
            db.query(ChatMessageModel)
            .filter(ChatMessageModel.conversation_id == conv_id)
            .order_by(ChatMessageModel.created_at.asc())
            .all()
        )

        db.add(ChatMessageModel(conversation_id=conv_id, role="user", content=req.mensaje))
        db.commit()

        respuesta_texto = AIService.generate_reply(
            user_message=req.mensaje,
            prior_messages=prior_messages,
            user_role=current_user.role
        )

        db.add(ChatMessageModel(conversation_id=conv_id, role="executive", content=respuesta_texto))
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()

        record_audit_event(
            db, "AI_CHAT_REQUEST", "chat", "SUCCESS",
            actor=current_user, resource_id=str(conv_id), ip_address=ip,
            details={"msg_len": len(req.mensaje), "role": current_user.role}, commit=True
        )
        return {"conversation_id": conv_id, "respuesta": respuesta_texto}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Chat error: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Error processing consultation.")


@app.get("/", response_class=FileResponse)
def home():
    return FileResponse(
        "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )
