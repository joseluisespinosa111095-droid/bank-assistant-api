import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import List, Optional
import google.generativeai as genai

app = FastAPI(
    title="Bank Assistant - Compliance & AI Executive Platform",
    version="3.5.0"
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

class TransaccionInput(BaseModel):
    comercio: str
    monto: float
    ubicacion: str
    categoria: str
    hora: str
    usuario_id: str = "cliente_principal"

class ChatMessage(BaseModel):
    sender: str  # "user" o "executive"
    text: str

class ChatRequest(BaseModel):
    mensaje: str
    historial: Optional[List[ChatMessage]] = []

@app.get("/", response_class=HTMLResponse)
def home():
    html_content = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Bank Assistant | Credit, Loans & AI Executive</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background-color: #F8FAFC;
                color: #0F172A;
                -webkit-font-smoothing: antialiased;
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
    <body class="min-h-screen pb-28 pt-4 px-4 flex flex-col items-center">
        <div class="w-full max-w-[390px] relative">
            
            <!-- ENCABEZADO GLOBAL -->
            <header class="flex items-center justify-between mb-5 px-1">
                <div class="flex items-center gap-3">
                    <div class="relative">
                        <div class="h-10 w-10 rounded-full bg-slate-900 flex items-center justify-center text-white font-bold text-sm shadow-sm border border-slate-700">
                            LE
                        </div>
                        <div class="absolute -bottom-0.5 -right-0.5 h-3.5 w-3.5 bg-emerald-500 rounded-full border-2 border-white" title="Cuenta verificada"></div>
                    </div>
                    <div>
                        <p class="text-[11px] text-slate-500 font-medium">Hola de nuevo,</p>
                        <h1 class="text-sm font-bold text-slate-900 tracking-tight">Luis Espinosa</h1>
                    </div>
                </div>
                
                <div class="flex items-center gap-2">
                    <button class="relative p-2 rounded-full bg-white border border-slate-200 text-slate-600 hover:bg-slate-50 transition shadow-sm">
                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path></svg>
                        <span class="absolute top-1 right-1 h-2 w-2 rounded-full bg-blue-600 animate-ping"></span>
                        <span class="absolute top-1 right-1 h-2 w-2 rounded-full bg-blue-600"></span>
                    </button>
                </div>
            </header>

            <!-- BOTÓN FLOTANTE: HABLA CON TU EJECUTIVO -->
            <div id="floating-executive-btn" class="fixed bottom-20 right-4 z-40 max-w-[390px]">
                <button onclick="switchTab('ia')" class="bg-blue-600 hover:bg-blue-700 text-white rounded-full p-3.5 shadow-xl border border-blue-400/30 flex items-center gap-2 transition-all transform active:scale-95">
                    <span class="text-base">✦</span>
                    <span id="floating-btn-text" class="text-xs font-semibold pr-1">Habla con tu ejecutivo</span>
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
                                <h3 class="text-xs font-bold text-slate-900 mt-0.5">Podrías mejorar tu flujo de efectivo este mes</h3>
                                <p class="text-[11px] text-slate-600 mt-1 leading-relaxed">Detectamos un ahorro potencial de $140 USD al consolidar tus suscripciones digitales activas.</p>
                                <button onclick="enviarSugerencia('Analizar cómo mejorar mi flujo de efectivo')" class="mt-2.5 bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-semibold px-3 py-1.5 rounded-lg shadow-sm transition">
                                    Ver análisis completo →
                                </button>
                            </div>
                        </div>
                    </div>

                    <div class="fintech-card p-5">
                        <div class="flex justify-between items-center mb-3">
                            <h3 class="text-xs font-bold text-slate-900 uppercase tracking-wider">Actividad Financiera</h3>
                            <span class="text-[10px] text-slate-400 font-medium">Octubre 2026</span>
                        </div>

                        <div class="bg-slate-50 p-3 rounded-xl border border-slate-100 mb-4">
                            <div class="flex justify-between text-[10px] text-slate-500 font-medium mb-1.5">
                                <span>Comparativo de Flujo</span>
                                <span>70% Ingresos / 30% Gastos</span>
                            </div>
                            <div class="h-2.5 w-full bg-slate-200 rounded-full overflow-hidden flex">
                                <div class="bg-emerald-500 h-full" style="width: 70%"></div>
                                <div class="bg-blue-600 h-full" style="width: 30%"></div>
                            </div>
                        </div>

                        <div class="space-y-2.5 text-xs">
                            <div class="flex justify-between items-center py-1">
                                <div class="flex items-center gap-2.5">
                                    <span class="p-2 rounded-lg bg-slate-100 text-slate-600 text-xs">🛒</span>
                                    <div>
                                        <p class="font-bold text-slate-800">Supermercado Trader Joe's</p>
                                        <p class="text-[10px] text-slate-400">Ayer • Comestibles</p>
                                    </div>
                                </div>
                                <span class="font-bold text-slate-900">-$85.40</span>
                            </div>
                            <div class="flex justify-between items-center py-1 border-t border-slate-100">
                                <div class="flex items-center gap-2.5">
                                    <span class="p-2 rounded-lg bg-emerald-50 text-emerald-600 text-xs">💵</span>
                                    <div>
                                        <p class="font-bold text-slate-800">Depósito Citibank Directo</p>
                                        <p class="text-[10px] text-slate-400">08 Oct • Nómina</p>
                                    </div>
                                </div>
                                <span class="font-bold text-emerald-600">+$2,125.00</span>
                            </div>
                        </div>

                        <button onclick="switchTab('ia')" class="w-full mt-3 text-center text-[11px] font-semibold text-blue-600 hover:text-blue-700 py-1">
                            Ver todas las transacciones →
                        </button>
                    </div>

                    <div class="fintech-card p-5">
                        <h3 class="text-xs font-bold text-slate-900 uppercase tracking-wider mb-3">Próximas Obligaciones</h3>
                        <div class="space-y-2 text-xs">
                            <div class="p-3 bg-slate-50 rounded-xl border border-slate-200/80 flex justify-between items-center">
                                <div>
                                    <div class="flex items-center gap-1.5">
                                        <h4 class="font-bold text-slate-900">Citibank Visa Signature</h4>
                                        <span class="text-[9px] bg-emerald-100 text-emerald-800 font-bold px-1.5 py-0.5 rounded">Confirmado</span>
                                    </div>
                                    <p class="text-[10px] text-slate-500 mt-0.5">Vence el 15 de Octubre</p>
                                </div>
                                <span class="font-bold text-slate-900">$240.00</span>
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
                                    <h2 class="text-xs font-bold text-slate-900">Tu Ejecutivo Financiero</h2>
                                    <div class="flex items-center gap-1.5 mt-0.5">
                                        <div class="h-1.5 w-1.5 rounded-full bg-emerald-500"></div>
                                        <span class="text-[10px] text-slate-500 font-medium">Gemini Core • En línea</span>
                                    </div>
                                </div>
                            </div>
                            <button onclick="reiniciarChat()" class="text-xs text-slate-400 hover:text-slate-600 font-medium">
                                Limpiar
                            </button>
                        </div>

                        <div id="chat-messages" class="flex-1 overflow-y-auto space-y-3 pr-1 text-xs">
                            <div class="chat-msg-executive p-3.5">
                                <p class="font-medium text-slate-800">Hola Luis. Soy tu ejecutivo financiero personal.</p>
                                <p class="mt-1 text-slate-600">Puedo ayudarte a entender tus gastos, comparar tarjetas, planificar préstamos y resolver disputas por fraudes bajo la Regulación E. ¿Qué te gustaría revisar hoy?</p>
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
                            <button onclick="enviarMensajeChat()" class="btn-primary px-4 rounded-xl text-xs flex items-center justify-center">
                                Enviar
                            </button>
                        </div>
                    </div>

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

                <!-- 3. TAB CRÉDITO (MARKETPLACE DE TARJETAS Y PERFIL) -->
                <section id="tab-credito" class="tab-content space-y-4">
                    <!-- Resumen Crediticio Experian Style -->
                    <div class="fintech-card p-5 bg-gradient-to-br from-slate-900 to-blue-950 text-white shadow-sm">
                        <div class="flex justify-between items-start mb-3">
                            <div>
                                <span class="text-[10px] uppercase tracking-wider text-blue-300 font-semibold">Perfil Crediticio</span>
                                <h2 class="text-3xl font-extrabold mt-1">745 <span class="text-xs font-normal text-emerald-400">● Excelente</span></h2>
                            </div>
                            <span class="text-[10px] bg-white/10 px-2 py-0.5 rounded-md backdrop-blur-md">Modelo Experian</span>
                        </div>
                        <p class="text-[11px] text-slate-300 mb-3">Tu utilización de crédito actual es del 14%, excelente para mantener tu puntuación al alza.</p>
                        
                        <div class="grid grid-cols-2 gap-2 pt-3 border-t border-white/10 text-xs">
                            <div>
                                <span class="text-slate-400 text-[10px] block">Historial Puntual</span>
                                <span class="font-bold text-emerald-400">100% (24 meses)</span>
                            </div>
                            <div>
                                <span class="text-slate-400 text-[10px] block">Límite Total</span>
                                <span class="font-bold text-white">$35,000.00</span>
                            </div>
                        </div>
                    </div>

                    <!-- Factores de Mejora -->
                    <div class="fintech-card p-4 bg-slate-50 border border-slate-200">
                        <h3 class="text-xs font-bold text-slate-900 uppercase tracking-wider mb-2">Factores Clave</h3>
                        <div class="space-y-2 text-xs">
                            <div class="flex items-center justify-between">
                                <span class="text-slate-600">Utilización de Crédito</span>
                                <span class="text-emerald-600 font-bold">Excelente (<30%)</span>
                            </div>
                            <div class="flex items-center justify-between">
                                <span class="text-slate-600">Antigüedad Promedio</span>
                                <span class="text-blue-600 font-bold">4 años 2 meses</span>
                            </div>
                        </div>
                    </div>

                    <!-- Marketplace de Tarjetas de Crédito -->
                    <div class="space-y-3">
                        <h3 class="text-xs font-bold text-slate-900 uppercase tracking-wider px-1">Marketplace de Tarjetas</h3>
                        
                        <!-- Tarjeta 1 -->
                        <div class="fintech-card p-4 bg-white border border-slate-200 shadow-sm">
                            <div class="flex justify-between items-start mb-2">
                                <div>
                                    <span class="text-[9px] uppercase font-bold bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded">98% Compatibilidad</span>
                                    <h4 class="text-xs font-bold text-slate-900 mt-1">Citibank Rewards+ Signature</h4>
                                </div>
                                <span class="text-xs font-bold text-blue-600">0% APR (12m)</span>
                            </div>
                            <p class="text-[11px] text-slate-600 mb-3">Gana 2X puntos en supermercados y gasolineras sin cuota anual.</p>
                            <div class="grid grid-cols-2 gap-2 pt-2 border-t border-slate-100 text-[10px] text-slate-500 mb-3">
                                <span>Cuota Anual: $0</span>
                                <span>Recompensa: 2% Cashback</span>
                            </div>
                            <button onclick="enviarSugerencia('Quiero solicitar información sobre Citibank Rewards+ Signature')" class="w-full btn-primary py-2 rounded-xl text-xs">
                                Solicitar Oferta Personalizada
                            </button>
                        </div>

                        <!-- Tarjeta 2 -->
                        <div class="fintech-card p-4 bg-white border border-slate-200 shadow-sm">
                            <div class="flex justify-between items-start mb-2">
                                <div>
                                    <span class="text-[9px] uppercase font-bold bg-blue-100 text-blue-800 px-2 py-0.5 rounded">92% Compatibilidad</span>
                                    <h4 class="text-xs font-bold text-slate-900 mt-1">Chase Sapphire Preferred</h4>
                                </div>
                                <span class="text-xs font-bold text-slate-700">21.24% APR</span>
                            </div>
                            <p class="text-[11px] text-slate-600 mb-3">Ideal para viajes y bonificación de bienvenida en puntos transferibles.</p>
                            <div class="grid grid-cols-2 gap-2 pt-2 border-t border-slate-100 text-[10px] text-slate-500 mb-3">
                                <span>Cuota Anual: $95</span>
                                <span>Recompensa: 3X Viajes</span>
                            </div>
                            <button onclick="enviarSugerencia('Quiero comparar Chase Sapphire Preferred')" class="w-full btn-secondary py-2 rounded-xl text-xs">
                                Ver Detalles y Requisitos
                            </button>
                        </div>
                    </div>
                </section>

                <!-- 4. TAB PRÉSTAMOS (CATÁLOGO Y SIMULADOR INTERACTIVO) -->
                <section id="tab-prestamos" class="tab-content space-y-4">
                    <!-- Simulador Interactivo de Préstamos -->
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

                            <div>
                                <div class="flex justify-between font-medium text-slate-700 mb-1">
                                    <span>Plazo (Meses):</span>
                                    <span id="label-plazo" class="font-bold text-blue-600">36 meses</span>
                                </div>
                                <input type="range" id="loan-term" min="12" max="60" step="6" value="36" oninput="calcularPrestamo()" class="w-full accent-blue-600 cursor-pointer">
                            </div>

                            <div class="bg-slate-50 p-3.5 rounded-xl border border-slate-200 flex justify-between items-center mt-3">
                                <div>
                                    <span class="text-[10px] text-slate-500 block uppercase font-semibold">Pago Estimado Mensual</span>
                                    <span id="display-pago" class="text-base font-extrabold text-slate-900">$463.20</span>
                                </div>
                                <div class="text-right">
                                    <span class="text-[10px] text-slate-500 block uppercase font-semibold">Costo Total (APR 6.99%)</span>
                                    <span id="display-total" class="text-xs font-bold text-blue-700">$16,675.20</span>
                                </div>
                            </div>

                            <button onclick="enviarSugerencia('Quiero aplicar para un préstamo simulado de $15,000 a 36 meses')" class="w-full btn-primary py-2.5 rounded-xl text-xs mt-2">
                                Iniciar Solicitud Oficial
                            </button>
                        </div>
                    </div>

                    <!-- Marketplace de Préstamos -->
                    <div class="space-y-3">
                        <h3 class="text-xs font-bold text-slate-900 uppercase tracking-wider px-1">Ofertas Multibanco</h3>
                        
                        <!-- Préstamo 1 -->
                        <div class="fintech-card p-4 bg-white border border-slate-200 shadow-sm">
                            <div class="flex justify-between items-start mb-2">
                                <div>
                                    <span class="text-[9px] uppercase font-bold bg-blue-100 text-blue-800 px-2 py-0.5 rounded">Pre-Aprobado</span>
                                    <h4 class="text-xs font-bold text-slate-900 mt-1">Citibank Liquidity Personal</h4>
                                </div>
                                <span class="text-xs font-bold text-emerald-600">Desde 6.99% APR</span>
                            </div>
                            <p class="text-[11px] text-slate-600 mb-3">Sin penalización por pago anticipado. Fondos depositados en 24 horas.</p>
                            <div class="grid grid-cols-2 gap-2 pt-2 border-t border-slate-100 text-[10px] text-slate-500 mb-3">
                                <span>Monto: Hasta $50,000</span>
                                <span>Plazos: 12 a 60 meses</span>
                            </div>
                            <button onclick="enviarSugerencia('Consultar detalles de Citibank Liquidity Personal')" class="w-full btn-primary py-2 rounded-xl text-xs">
                                Consultar Oferta
                            </button>
                        </div>
                    </div>
                </section>

                <!-- 5. TAB PERFIL -->
                <section id="tab-perfil" class="tab-content space-y-4">
                    <div class="fintech-card p-6">
                        <h2 class="text-base font-bold text-slate-900 mb-2">Configuración y Seguridad</h2>
                        <div class="space-y-2 text-xs">
                            <div class="p-3.5 bg-slate-50 rounded-xl flex justify-between items-center">
                                <span class="font-medium text-slate-700">Institución Vinculada</span>
                                <span class="font-bold text-slate-900">Citibank N.A.</span>
                            </div>
                        </div>
                    </div>
                </section>

            </main>

            <!-- BARRA DE NAVEGACIÓN INFERIOR (BOTTOM NAV) -->
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
            let historialChat = [];
            let saldosOcultos = false;

            function togglePrivacy() {
                saldosOcultos = !saldosOcultos;
                document.getElementById('saldo-val').innerText = saldosOcultos ? '••••••••' : '$12,480.50';
                document.getElementById('ingresos-val').innerText = saldosOcultos ? '••••••' : '+$4,250.00';
                document.getElementById('gastos-val').innerText = saldosOcultos ? '••••••' : '-$1,820.30';
                document.getElementById('privacy-label').innerText = saldosOcultos ? 'Mostrar' : 'Ocultar';
            }

            function calcularPrestamo() {
                const monto = parseFloat(document.getElementById('loan-amount').value) || 15000;
                const meses = parseInt(document.getElementById('loan-term').value) || 36;
                const apr = 0.0699; // 6.99% APR
                const tasaMensual = apr / 12;
                const pagoMensual = (monto * tasaMensual * Math.pow(1 + tasaMensual, meses)) / (Math.pow(1 + tasaMensual, meses) - 1);
                const costoTotal = pagoMensual * meses;

                document.getElementById('label-monto').innerText = '$' + monto.toLocaleString();
                document.getElementById('label-plazo').innerText = meses + ' meses';
                document.getElementById('display-pago').innerText = '$' + pagoMensual.toFixed(2);
                document.getElementById('display-total').innerText = '$' + costoTotal.toFixed(2);
            }

            function switchTab(tabId) {
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

                document.getElementById('tab-' + tabId).classList.add('active');
                document.getElementById('btn-' + tabId).classList.add('active');

                const floatingText = document.getElementById('floating-btn-text');
                if (tabId === 'ia') {
                    floatingText.classList.add('hidden');
                } else {
                    floatingText.classList.remove('hidden');
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

                appendMessage('user', texto);
                input.value = '';

                const loadingId = appendMessage('executive', 'Pensando...', true);

                try {
                    const res = await fetch('/chat-ejecutivo', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ mensaje: texto, historial: historialChat })
                    });
                    const data = await res.json();
                    
                    document.getElementById(loadingId)?.remove();
                    appendMessage('executive', data.respuesta);
                } catch (err) {
                    document.getElementById(loadingId)?.remove();
                    appendMessage('executive', "Error al conectar con el servidor: " + err.message);
                }
            }

            function appendMessage(sender, text, isLoading = false) {
                const container = document.getElementById('chat-messages');
                const msgDiv = document.createElement('div');
                const id = 'msg-' + Date.now();
                msgDiv.id = id;

                if (sender === 'user') {
                    msgDiv.className = 'chat-msg-user p-3 ml-8 text-xs leading-relaxed self-end';
                    msgDiv.innerText = text;
                    historialChat.push({ sender: "user", text: text });
                } else {
                    msgDiv.className = 'chat-msg-executive p-3 mr-8 text-xs leading-relaxed whitespace-pre-wrap';
                    if (isLoading) msgDiv.classList.add('animate-pulse', 'text-slate-400');
                    msgDiv.innerText = text;
                    if (!isLoading) historialChat.push({ sender: "executive", text: text });
                }

                container.appendChild(msgDiv);
                container.scrollTop = container.scrollHeight;
                return id;
            }

            function reiniciarChat() {
                historialChat = [];
                const container = document.getElementById('chat-messages');
                container.innerHTML = `
                    <div class="chat-msg-executive p-3.5">
                        <p class="font-medium text-slate-800">Conversación reiniciada.</p>
                        <p class="mt-1 text-slate-600">¿En qué puedo orientarte ahora respecto a tus finanzas o disputas?</p>
                    </div>
                `;
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

                appendMessage('user', "Auditar mediante endpoint: " + endpoint);
                const loadingId = appendMessage('executive', "Analizando con modelo Gemini...", true);

                try {
                    const res = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    document.getElementById(loadingId)?.remove();

                    if (data.analisis_centinela) {
                        appendMessage('executive', data.analisis_centinela);
                    } else if (data.carta_formal_regulacion_e) {
                        appendMessage('executive', data.carta_formal_regulacion_e);
                    }
                } catch (err) {
                    document.getElementById(loadingId)?.remove();
                    appendMessage('executive', "Error en auditoría: " + err.message);
                }
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/chat-ejecutivo")
def chat_ejecutivo(req: ChatRequest):
    try:
        model = genai.GenerativeModel("gemini-3.8-flash")
        
        contexto_financiero = """
        Eres el ejecutivo financiero personal del usuario (Luis Espinosa) en la plataforma Bank Assistant.
        Perfil del usuario:
        - Score Crediticio: 745 (Modelo Experian, Excelente).
        - Flujo mensual promedio: +$4,250.00 USD.
        - Tarjeta de crédito vinculada: Citibank Visa Signature (Utilización 14%).
        - Ley aplicable para reclamos de fraude: Regulación E (12 CFR § 1005.11).

        Directrices de comportamiento:
        - Tu tono es ejecutivo, altamente profesional, empático y orientado a la protección financiera.
        - Da respuestas claras, estructuradas y con próximos pasos concretos.
        - No inventes saldos adicionales ni afirmes que ejecutaste transferencias reales si no existen.
        - Cuando el usuario pregunte por tarjetas, préstamos o finanzas, oriéntalo con base en su perfil excelente de 745 puntos.
        """

        prompt = f"{contexto_financiero}\n\nMensaje del usuario: {req.mensaje}"
        response = model.generate_content(prompt)
        return {"respuesta": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en chat ejecutivo: {str(e)}")

@app.post("/analizar-fraude")
def analizar_fraude(data: TransaccionInput):
    try:
        model = genai.GenerativeModel("gemini-3.8-flash")
        prompt = f"""
        Actúa como un oficial senior de ciberseguridad bancaria y experto en la Regulación E (12 CFR § 1005.11).
        Analiza esta transacción sospechosa y entrega un diagnóstico estructurado:
        - Comercio / Destino: {data.comercio}
        - Monto: ${data.monto}
        - Ubicación: {data.ubicacion}
        - Categoría: {data.categoria}
        - Hora: {data.hora}

        Indica de forma clara y ejecutiva:
        1. Nivel de Riesgo (Bajo, Moderado, Crítico).
        2. Tipo de fraude probable (ej. Zelle Account Takeover, Skimmer en gasolinera, Suscripción trampa).
        3. Probabilidad de éxito en recuperación de fondos bajo ley federal.
        """
        response = model.generate_content(prompt)
        return {"analisis_centinela": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error en motor de IA: {str(e)}")

@app.post("/generar-disputa-reg-e")
def generar_disputa(data: TransaccionInput):
    try:
        model = genai.GenerativeModel("gemini-3.8-flash")
        prompt = f"""
        Redacta un documento formal de disputa y notificación de cargo no autorizado dirigido al departamento 
        de fraudes de una institución financiera en EE. UU., invocando los derechos del consumidor bajo 
        la Regulación E (12 CFR § 1005.11).
        
        Detalles del incidente:
        - Comercio afectado: {data.comercio}
        - Monto disputado: ${data.monto}
        - Fecha y Hora: {data.hora}
        - Contexto de ubicación: {data.ubicacion}

        El documento debe ser formal, exigir el reembolso provisional dentro del plazo legal de 10 días hábiles, 
        y mantener un tono legal estricto y profesional.
        """
        response = model.generate_content(prompt)
        return {"carta_formal_regulacion_e": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al redactar documento: {str(e)}")
