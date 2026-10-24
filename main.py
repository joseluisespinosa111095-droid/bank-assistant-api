import os
import time
import hashlib
import json
import logging
import secrets
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
import google.generativeai as genai
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text,
    DateTime, Boolean, ForeignKey, event, text
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


@event.listens_for(AuditLogModel, "before_update")
def _prevent_audit_update(mapper, connection, target):
    raise RuntimeError("AuditLogModel is append-only: UPDATE is forbidden.")


@event.listens_for(AuditLogModel, "before_delete")
def _prevent_audit_delete(mapper, connection, target):
    raise RuntimeError("AuditLogModel is append-only: DELETE is forbidden.")


# --- RATE LIMITING & IP HELPERS ---
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


# --- TRANSACTIONAL & STANDALONE AUDIT SERVICE ---
SENSITIVE_AUDIT_KEYS = {
    "password", "password_hash", "token", "session_token",
    "csrf_token", "secret", "bootstrap_secret", "api_key"
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
        # Fail-closed: if audit flush fails, the caller's operation rolls back together
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

app = FastAPI(title="NURA CRM | Credit & Pipeline System", version="7.1.0", lifespan=lifespan)

if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- PYDANTIC SCHEMAS (Preserving backward compatibility) ---
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
    # Preserved from original main.py for backward compatibility
    descripcion: str
    monto: float
    tipo: str


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
            # Build structured conversation without duplicating the current user message
            structured_contents = []
            for m in prior_messages[-10:]:
                mapped_role = "user" if m.role == "user" else "model"
                structured_contents.append({"role": mapped_role, "parts": [m.content[:2000]]})

            structured_contents.append({"role": "user", "parts": [user_message.strip()]})

            try:
                model = genai.GenerativeModel(model_name, system_instruction=system_rules)
                res = model.generate_content(structured_contents)
            except TypeError:
                # Fallback if SDK version requires single prompt string
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
    if not check_rate_limit(f"bootstrap:{ip}", max_attempts=5, window_seconds=600):
        record_audit_event(db, "BOOTSTRAP_RATE_LIMITED", "admin", "DENIED", actor=current_user, ip_address=ip, commit=True)
        raise HTTPException(status_code=429, detail="Too many bootstrap attempts.")

    expected_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    expected_secret = os.getenv("BOOTSTRAP_ADMIN_SECRET", "").strip()

    if not expected_email or not expected_secret:
        record_audit_event(db, "BOOTSTRAP_NOT_CONFIGURED", "admin", "DENIED", actor=current_user, ip_address=ip, commit=True)
        raise HTTPException(status_code=403, detail="Bootstrap is disabled in environment.")

    if current_user.email != expected_email or not secrets.compare_digest(payload.secret, expected_secret):
        record_audit_event(db, "BOOTSTRAP_INVALID_CREDENTIALS", "admin", "DENIED", actor=current_user, ip_address=ip, commit=True)
        raise HTTPException(status_code=403, detail="Invalid bootstrap credentials.")

    try:
        # Atomic transaction lock in PostgreSQL to prevent concurrent race conditions
        if db.bind and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(9001001)"))

        active_owners = (
            db.query(UserModel)
            .filter(UserModel.role == "OWNER_ADMIN", UserModel.is_active.is_(True))
            .with_for_update()
            .count()
        )
        already_bootstrapped = (
            db.query(AuditLogModel)
            .filter(AuditLogModel.event_type == "BOOTSTRAP_ADMIN_SUCCESS")
            .first()
        )

        if active_owners > 0 or already_bootstrapped is not None:
            db.rollback()
            record_audit_event(db, "BOOTSTRAP_ALREADY_USED", "admin", "DENIED", actor=current_user, ip_address=ip, commit=True)
            raise HTTPException(status_code=403, detail="Initial Owner Admin has already been initialized.")

        current_user.role = "OWNER_ADMIN"
        current_user.is_active = True

        # Fail-closed transactional audit (commits together with role promotion)
        record_audit_event(
            db, "BOOTSTRAP_ADMIN_SUCCESS", "admin", "SUCCESS",
            actor=current_user, resource_id=str(current_user.id), ip_address=ip,
            details={"promoted_role": "OWNER_ADMIN"}, commit=False
        )
        db.commit()
        return {
            "message": "Account promoted to OWNER_ADMIN",
            "email": current_user.email,
            "role": current_user.role
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Bootstrap error: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not complete bootstrap.")


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

        # Prevent removing or deactivating the last active OWNER_ADMIN
        if target.role == "OWNER_ADMIN" and (new_role != "OWNER_ADMIN" or not new_active):
            active_owners = (
                db.query(UserModel)
                .filter(UserModel.role == "OWNER_ADMIN", UserModel.is_active.is_(True))
                .with_for_update()
                .count()
            )
            if active_owners <= 1:
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

        # Query previous messages BEFORE inserting the new user message to prevent duplicate prompt entry
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
    return "index.html"
