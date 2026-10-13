import os
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
import google.generativeai as genai
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from passlib.context import CryptContext

# --- CONFIGURACIÓN SEGURA DE BASE DE DATOS ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

IS_PRODUCTION = os.getenv("RENDER", "false").lower() == "true" or os.getenv("ENVIRONMENT", "development").lower() == "production"
DATABASE_URL = os.getenv("DATABASE_URL")

if IS_PRODUCTION and not DATABASE_URL:
    raise RuntimeError("CRÍTICO: Faltan credenciales de PostgreSQL. DATABASE_URL es obligatoria en producción.")

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

# --- MODELOS ---
class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    conversations = relationship("ConversationModel", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("SessionModel", back_populates="user", cascade="all, delete-orphan")

class SessionModel(Base):
    __tablename__ = "sessions"
    token = Column(String, primary_key=True, index=True) 
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user = relationship("UserModel", back_populates="sessions")

class ConversationModel(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="New Consultation")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    user = relationship("UserModel", back_populates="conversations")
    messages = relationship("ChatMessageModel", back_populates="conversation", cascade="all, delete-orphan")

class ChatMessageModel(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    conversation = relationship("ConversationModel", back_populates="messages")


# --- AISLAMIENTO DE CREACIÓN DE TABLAS (LIFESPAN) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # La BD será creada por Alembic de ahora en adelante, ya no por SQLAlchemy
    # Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="NURA | Smart Finance", version="6.4.0", lifespan=lifespan)

if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- ESQUEMAS ---
class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class TransaccionInput(BaseModel):
    comercio: str; monto: float; ubicacion: str; categoria: str; hora: str

class ChatRequest(BaseModel):
    mensaje: str
    conversation_id: Optional[int] = None

# --- MITIGACIÓN CSRF SEGURA ---
def verify_csrf(request: Request):
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        
        allowed_origins = [o.strip().rstrip('/') for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
        is_dev = os.getenv("ENVIRONMENT", "development").lower() != "production" and os.getenv("RENDER", "false").lower() != "true"
        if is_dev:
            allowed_origins.extend(["http://localhost", "http://127.0.0.1", "http://localhost:8000", "http://127.0.0.1:8000"])
            
        valid = False
        if origin:
            if origin in allowed_origins:
                valid = True
        elif referer:
            from urllib.parse import urlparse
            parsed = urlparse(referer)
            base_referer = f"{parsed.scheme}://{parsed.netloc}"
            if base_referer in allowed_origins:
                valid = True
                
        if not valid:
            raise HTTPException(status_code=403, detail="CSRF: Unauthorized origin.")
            
        csrf_cookie = request.cookies.get("csrf_token")
        csrf_header = request.headers.get("X-CSRF-Token")
        if not csrf_cookie or not csrf_header or csrf_cookie != csrf_header:
            raise HTTPException(status_code=403, detail="CSRF: Missing or invalid token.")

# --- AUTENTICACIÓN SEGURA ---
def get_current_user(session_token: Optional[str] = Cookie(None, alias="session_token"), db: Session = Depends(get_db)) -> UserModel:
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    token_hash = hash_token(session_token)
    db_session = db.query(SessionModel).filter(SessionModel.token == token_hash).first()
    
    if not db_session or db_session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        if db_session:
            db.delete(db_session)
            db.commit()
        raise HTTPException(status_code=401, detail="Session expired or invalid")
    
    user = db.query(UserModel).filter(UserModel.id == db_session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

@app.post("/api/auth/register")
def register(payload: UserRegister, db: Session = Depends(get_db)):
    email_clean = payload.email.strip().lower()
    if db.query(UserModel).filter(UserModel.email == email_clean).first():
        raise HTTPException(status_code=400, detail="Could not process registration with the provided details.")
    
    db.add(UserModel(email=email_clean, password_hash=pwd_context.hash(payload.password)))
    db.commit()
    return {"message": "Account created successfully."}

@app.post("/api/auth/login")
def login(payload: UserLogin, response: Response, db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.email == payload.email.strip().lower()).first()
    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    
    token = secrets.token_hex(32)
    csrf_token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    
    db.add(SessionModel(token=hash_token(token), user_id=user.id, expires_at=expires))
    db.commit()

    cookie_secure = os.getenv("COOKIE_SECURE", str(IS_PRODUCTION)).lower() == "true"
    response.set_cookie(key="session_token", value=token, httponly=True, secure=cookie_secure, samesite="lax", max_age=604800, path="/")
    response.set_cookie(key="csrf_token", value=csrf_token, httponly=False, secure=cookie_secure, samesite="lax", max_age=604800, path="/")
    return {"message": "Signed in successfully", "email": user.email}

@app.post("/api/auth/logout", dependencies=[Depends(verify_csrf)])
def logout(response: Response, session_token: Optional[str] = Cookie(None), db: Session = Depends(get_db)):
    if session_token:
        db.query(SessionModel).filter(SessionModel.token == hash_token(session_token)).delete()
        db.commit()
    response.delete_cookie(key="session_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "Signed out"}

@app.get("/api/auth/me")
def get_me(current_user: UserModel = Depends(get_current_user)):
    return {"email": current_user.email, "id": current_user.id}

# --- ENDPOINTS ---
@app.get("/api/conversations")
def listar_conversaciones(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = db.query(ConversationModel).filter(ConversationModel.user_id == current_user.id).order_by(ConversationModel.updated_at.desc()).all()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in convs]

@app.post("/api/conversations", dependencies=[Depends(verify_csrf)])
def crear_conversacion(payload: dict, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    nueva = ConversationModel(user_id=current_user.id, title=payload.get("title", "New Consultation"))
    db.add(nueva); db.commit(); db.refresh(nueva)
    return {"id": nueva.id, "title": nueva.title}

@app.get("/api/conversations/{conv_id}/messages")
def obtener_mensajes(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    if not db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first():
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = db.query(ChatMessageModel).filter(ChatMessageModel.conversation_id == conv_id).order_by(ChatMessageModel.created_at.asc()).all()
    return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in msgs]

@app.delete("/api/conversations/{conv_id}", dependencies=[Depends(verify_csrf)])
def eliminar_conversacion(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first()
    if not conv: raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv); db.commit()
    return {"message": "Deleted"}

@app.post("/chat-ejecutivo", dependencies=[Depends(verify_csrf)])
def chat_ejecutivo(req: ChatRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        conv_id = req.conversation_id
        if conv_id:
            conv = db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first()
            if not conv: raise HTTPException(status_code=403, detail="Access denied")
        else:
            conv = ConversationModel(user_id=current_user.id, title=req.mensaje[:30] + "...")
            db.add(conv); db.commit(); db.refresh(conv)
            conv_id = conv.id

        db.add(ChatMessageModel(conversation_id=conv_id, role="user", content=req.mensaje))
        db.commit()

        try:
            historial = db.query(ChatMessageModel).filter(ChatMessageModel.conversation_id == conv_id).order_by(ChatMessageModel.created_at.asc()).all()
            prompt = (
                f"You are Nura, the virtual financial executive for NURA — Smart Finance. "
                f"User ID: {current_user.id}.\n"
                f"Instructions: Respond clearly, professionally, and concisely in English. "
                f"Strict rule: Never claim or imply that you have executed a money transfer, payment, or account modification, "
                f"as transactional execution is not active in this chat.\n\n"
            )
            for m in historial[-10:]: prompt += f"{m.role}: {m.content}\n"
            
            respuesta_texto = genai.GenerativeModel("gemini-3.8-flash").generate_content(prompt).text
        except:
            respuesta_texto = "⚠️ Nura is currently busy. Your message has been saved."

        db.add(ChatMessageModel(conversation_id=conv_id, role="executive", content=respuesta_texto))
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"conversation_id": conv_id, "respuesta": respuesta_texto}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/", response_class=FileResponse)
def home():
    return "index.html"
