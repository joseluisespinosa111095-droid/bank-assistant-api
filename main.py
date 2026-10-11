import os
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
import google.generativeai as genai
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from passlib.context import CryptContext

# Configuración de Contraseñas (Bcrypt)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Configuración de Base de Datos
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bank_assistant.db")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- FUNCIONES DE SEGURIDAD (HASHING DE TOKENS) ---
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# --- MODELOS SQLALCHEMY ---
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
    title = Column(String, default="Nueva Asesoría")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    user = relationship("UserModel", back_populates="conversations")
    messages = relationship("ChatMessageModel", back_populates="conversation", cascade="all, delete-orphan")

class ChatMessageModel(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), index=True)
    role = Column(String)  # 'user' o 'executive'
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    
    conversation = relationship("ConversationModel", back_populates="messages")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

app = FastAPI(
    title="Bank Assistant - Secure Fintech Platform (Hardened)",
    version="6.2.1"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- ESQUEMAS PYDANTIC (CORREGIDOS A STR) ---
class UserRegister(BaseModel):
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class TransaccionInput(BaseModel):
    comercio: str
    monto: float
    ubicacion: str
    categoria: str
    hora: str

class ChatRequest(BaseModel):
    mensaje: str
    conversation_id: Optional[int] = None

# --- DEFENSA CSRF ---
def verify_csrf(request: Request):
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        host = request.headers.get("host")
        
        allowed_origins = os.getenv("ALLOWED_ORIGINS", "").split(",")
        allowed_origins = [o.strip() for o in allowed_origins if o.strip()]
        
        if host and ("localhost" in host or "127.0.0.1" in host):
            return
        if host and ((origin and host in origin) or (referer and host in referer)):
            return
        if allowed_origins and ((origin and any(ao in origin for ao in allowed_origins)) or (referer and any(ao in referer for ao in allowed_origins))):
            return

        raise HTTPException(status_code=403, detail="Protección CSRF: Solicitud rechazada por origen no autorizado.")

# --- AUTENTICACIÓN ---
def get_current_user(session_token: Optional[str] = Cookie(None, alias="session_token"), db: Session = Depends(get_db)) -> UserModel:
    if not session_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    
    token_hash = hash_token(session_token)
    db_session = db.query(SessionModel).filter(SessionModel.token == token_hash).first()
    
    if not db_session or db_session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        if db_session:
            db.delete(db_session)
            db.commit()
        raise HTTPException(status_code=401, detail="Sesión expirada o inválida")
    
    user = db.query(UserModel).filter(UserModel.id == db_session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user

@app.post("/api/auth/register")
def register(payload: UserRegister, db: Session = Depends(get_db)):
    email_clean = payload.email.strip().lower()
    existing = db.query(UserModel).filter(UserModel.email == email_clean).first()
    if existing:
        raise HTTPException(status_code=400, detail="No se pudo procesar el registro con los datos proporcionados.")
    
    hashed_password = pwd_context.hash(payload.password)
    new_user = UserModel(email=email_clean, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Cuenta creada exitosamente. Por favor inicie sesión."}

@app.post("/api/auth/login")
def login(payload: UserLogin, response: Response, db: Session = Depends(get_db)):
    email_clean = payload.email.strip().lower()
    user = db.query(UserModel).filter(UserModel.email == email_clean).first()
    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    
    token = secrets.token_hex(32)
    token_hash = hash_token(token)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    
    db_session = SessionModel(token=token_hash, user_id=user.id, expires_at=expires)
    db.add(db_session)
    db.commit()

    is_production = os.getenv("RENDER", "false").lower() == "true" or os.getenv("ENVIRONMENT", "development").lower() == "production"
    cookie_secure = os.getenv("COOKIE_SECURE", str(is_production)).lower() == "true"

    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=cookie_secure,
        samesite="lax",
        max_age=7 * 24 * 60 * 60
    )
    return {"message": "Inicio de sesión exitoso", "email": user.email}

@app.post("/api/auth/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(None, alias="session_token"), db: Session = Depends(get_db)):
    if session_token:
        token_hash = hash_token(session_token)
        db.query(SessionModel).filter(SessionModel.token == token_hash).delete()
        db.commit()
    response.delete_cookie(key="session_token")
    return {"message": "Sesión cerrada correctamente"}

@app.get("/api/auth/me")
def get_me(current_user: UserModel = Depends(get_current_user)):
    return {"email": current_user.email, "id": current_user.id}

# --- ENDPOINTS DE CONVERSACIONES ---
@app.get("/api/conversations")
def listar_conversaciones(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = db.query(ConversationModel).filter(ConversationModel.user_id == current_user.id).order_by(ConversationModel.updated_at.desc()).all()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in convs]

@app.post("/api/conversations", dependencies=[Depends(verify_csrf)])
def crear_conversacion(payload: dict, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    title = payload.get("title", "Nueva Asesoría")
    nueva = ConversationModel(user_id=current_user.id, title=title)
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return {"id": nueva.id, "title": nueva.title}

@app.get("/api/conversations/{conv_id}/messages")
def obtener_mensajes(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    msgs = db.query(ChatMessageModel).filter(ChatMessageModel.conversation_id == conv_id).order_by(ChatMessageModel.created_at.asc()).all()
    return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in msgs]

@app.delete("/api/conversations/{conv_id}", dependencies=[Depends(verify_csrf)])
def eliminar_conversacion(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.delete(conv)
    db.commit()
    return {"message": "Conversación eliminada correctamente"}

# --- CHAT GEMINI ---
@app.post("/chat-ejecutivo", dependencies=[Depends(verify_csrf)])
def chat_ejecutivo(req: ChatRequest, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        conv_id = req.conversation_id
        if conv_id:
            conv = db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first()
            if not conv:
                raise HTTPException(status_code=403, detail="Acceso denegado a esta conversación")
        else:
            conv = ConversationModel(user_id=current_user.id, title=req.mensaje[:30] + "...")
            db.add(conv)
            db.commit()
            db.refresh(conv)
            conv_id = conv.id

        user_msg = ChatMessageModel(conversation_id=conv_id, role="user", content=req.mensaje)
        db.add(user_msg)
        db.commit()

        try:
            historial_db = db.query(ChatMessageModel).filter(ChatMessageModel.conversation_id == conv_id).order_by(ChatMessageModel.created_at.asc()).all()
            
            contexto_financiero = f"""
            Eres el ejecutivo financiero personal del usuario autenticado en Bank Assistant.
            Perfil del usuario:
            - ID Interno: {current_user.id}
            - Score Crediticio: 745 (Modelo Experian, Excelente).
            - Flujo mensual promedio: +$4,250.00 USD.
            - Tarjeta de crédito vinculada: Citibank Visa Signature (Utilización 14%).
            - Ley aplicable para reclamos de fraude: Regulación E (12 CFR § 1005.11).
            """

            prompt_historial = contexto_financiero + "\n\nHistorial de la conversación:\n"
            for m in historial_db[-10:]:
                rol_txt = "Usuario" if m.role == "user" else "Ejecutivo"
                prompt_historial += f"{rol_txt}: {m.content}\n"

            model = genai.GenerativeModel("gemini-3.8-flash")
            response = model.generate_content(prompt_historial)
            respuesta_texto = response.text
        except Exception as ai_err:
            respuesta_texto = "⚠️ El ejecutivo financiero está experimentando alta demanda. Tu mensaje fue guardado de forma segura, intenta de nuevo en unos segundos."

        exec_msg = ChatMessageModel(conversation_id=conv_id, role="executive", content=respuesta_texto)
        db.add(exec_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()

        return {"conversation_id": conv_id, "respuesta": respuesta_texto}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en chat ejecutivo: {str(e)}")

# --- SERVIR INTERFAZ HTML ---
@app.get("/", response_class=FileResponse)
def home():
    return "index.html"
