import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Depends, Response, Cookie
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr
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
    title="Bank Assistant - Secure Multi-User Fintech Platform",
    version="6.0.0"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- ESQUEMAS PYDANTIC ---
class UserRegister(BaseModel):
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
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

# --- DEPENDENCIA DE AUTENTICACIÓN ---
def get_current_user(session_token: Optional[str] = Cookie(None, alias="session_token"), db: Session = Depends(get_db)) -> UserModel:
    if not session_token:
        raise HTTPException(status_code=401, detail="No autenticado")
    
    db_session = db.query(SessionModel).filter(SessionModel.token == session_token).first()
    if not db_session or db_session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        if db_session:
            db.delete(db_session)
            db.commit()
        raise HTTPException(status_code=401, detail="Sesión expirada o inválida")
    
    user = db.query(UserModel).filter(UserModel.id == db_session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")
    return user

# --- ENDPOINTS DE AUTENTICACIÓN ---
@app.post("/api/auth/register")
def register(payload: UserRegister, db: Session = Depends(get_db)):
    existing = db.query(UserModel).filter(UserModel.email == payload.email).first()
    if existing:
        # Mensaje genérico para no revelar existencia de cuentas
        raise HTTPException(status_code=400, detail="No se pudo procesar el registro con los datos proporcionados.")
    
    hashed_password = pwd_context.hash(payload.password)
    new_user = UserModel(email=payload.email, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": "Cuenta creada exitosamente. Por favor inicie sesión."}

@app.post("/api/auth/login")
def login(payload: UserLogin, response: Response, db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.email == payload.email).first()
    if not user or not pwd_context.verify(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    
    # Crear token de sesión seguro
    token = secrets.token_hex(32)
    expires = datetime.now(timezone.utc) + timedelta(days=7)
    
    db_session = SessionModel(token=token, user_id=user.id, expires_at=expires)
    db.add(db_session)
    db.commit()

    # Configurar Cookie HttpOnly Segura
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=False, # Cambiar a True en producción con HTTPS
        samesite="lax",
        max_age=7 * 24 * 60 * 60
    )
    return {"message": "Inicio de sesión exitoso", "email": user.email}

@app.post("/api/auth/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(None, alias="session_token"), db: Session = Depends(get_db)):
    if session_token:
        db.query(SessionModel).filter(SessionModel.token == session_token).delete()
        db.commit()
    response.delete_cookie(key="session_token")
    return {"message": "Sesión cerrada correctamente"}

@app.get("/api/auth/me")
def get_me(current_user: UserModel = Depends(get_current_user)):
    return {"email": current_user.email, "id": current_user.id}

# --- ENDPOINTS DE CONVERSACIONES (PROTEGIDOS) ---
@app.get("/api/conversations")
def listar_conversaciones(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = db.query(ConversationModel).filter(ConversationModel.user_id == current_user.id).order_by(ConversationModel.updated_at.desc()).all()
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in convs]

@app.post("/api/conversations")
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

@app.delete("/api/conversations/{conv_id}")
def eliminar_conversacion(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = db.query(ConversationModel).filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada")
    db.delete(conv)
    db.commit()
    return {"message": "Conversación eliminada correctamente"}

# --- ENDPOINT DE CHAT CON GEMINI ---
@app.post("/chat-ejecutivo")
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

        # 1. Guardar mensaje del usuario
        user_msg = ChatMessageModel(conversation_id=conv_id, role="user", content=req.mensaje)
        db.add(user_msg)
        db.commit()

        # 2. Intentar contactar con Gemini (Manejo robusto de fallos)
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
            # El mensaje del usuario ya está guardado, pero la IA falló temporalmente
            respuesta_texto = "⚠️ El ejecutivo financiero está experimentando alta demanda en este momento. Tu mensaje fue guardado, por favor intenta de nuevo en unos segundos."

        # 3. Guardar respuesta del ejecutivo o aviso de fallo
        exec_msg = ChatMessageModel(conversation_id=conv_id, role="executive", content=respuesta_texto)
        db.add(exec_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()

        return {"conversation_id": conv_id, "respuesta": respuesta_texto}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error en chat ejecutivo: {str(e)}")

@app.post("/analizar-fraude")
def analizar_fraude(data: TransaccionInput, current_user: UserModel = Depends(get_current_user)):
    try:
        model = genai.GenerativeModel("gemini-3.8-flash")
        prompt = f"""
        Actúa como un oficial senior de ciberseguridad bancaria y experto en la Regulación E (12 CFR § 1005.11).
        Analiza esta transacción sospechosa para el usuario ID {current_user.id}:
        - Comercio: {data.comercio}
        - Monto: ${data.monto}
        - Ubicación: {data.ubicacion}
        - Categoría: {data.categoria}
        - Hora: {data.hora}
        """
        response = model.generate_content(prompt)
        return {"analisis_centinela": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en motor de IA: {str(e)}")

@app.post("/generar-disputa-reg-e")
def generar_disputa(data: TransaccionInput, current_user: UserModel = Depends(get_current_user)):
    try:
        model = genai.GenerativeModel("gemini-3.8-flash")
        prompt = f"""
        Redacta un documento formal de disputa y notificación de cargo no autorizado bajo Regulación E (12 CFR § 1005.11).
        - Usuario ID: {current_user.id}
        - Comercio: {data.comercio}
        - Monto: ${data.monto}
        - Hora: {data.hora}
        - Ubicación: {data.ubicacion}
        """
        response = model.generate_content(prompt)
        return {"carta_formal_regulacion_e": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al redactar documento: {str(e)}")

# --- INTERFAZ FRONTEND SPA ---
@app.get("/", response_class=HTMLResponse)
def home():
    html_content = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
        <title>Bank Assistant | Secure Multi-User Fintech</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background-color: #F8FAFC;
                color: #0F172A;
                -webkit-font-smoothing: antialiased;
                padding-bottom: env(safe-area-inset-bottom);
            }
            .fintech-card {
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 20px;
                box-shadow: 0 2px 12px rgba(15, 23, 42, 0.03);
            }
            .fintech-input {
                background: #F1F5F9;
                border: 1px solid #CBD5E1;
                color: #0F172A;
                transition: all 0.2s ease;
            }
            .fintech-input:focus {
                border-color: #0066CC;
                box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.12);
                outline: none;
                background: #FFFFFF;
            }
            .btn-primary {
                background: #0066CC;
                color: #FFFFFF;
                font-weight: 600;
                transition: background 0.2s ease, transform 0.1s ease;
            }
            .btn-primary:active { transform: scale(0.98); }
            .btn-primary:hover { background: #0052A3; }
            .btn-secondary {
                background: #F1F5F9;
                color: #334155;
                font-weight: 600;
                border: 1px solid #E2E8F0;
                transition: background 0.2s ease;
            }
            .btn-secondary:hover { background: #E2E8F0; }
            .nav-item.active { color: #0066CC; }
            .nav-item { color: #94A3B8; transition: color 0.2s ease; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
            
            .chat-msg-user {
                background-color: #0066CC;
                color: #FFFFFF;
                border-radius: 18px 18px 4px 18px;
            }
            .chat-msg-executive {
                background-color: #FFFFFF;
                color: #1E293B;
                border: 1px solid #E2E8F0;
                border-radius: 18px 18px 18px 4px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.02);
            }
            .no-scrollbar::-webkit-scrollbar { display: none; }
            .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
        </style>
    </head>
    <body class="min-h-screen pb-32 pt-4 px-4 flex flex-col items-center">
        
        <!-- VISTA DE AUTENTICACIÓN (LOGIN / REGISTER) -->
        <div id="auth-view" class="w-full max-w-[390px] flex flex-col justify-center min-h-[85vh]">
            <div class="fintech-card p-6 space-y-5 bg-white">
                <div class="text-center">
                    <div class="h-12 w-12 rounded-2xl bg-blue-600 text-white font-bold text-xl flex items-center justify-center mx-auto mb-3 shadow-md">
                        BA
                    </div>
                    <h1 class="text-lg font-bold text-slate-900">Bank Assistant</h1>
                    <p class="text-xs text-slate-500 mt-1">Plataforma Fintech Segura & IA</p>
                </div>

                <div id="auth-error" class="hidden p-3 bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl"></div>

                <form id="auth-form" onsubmit="handleAuthSubmit(event)" class="space-y-3 text-xs">
                    <div>
                        <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Correo Electrónico</label>
                        <input type="email" id="auth-email" required class="w-full fintech-input rounded-xl p-3 text-xs" placeholder="nombre@correo.com">
                    </div>
                    <div>
                        <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Contraseña</label>
                        <input type="password" id="auth-password" required class="w-full fintech-input rounded-xl p-3 text-xs" placeholder="••••••••">
                    </div>
                    
                    <button type="submit" id="auth-submit-btn" class="w-full btn-primary py-3 rounded-xl text-xs mt-2">
                        Iniciar Sesión
                    </button>
                </form>

                <div class="text-center pt-2 border-t border-slate-100">
                    <button onclick="toggleAuthMode()" id="auth-toggle-btn" class="text-xs text-blue-600 hover:text-blue-700 font-semibold">
                        ¿No tienes cuenta? Regístrate aquí
                    </button>
                </div>
            </div>
        </div>

        <!-- VISTA PRINCIPAL DE LA APLICACIÓN (OCULTA HASTA AUTENTICARSE) -->
        <div id="app-view" class="w-full max-w-[390px] relative hidden">
            
            <!-- ENCABEZADO GLOBAL -->
            <header class="flex items-center justify-between mb-5 px-1">
                <div class="flex items-center gap-3">
                    <div class="relative">
                        <div class="h-10 w-10 rounded-full bg-slate-900 flex items-center justify-center text-white font-bold text-sm shadow-sm border border-slate-700" id="user-initials">
                            US
                        </div>
                        <div class="absolute -bottom-0.5 -right-0.5 h-3.5 w-3.5 bg-emerald-500 rounded-full border-2 border-white" title="Sesión Segura"></div>
                    </div>
                    <div>
                        <p class="text-[11px] text-slate-500 font-medium">Conectado como,</p>
                        <h1 id="user-email-display" class="text-xs font-bold text-slate-900 tracking-tight truncate max-w-[160px]">usuario@banco.com</h1>
                    </div>
                </div>
                
                <div class="flex items-center gap-2">
                    <button onclick="cerrarSesion()" class="p-2 rounded-full bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 transition shadow-sm text-xs font-semibold px-3 flex items-center gap-1">
                        <span>🚪</span> Salir
                    </button>
                </div>
            </header>

            <!-- BOTÓN FLOTANTE ADAPTATIVO -->
            <div id="floating-executive-btn" class="fixed bottom-24 right-4 z-40 max-w-[390px] transition-all duration-300">
                <button onclick="switchTab('ia')" class="bg-blue-600 hover:bg-blue-700 text-white rounded-full p-3.5 shadow-xl border border-blue-400/30 flex items-center gap-2 transition-all transform active:scale-95">
                    <span class="text-base">✦</span>
                    <span class="text-xs font-semibold pr-1">Habla con tu ejecutivo</span>
                </button>
            </div>

            <!-- CONTENEDOR DE PANTALLAS (TABS) -->
            <main class="w-full">

                <!-- 1. TAB HOME -->
                <section id="tab-home" class="tab-content active space-y-4">
                    <div class="fintech-card p-5 bg-white border border-slate-200 shadow-sm relative overflow-hidden">
                        <div class="flex justify-between items-center mb-3">
                            <span class="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Dinero Disponible</span>
                            <button onclick="togglePrivacy()" id="privacy-btn" class="text-slate-400 hover:text-slate-600 text-xs flex items-center gap-1 bg-slate-50 border border-slate-200 px-2.5 py-1 rounded-full">
                                <span>👁️</span> <span id="privacy-label" class="text-[10px]">Ocultar</span>
                            </button>
                        </div>
                        
                        <div class="mb-4">
                            <h2 id="saldo-val" class="text-3xl font-extrabold text-slate-900 tracking-tight">$12,480.50</h2>
                            <p class="text-[11px] text-emerald-600 font-medium mt-1 flex items-center gap-1">
                                <span>↑ +8.4%</span> <span class="text-slate-400">vs. mes anterior</span>
                            </p>
                        </div>

                        <div class="grid grid-cols-2 gap-3 pt-3 border-t border-slate-100 text-xs">
                            <div class="bg-slate-50 p-2.5 rounded-xl border border-slate-100">
                                <span class="text-slate-500 text-[10px] uppercase font-semibold block">Ingresos (Mes)</span>
                                <span id="ingresos-val" class="font-bold text-emerald-700 text-sm">+$4,250.00</span>
                            </div>
                            <div class="bg-slate-50 p-2.5 rounded-xl border border-slate-100">
                                <span class="text-slate-500 text-[10px] uppercase font-semibold block">Gastos (Mes)</span>
                                <span id="gastos-val" class="font-bold text-slate-800 text-sm">-$1,820.30</span>
                            </div>
                        </div>
                    </div>

                    <div class="fintech-card p-4 bg-gradient-to-r from-blue-50 to-indigo-50/50 border border-blue-200/80">
                        <div class="flex items-start gap-3">
                            <div class="h-8 w-8 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-xs shrink-0 mt-0.5">
                                ✦
                            </div>
                            <div class="flex-1">
                                <span class="text-[10px] font-bold uppercase tracking-wider text-blue-700">Tu ejecutivo recomienda</span>
                                <h3 class="text-xs font-bold text-slate-900 mt-0.5">Optimización de flujo detectada</h3>
                                <p class="text-[11px] text-slate-600 mt-1 leading-relaxed">Analizamos tus cuentas vinculadas y detectamos un potencial de ahorro mensual.</p>
                                <button onclick="enviarSugerencia('Analizar cómo mejorar mi flujo de efectivo')" class="mt-2.5 bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-semibold px-3 py-1.5 rounded-lg shadow-sm transition">
                                    Ver análisis completo →
                                </button>
                            </div>
                        </div>
                    </div>
                </section>

                <!-- 2. TAB EJECUTIVO IA -->
                <section id="tab-ia" class="tab-content space-y-4">
                    <div class="fintech-card p-5 flex flex-col h-[520px]">
                        <div class="flex items-center justify-between pb-3 border-b border-slate-100 mb-3">
                            <div class="flex items-center gap-3">
                                <div class="h-9 w-9 rounded-full bg-blue-600 flex items-center justify-center text-white font-bold text-sm">
                                    ✦
                                </div>
                                <div>
                                    <h2 class="text-xs font-bold text-slate-900">Ejecutivo Financiero</h2>
                                    <div class="flex items-center gap-1.5 mt-0.5">
                                        <div class="h-1.5 w-1.5 rounded-full bg-emerald-500"></div>
                                        <span class="text-[10px] text-slate-500 font-medium">Aislamiento Seguro (Multi-user)</span>
                                    </div>
                                </div>
                            </div>
                            <div class="flex gap-2">
                                <button onclick="eliminarConversacionActual()" title="Eliminar conversación" class="text-xs text-red-500 hover:text-red-700 font-semibold bg-red-50 px-2.5 py-1 rounded-lg">
                                    🗑️ Borrar
                                </button>
                                <button onclick="nuevaConversacion()" class="text-xs text-blue-600 hover:text-blue-700 font-semibold bg-blue-50 px-2.5 py-1 rounded-lg">
                                    + Nueva
                                </button>
                            </div>
                        </div>

                        <!-- Selector de Conversaciones Activas -->
                        <div id="conversations-bar" class="pb-2 overflow-x-auto flex gap-1.5 no-scrollbar border-b border-slate-100 text-[11px]">
                            <!-- Se llena dinámicamente -->
                        </div>

                        <div id="chat-messages" class="flex-1 overflow-y-auto space-y-3 pr-1 text-xs mt-2">
                            <div class="chat-msg-executive p-3.5">
                                <p class="font-medium text-slate-800">Hola. Soy tu ejecutivo financiero personal seguro.</p>
                                <p class="mt-1 text-slate-600">Tus datos están protegidos bajo estricto aislamiento de sesión. ¿Qué deseas consultar hoy?</p>
                            </div>
                        </div>

                        <div class="py-2 overflow-x-auto flex gap-2 no-scrollbar border-t border-slate-100 mt-2">
                            <button onclick="enviarSugerencia('Analizar mis gastos')" class="whitespace-nowrap px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-medium rounded-full transition">
                                📊 Analizar mis gastos
                            </button>
                            <button onclick="enviarSugerencia('¿Cómo puedo mejorar mi crédito?')" class="whitespace-nowrap px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 text-[11px] font-medium rounded-full transition">
                                💳 Mejorar crédito
                            </button>
                            <button onclick="enviarSugerencia('Auditar transacción sospechosa Reg E')" class="whitespace-nowrap px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 text-[11px] font-medium rounded-full transition">
                                🛡️ Auditar Fraude
                            </button>
                        </div>

                        <div class="pt-2 flex gap-2">
                            <input type="text" id="chat-input" placeholder="Escribe a tu ejecutivo..." onkeypress="handleKeyPress(event)" class="flex-1 fintech-input rounded-xl px-3.5 py-2.5 text-xs">
                            <button onclick="enviarMensajeChat()" id="send-chat-btn" class="btn-primary px-4 rounded-xl text-xs flex items-center justify-center">
                                Enviar
                            </button>
                        </div>
                    </div>

                    <!-- Panel Manual Reg E -->
                    <details class="fintech-card p-4 text-xs">
                        <summary class="font-semibold text-slate-700 cursor-pointer flex justify-between items-center">
                            <span>🛠️ Panel Manual de Fraudes & Disputas Reg E</span>
                            <span class="text-blue-600 text-xs">Abrir</span>
                        </summary>
                        <form class="space-y-3 mt-3 pt-3 border-t border-slate-100">
                            <div>
                                <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Comercio / Destino</label>
                                <input type="text" id="comercio" value="Zelle Transfer / Electronics Store" class="w-full fintech-input rounded-lg p-2 text-xs">
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Monto ($ USD)</label>
                                    <input type="number" step="0.01" id="monto" value="485.50" class="w-full fintech-input rounded-lg p-2 text-xs">
                                </div>
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Categoría</label>
                                    <input type="text" id="categoria" value="Transferencia no autorizada" class="w-full fintech-input rounded-lg p-2 text-xs">
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-2">
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Ubicación / IP</label>
                                    <input type="text" id="ubicacion" value="Miami, FL" class="w-full fintech-input rounded-lg p-2 text-xs">
                                </div>
                                <div>
                                    <label class="block text-[10px] font-semibold text-slate-500 uppercase mb-1">Hora del Reporte</label>
                                    <input type="text" id="hora" value="23:15 PST" class="w-full fintech-input rounded-lg p-2 text-xs">
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-2 pt-1">
                                <button type="button" onclick="ejecutarAuditoria('/analizar-fraude')" class="btn-primary py-2 rounded-lg text-xs">
                                    Evaluar Riesgo
                                </button>
                                <button type="button" onclick="ejecutarAuditoria('/generar-disputa-reg-e')" class="btn-secondary py-2 rounded-lg text-xs">
                                    Carta Reg E
                                </button>
                            </div>
                        </form>
                    </details>
                </section>

                <!-- 3. TAB CRÉDITO -->
                <section id="tab-credito" class="tab-content space-y-4">
                    <div class="fintech-card p-5 bg-gradient-to-br from-slate-900 to-blue-950 text-white shadow-sm">
                        <div class="flex justify-between items-start mb-3">
                            <div>
                                <span class="text-[10px] uppercase tracking-wider text-blue-300 font-semibold">Perfil Crediticio</span>
                                <h2 class="text-3xl font-extrabold mt-1">745 <span class="text-xs font-normal text-emerald-400">● Excelente</span></h2>
                            </div>
                            <span class="text-[10px] bg-white/10 px-2 py-0.5 rounded-md backdrop-blur-md">Modelo Experian</span>
                        </div>
                        <p class="text-[11px] text-slate-300 mb-3">Utilización de crédito actual: 14%.</p>
                    </div>
                </section>

                <!-- 4. TAB PRÉSTAMOS -->
                <section id="tab-prestamos" class="tab-content space-y-4">
                    <div class="fintech-card p-5 bg-white border border-slate-200 shadow-sm">
                        <h2 class="text-xs font-bold text-slate-900 uppercase tracking-wider mb-3">Simulador de Préstamo Personal</h2>
                        <div class="space-y-3 text-xs">
                            <div>
                                <div class="flex justify-between font-medium text-slate-700 mb-1">
                                    <span>Monto Solicitado:</span>
                                    <span id="label-monto" class="font-bold text-blue-600">$15,000</span>
                                </div>
                                <input type="range" id="loan-amount" min="5000" max="50000" step="1000" value="15000" oninput="calcularPrestamo()" class="w-full accent-blue-600 cursor-pointer">
                            </div>
                            <div class="bg-slate-50 p-3.5 rounded-xl border border-slate-200 flex justify-between items-center mt-3">
                                <div>
                                    <span class="text-[10px] text-slate-500 block uppercase font-semibold">Pago Estimado Mensual</span>
                                    <span id="display-pago" class="text-base font-extrabold text-slate-900">$463.20</span>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                <!-- 5. TAB PERFIL -->
                <section id="tab-perfil" class="tab-content space-y-4">
                    <div class="fintech-card p-6">
                        <h2 class="text-base font-bold text-slate-900 mb-2">Configuración y Seguridad</h2>
                        <div class="space-y-2 text-xs">
                            <div class="p-3.5 bg-slate-50 rounded-xl flex justify-between items-center">
                                <span class="font-medium text-slate-700">Estado de Sesión</span>
                                <span class="font-bold text-emerald-600">Autenticado & Encriptado</span>
                            </div>
                        </div>
                    </div>
                </section>

            </main>

            <!-- BARRA DE NAVEGACIÓN INFERIOR -->
            <nav class="fixed bottom-0 left-0 right-0 bg-white/90 backdrop-blur-md border-t border-slate-200 py-2.5 px-6 flex justify-between items-center z-50 shadow-lg max-w-[390px] mx-auto">
                <button onclick="switchTab('home')" id="btn-home" class="nav-item active flex flex-col items-center gap-1">
                    <span class="text-lg">🏠</span>
                    <span class="text-[10px] font-semibold">Home</span>
                </button>
                <button onclick="switchTab('ia')" id="btn-ia" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-lg">✦</span>
                    <span class="text-[10px] font-semibold">Ejecutivo IA</span>
                </button>
                <button onclick="switchTab('credito')" id="btn-credito" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-lg">💳</span>
                    <span class="text-[10px] font-semibold">Crédito</span>
                </button>
                <button onclick="switchTab('prestamos')" id="btn-prestamos" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-lg">📊</span>
                    <span class="text-[10px] font-semibold">Préstamos</span>
                </button>
                <button onclick="switchTab('perfil')" id="btn-perfil" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-lg">⚙️</span>
                    <span class="text-[10px] font-semibold">Perfil</span>
                </button>
            </nav>

        </div>

        <script>
            let isLoginMode = true;
            let currentConversationId = null;
            let currentUser = null;

            window.addEventListener('DOMContentLoaded', async () => {
                await checkAuthStatus();
            });

            async function checkAuthStatus() {
                try {
                    const res = await fetch('/api/auth/me');
                    if (res.ok) {
                        const data = await res.json();
                        currentUser = data;
                        document.getElementById('user-email-display').innerText = data.email;
                        document.getElementById('user-initials').innerText = data.email.substring(0, 2).toUpperCase();
                        
                        document.getElementById('auth-view').classList.add('hidden');
                        document.getElementById('app-view').classList.remove('hidden');
                        
                        await cargarConversaciones();
                    } else {
                        mostrarAuthView();
                    }
                } catch (e) {
                    mostrarAuthView();
                }
            }

            function mostrarAuthView() {
                document.getElementById('app-view').classList.add('hidden');
                document.getElementById('auth-view').classList.remove('hidden');
                currentConversationId = null;
            }

            function toggleAuthMode() {
                isLoginMode = !isLoginMode;
                const titleBtn = document.getElementById('auth-submit-btn');
                const toggleBtn = document.getElementById('auth-toggle-btn');
                
                if (isLoginMode) {
                    titleBtn.innerText = "Iniciar Sesión";
                    toggleBtn.innerText = "¿No tienes cuenta? Regístrate aquí";
                } else {
                    titleBtn.innerText = "Registrarme";
                    toggleBtn.innerText = "¿Ya tienes cuenta? Inicia sesión";
                }
                document.getElementById('auth-error').classList.add('hidden');
            }

            async function handleAuthSubmit(e) {
                e.preventDefault();
                const email = document.getElementById('auth-email').value;
                const password = document.getElementById('auth-password').value;
                const endpoint = isLoginMode ? '/api/auth/login' : '/api/auth/register';
                
                const errBox = document.getElementById('auth-error');
                errBox.classList.add('hidden');

                try {
                    const res = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ email, password })
                    });
                    const data = await res.json();

                    if (!res.ok) {
                        throw new Error(data.detail || "Error en la autenticación");
                    }

                    if (isLoginMode) {
                        await checkAuthStatus();
                    } else {
                        alert("¡Cuenta creada con éxito! Ahora puedes iniciar sesión.");
                        toggleAuthMode();
                    }
                } catch (err) {
                    errBox.innerText = err.message;
                    errBox.classList.remove('hidden');
                }
            }

            async function cerrarSesion() {
                try {
                    await fetch('/api/auth/logout', { method: 'POST' });
                    currentUser = null;
                    mostrarAuthView();
                } catch (e) {
                    console.error("Error al cerrar sesión", e);
                }
            }

            async function cargarConversaciones() {
                try {
                    const res = await fetch('/api/conversations');
                    if (res.ok) {
                        const convs = await res.json();
                        const bar = document.getElementById('conversations-bar');
                        bar.innerHTML = '';
                        
                        if (convs.length > 0) {
                            convs.forEach(c => {
                                const btn = document.createElement('button');
                                btn.className = `whitespace-nowrap px-3 py-1 rounded-lg font-medium transition ${c.id === currentConversationId ? 'bg-blue-600 text-white' : 'bg-slate-100 hover:bg-slate-200 text-slate-700'}`;
                                btn.innerText = c.title;
                                btn.onclick = () => seleccionarConversacion(c.id);
                                bar.appendChild(btn);
                            });
                            if (!currentConversationId) {
                                currentConversationId = convs[0].id;
                            }
                            await cargarMensajes(currentConversationId);
                        } else {
                            await nuevaConversacion();
                        }
                    }
                } catch (e) {
                    console.error("Error cargando conversaciones", e);
                }
            }

            async function seleccionarConversacion(id) {
                currentConversationId = id;
                await cargarConversaciones();
                await cargarMensajes(id);
            }

            async function nuevaConversacion() {
                try {
                    const res = await fetch('/api/conversations', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: 'Nueva Asesoría' })
                    });
                    if (res.ok) {
                        const conv = await res.json();
                        currentConversationId = conv.id;
                        await cargarConversaciones();
                        document.getElementById('chat-messages').innerHTML = `
                            <div class="chat-msg-executive p-3.5">
                                <p class="font-medium text-slate-800">Nueva sesión segura iniciada.</p>
                                <p class="mt-1 text-slate-600">¿Qué deseas consultar hoy?</p>
                            </div>
                        `;
                    }
                } catch (e) {
                    console.error("Error creando conversación", e);
                }
            }

            async function eliminarConversacionActual() {
                if (!currentConversationId) return;
                if (!confirm("¿Deseas eliminar esta conversación de forma permanente?")) return;

                try {
                    const res = await fetch(`/api/conversations/${currentConversationId}`, { method: 'DELETE' });
                    if (res.ok) {
                        currentConversationId = null;
                        await cargarConversaciones();
                    }
                } catch (e) {
                    console.error("Error al eliminar conversación", e);
                }
            }

            async function cargarMensajes(convId) {
                try {
                    const res = await fetch(`/api/conversations/${convId}/messages`);
                    if (res.ok) {
                        const messages = await res.json();
                        const container = document.getElementById('chat-messages');
                        container.innerHTML = '';
                        if (messages.length === 0) {
                            container.innerHTML = `
                                <div class="chat-msg-executive p-3.5">
                                    <p class="font-medium text-slate-800">Hola. Soy tu ejecutivo financiero personal.</p>
                                    <p class="mt-1 text-slate-600">¿Qué te gustaría revisar hoy?</p>
                                </div>
                            `;
                            return;
                        }
                        messages.forEach(msg => {
                            const div = document.createElement('div');
                            if (msg.role === 'user') {
                                div.className = 'chat-msg-user p-3 ml-8 text-xs leading-relaxed self-end';
                                div.innerText = msg.content;
                            } else {
                                div.className = 'chat-msg-executive p-3 mr-8 text-xs leading-relaxed whitespace-pre-wrap';
                                div.innerText = msg.content;
                            }
                            container.appendChild(div);
                        });
                        container.scrollTop = container.scrollHeight;
                    }
                } catch (e) {
                    console.error("Error cargando mensajes", e);
                }
            }

            function togglePrivacy() {
                saldosOcultos = !saldosOcultos;
                document.getElementById('saldo-val').innerText = saldosOcultos ? '••••••••' : '$12,480.50';
                document.getElementById('ingresos-val').innerText = saldosOcultos ? '••••••' : '+$4,250.00';
                document.getElementById('gastos-val').innerText = saldosOcultos ? '••••••' : '-$1,820.30';
                document.getElementById('privacy-label').innerText = saldosOcultos ? 'Mostrar' : 'Ocultar';
            }

            function calcularPrestamo() {
                const monto = parseFloat(document.getElementById('loan-amount').value) || 15000;
                const pagoMensual = (monto * 0.0699 / 12 * Math.pow(1 + 0.0699/12, 36)) / (Math.pow(1 + 0.0699/12, 36) - 1);
                document.getElementById('label-monto').innerText = '$' + monto.toLocaleString();
                document.getElementById('display-pago').innerText = '$' + pagoMensual.toFixed(2);
            }

            function switchTab(tabId) {
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
                document.getElementById('tab-' + tabId).classList.add('active');
                document.getElementById('btn-' + tabId).classList.add('active');

                const floatingBtn = document.getElementById('floating-executive-btn');
                if (tabId === 'ia') {
                    floatingBtn.style.transform = 'translateY(100px)';
                    floatingBtn.style.opacity = '0';
                    setTimeout(() => floatingBtn.classList.add('hidden'), 300);
                } else {
                    floatingBtn.classList.remove('hidden');
                    setTimeout(() => {
                        floatingBtn.style.transform = 'translateY(0)';
                        floatingBtn.style.opacity = '1';
                    }, 10);
                }
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }

            function handleKeyPress(e) {
                if (e.key === 'Enter') enviarMensajeChat();
            }

            function enviarSugerencia(texto) {
                switchTab('ia');
                document.getElementById('chat-input').value = texto;
                enviarMensajeChat();
            }

            async function enviarMensajeChat() {
                const input = document.getElementById('chat-input');
                const texto = input.value.trim();
                if (!texto) return;

                appendUIMessage('user', texto);
                input.value = '';

                const sendBtn = document.getElementById('send-chat-btn');
                sendBtn.disabled = true;

                const loadingId = appendUIMessage('executive', 'Pensando...', true);

                try {
                    const res = await fetch('/chat-ejecutivo', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mensaje: texto, conversation_id: currentConversationId })
                    });
                    
                    if (!res.ok) throw new Error(`Error en servidor (${res.status})`);

                    const data = await res.json();
                    currentConversationId = data.conversation_id;
                    document.getElementById(loadingId)?.remove();
                    appendUIMessage('executive', data.respuesta);
                    await cargarConversaciones();
                } catch (err) {
                    document.getElementById(loadingId)?.remove();
                    appendUIMessage('executive', "Error de conexión: " + err.message);
                } finally {
                    sendBtn.disabled = false;
                }
            }

            function appendUIMessage(role, text, isLoading = false) {
                const container = document.getElementById('chat-messages');
                const msgDiv = document.createElement('div');
                const id = 'msg-' + Date.now();
                msgDiv.id = id;

                if (role === 'user') {
                    msgDiv.className = 'chat-msg-user p-3 ml-8 text-xs leading-relaxed self-end';
                    msgDiv.innerText = text;
                } else {
                    msgDiv.className = 'chat-msg-executive p-3 mr-8 text-xs leading-relaxed whitespace-pre-wrap';
                    if (isLoading) msgDiv.classList.add('animate-pulse', 'text-slate-400');
                    msgDiv.innerText = text;
                }

                container.appendChild(msgDiv);
                container.scrollTop = container.scrollHeight;
                return id;
            }

            async function ejecutarAuditoria(endpoint) {
                switchTab('ia');
                const payload = {
                    comercio: document.getElementById('comercio').value,
                    monto: parseFloat(document.getElementById('monto').value),
                    ubicacion: document.getElementById('ubicacion').value,
                    categoria: document.getElementById('categoria').value,
                    hora: document.getElementById('hora').value
                };

                appendUIMessage('user', "Auditar mediante endpoint: " + endpoint);
                const loadingId = appendUIMessage('executive', "Analizando con modelo Gemini...", true);

                try {
                    const res = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    if (!res.ok) throw new Error(`Error (${res.status})`);
                    const data = await res.json();
                    document.getElementById(loadingId)?.remove();
                    appendUIMessage('executive', data.analisis_centinela || data.carta_formal_regulacion_e);
                } catch (err) {
                    document.getElementById(loadingId)?.remove();
                    appendUIMessage('executive', "Error en auditoría: " + err.message);
                }
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)
