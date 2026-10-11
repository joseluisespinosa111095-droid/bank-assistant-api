import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI(
    title="Bank Assistant - Compliance & Fraud Platform",
    version="2.0.0"
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

@app.get("/", response_class=HTMLResponse)
def home():
    html_content = """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Bank Assistant | Experian & Reg E Standard</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background-color: #F4F6F9;
                color: #111827;
                -webkit-font-smoothing: antialiased;
            }
            .fintech-card {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 20px;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.03);
            }
            .fintech-input {
                background: #F9FAFB;
                border: 1px solid #D1D5DB;
                color: #111827;
                transition: all 0.2s ease;
            }
            .fintech-input:focus {
                border-color: #0066CC;
                box-shadow: 0 0 0 3px rgba(0, 102, 204, 0.15);
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
                background: #F3F4F6;
                color: #374151;
                font-weight: 600;
                border: 1px solid #E5E7EB;
                transition: background 0.2s ease;
            }
            .btn-secondary:hover { background: #E5E7EB; }
            .nav-item.active { color: #0066CC; }
            .nav-item { color: #9CA3AF; transition: color 0.2s ease; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
        </style>
    </head>
    <body class="min-h-screen pb-28 pt-4 px-4 md:px-8 flex flex-col items-center">
        <div class="max-w-md w-full">
            
            <!-- ENCABEZADO SUPERIOR -->
            <header class="flex items-center justify-between mb-6">
                <div class="flex items-center gap-3">
                    <div class="h-10 w-10 rounded-full bg-blue-600 flex items-center text-white font-bold justify-center shadow-md">
                        BA
                    </div>
                    <div>
                        <p class="text-xs text-gray-500 font-medium">Hola de nuevo,</p>
                        <h1 class="text-base font-bold text-gray-900 tracking-tight">Luis Espinosa</h1>
                    </div>
                </div>
                <div class="flex items-center gap-2">
                    <div class="bg-emerald-50 border border-emerald-200 px-3 py-1 rounded-full flex items-center gap-1.5">
                        <div class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></div>
                        <span class="text-[11px] font-semibold text-emerald-700">Protegido</span>
                    </div>
                </div>
            </header>

            <!-- CONTENEDOR DE PANTALLAS (TABS) -->
            <main class="w-full">

                <!-- 1. TAB HOME -->
                <section id="tab-home" class="tab-content active space-y-4">
                    <!-- Score Crediticio / Resumen Experian Style -->
                    <div class="fintech-card p-6 bg-gradient-to-br from-slate-900 to-blue-950 text-white shadow-lg">
                        <div class="flex justify-between items-start mb-4">
                            <div>
                                <span class="text-[11px] uppercase tracking-wider text-blue-300 font-semibold">Salud Financiera & Score</span>
                                <h2 class="text-3xl font-bold mt-1">745 <span class="text-xs font-normal text-emerald-400">● Excelente</span></h2>
                            </div>
                            <span class="text-xs bg-white/10 px-2.5 py-1 rounded-lg backdrop-blur-md">Experian Model</span>
                        </div>
                        <p class="text-xs text-slate-300 mb-4">Cero reportes de fraude activo. Tus derechos bajo Regulación E están vigentes.</p>
                        <div class="grid grid-cols-2 gap-3 pt-3 border-t border-white/10 text-xs">
                            <div>
                                <span class="text-slate-400 block">Flujo Mensual</span>
                                <span class="font-semibold text-white">+$4,250.00</span>
                            </div>
                            <div>
                                <span class="text-slate-400 block">Alertas Activas</span>
                                <span class="font-semibold text-emerald-400">Ninguna</span>
                            </div>
                        </div>
                    </div>

                    <!-- Alerta / Recomendación IA -->
                    <div class="fintech-card p-5 border-l-4 border-blue-600 bg-blue-50/50">
                        <div class="flex items-start gap-3">
                            <span class="text-xl">💡</span>
                            <div>
                                <h3 class="text-sm font-bold text-gray-900">Recomendación del Ejecutivo IA</h3>
                                <p class="text-xs text-gray-600 mt-1">Monitorea tus transferencias Zelle nocturnas. Utiliza la sección de Ejecutivo IA para auditar cualquier cargo desconocido inmediatamente.</p>
                            </div>
                        </div>
                    </div>

                    <!-- Accesos Rápidos -->
                    <div class="fintech-card p-5">
                        <h3 class="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3">Accesorios Financieros</h3>
                        <div class="grid grid-cols-4 gap-3 text-center">
                            <button onclick="switchTab('ia')" class="p-3 rounded-xl bg-blue-50 hover:bg-blue-100 transition flex flex-col items-center gap-1">
                                <span class="text-lg">🛡️</span>
                                <span class="text-[10px] font-semibold text-blue-700">Fraude</span>
                            </button>
                            <button onclick="switchTab('credito')" class="p-3 rounded-xl bg-gray-50 hover:bg-gray-100 transition flex flex-col items-center gap-1">
                                <span class="text-lg">💳</span>
                                <span class="text-[10px] font-semibold text-gray-700">Tarjetas</span>
                            </button>
                            <button onclick="switchTab('prestamos')" class="p-3 rounded-xl bg-gray-50 hover:bg-gray-100 transition flex flex-col items-center gap-1">
                                <span class="text-lg">📊</span>
                                <span class="text-[10px] font-semibold text-gray-700">Préstamos</span>
                            </button>
                            <button onclick="switchTab('perfil')" class="p-3 rounded-xl bg-gray-50 hover:bg-gray-100 transition flex flex-col items-center gap-1">
                                <span class="text-lg">⚙️</span>
                                <span class="text-[10px] font-semibold text-gray-700">Ajustes</span>
                            </button>
                        </div>
                    </div>
                </section>

                <!-- 2. TAB EJECUTIVO IA (Módulo de Fraude & Reg E) -->
                <section id="tab-ia" class="tab-content space-y-4">
                    <div class="fintech-card p-6">
                        <div class="flex items-center justify-between mb-4">
                            <h2 class="text-base font-bold text-gray-900 flex items-center gap-2">
                                <span>✦</span> Ejecutivo IA & Reg E
                            </h2>
                            <span class="text-[10px] bg-blue-100 text-blue-700 font-bold px-2.5 py-1 rounded-full">Gemini Core</span>
                        </div>
                        <p class="text-xs text-gray-500 mb-5">Analiza transacciones sospechosas y genera cartas de disputa legales basadas en 12 CFR § 1005.11.</p>

                        <form class="space-y-3.5">
                            <div>
                                <label class="block text-[11px] font-semibold text-gray-600 uppercase mb-1">Comercio / Destino</label>
                                <input type="text" id="comercio" value="Zelle Transfer / Electronics Store" class="w-full fintech-input rounded-xl p-3 text-xs" required>
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-[11px] font-semibold text-gray-600 uppercase mb-1">Monto ($ USD)</label>
                                    <input type="number" step="0.01" id="monto" value="485.50" class="w-full fintech-input rounded-xl p-3 text-xs" required>
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-gray-600 uppercase mb-1">Categoría</label>
                                    <input type="text" id="categoria" value="Transferencia no autorizada" class="w-full fintech-input rounded-xl p-3 text-xs" required>
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-[11px] font-semibold text-gray-600 uppercase mb-1">Ubicación / IP</label>
                                    <input type="text" id="ubicacion" value="Miami, FL" class="w-full fintech-input rounded-xl p-3 text-xs" required>
                                </div>
                                <div>
                                    <label class="block text-[11px] font-semibold text-gray-600 uppercase mb-1">Hora del Reporte</label>
                                    <input type="text" id="hora" value="23:15 PST" class="w-full fintech-input rounded-xl p-3 text-xs" required>
                                </div>
                            </div>

                            <div class="grid grid-cols-2 gap-3 pt-3">
                                <button type="button" onclick="ejecutarAccion('/analizar-fraude')" class="btn-primary py-3 px-4 rounded-xl text-xs shadow-md">
                                    Evaluar Riesgo
                                </button>
                                <button type="button" onclick="ejecutarAccion('/generar-disputa-reg-e')" class="btn-secondary py-3 px-4 rounded-xl text-xs">
                                    Disputa Legal
                                </button>
                            </div>
                        </form>
                    </div>

                    <!-- Caja de Resultados IA -->
                    <div class="fintech-card p-5">
                        <div class="flex items-center justify-between mb-3">
                            <h3 class="text-xs font-bold text-gray-700 uppercase tracking-wider">Diagnóstico de Inteligencia Artificial</h3>
                            <span id="loader" class="hidden text-xs text-blue-600 font-semibold animate-pulse">Analizando...</span>
                        </div>
                        <div id="resultBox" class="bg-slate-900 text-slate-100 p-4 rounded-xl text-xs font-mono leading-relaxed h-[280px] overflow-y-auto whitespace-pre-wrap select-text">
Selecciona una acción arriba para iniciar la auditoría o generar el documento de reclamo oficial.
                        </div>
                    </div>
                </section>

                <!-- 3. TAB CRÉDITO -->
                <section id="tab-credito" class="tab-content space-y-4">
                    <div class="fintech-card p-6">
                        <h2 class="text-base font-bold text-gray-900 mb-2">Perfil Crediticio</h2>
                        <p class="text-xs text-gray-500 mb-4">Monitoreo en tiempo real de tus líneas de crédito y tarjetas activas.</p>
                        
                        <div class="space-y-3">
                            <div class="p-4 bg-gray-50 rounded-xl border border-gray-200 flex justify-between items-center">
                                <div>
                                    <h4 class="text-xs font-bold text-gray-900">Citibank Visa Signature</h4>
                                    <p class="text-[11px] text-gray-500">Utilización: 14% ($1,450 / $10,000)</p>
                                </div>
                                <span class="text-xs font-bold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full">Al corriente</span>
                            </div>
                            <div class="p-4 bg-gray-50 rounded-xl border border-gray-200 flex justify-between items-center">
                                <div>
                                    <h4 class="text-xs font-bold text-gray-900">Línea de Crédito Rotativa</h4>
                                    <p class="text-[11px] text-gray-500">Disponible: $25,000.00</p>
                                </div>
                                <span class="text-xs font-bold text-emerald-600 bg-emerald-50 px-2.5 py-1 rounded-full">Activa</span>
                            </div>
                        </div>
                    </div>
                </section>

                <!-- 4. TAB PRÉSTAMOS -->
                <section id="tab-prestamos" class="tab-content space-y-4">
                    <div class="fintech-card p-6">
                        <h2 class="text-base font-bold text-gray-900 mb-2">Préstamos y Ofertas</h2>
                        <p class="text-xs text-gray-500 mb-4">Opciones pre-aprobadas basadas en tu comportamiento financiero.</p>
                        
                        <div class="p-5 bg-gradient-to-r from-blue-900 to-indigo-900 text-white rounded-2xl shadow-md">
                            <span class="text-[10px] uppercase tracking-wider bg-white/20 px-2.5 py-1 rounded-md font-semibold">Pre-aprobado</span>
                            <h3 class="text-lg font-bold mt-3">Préstamo Personal de Liquidez</h3>
                            <p class="text-2xl font-extrabold mt-1">$35,000.00 <span class="text-xs font-normal text-blue-200">al 6.99% APR</span></p>
                            <button class="mt-4 w-full bg-white text-blue-950 font-bold py-2.5 rounded-xl text-xs hover:bg-blue-50 transition">
                                Ver Detalles y Simulación
                            </button>
                        </div>
                    </div>
                </section>

                <!-- 5. TAB PERFIL -->
                <section id="tab-perfil" class="tab-content space-y-4">
                    <div class="fintech-card p-6">
                        <h2 class="text-base font-bold text-gray-900 mb-2">Configuración y Seguridad</h2>
                        <p class="text-xs text-gray-500 mb-4">Gestión de cuentas conectadas y preferencias legales.</p>
                        
                        <div class="space-y-2 text-xs">
                            <div class="p-3.5 bg-gray-50 rounded-xl flex justify-between items-center">
                                <span class="font-medium text-gray-700">Institución Vinculada</span>
                                <span class="font-bold text-gray-900">Citibank N.A.</span>
                            </div>
                            <div class="p-3.5 bg-gray-50 rounded-xl flex justify-between items-center">
                                <span class="font-medium text-gray-700">Normativa de Protección</span>
                                <span class="font-bold text-blue-600">Regulación E (12 CFR § 1005.11)</span>
                            </div>
                            <div class="p-3.5 bg-gray-50 rounded-xl flex justify-between items-center">
                                <span class="font-medium text-gray-700">Versión del Sistema</span>
                                <span class="font-bold text-gray-900">Bank Assistant Pro v2.0</span>
                            </div>
                        </div>
                    </div>
                </section>

            </main>

            <!-- BARRA DE NAVEGACIÓN INFERIOR (BOTTOM NAV) -->
            <nav class="fixed bottom-0 left-0 right-0 bg-white/90 backdrop-blur-md border-t border-gray-200 py-2.5 px-6 flex justify-between items-center z-50 shadow-lg max-w-md mx-auto">
                <button onclick="switchTab('home')" id="btn-home" class="nav-item active flex flex-col items-center gap-1">
                    <span class="text-xl">🏠</span>
                    <span class="text-[10px] font-semibold">Home</span>
                </button>
                <button onclick="switchTab('ia')" id="btn-ia" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-xl">✦</span>
                    <span class="text-[10px] font-semibold">Ejecutivo IA</span>
                </button>
                <button onclick="switchTab('credito')" id="btn-credito" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-xl">💳</span>
                    <span class="text-[10px] font-semibold">Crédito</span>
                </button>
                <button onclick="switchTab('prestamos')" id="btn-prestamos" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-xl">📊</span>
                    <span class="text-[10px] font-semibold">Préstamos</span>
                </button>
                <button onclick="switchTab('perfil')" id="btn-perfil" class="nav-item flex flex-col items-center gap-1">
                    <span class="text-xl">⚙️</span>
                    <span class="text-[10px] font-semibold">Perfil</span>
                </button>
            </nav>

        </div>

        <script>
            function switchTab(tabId) {
                // Ocultar todas las pestañas
                document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
                document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));

                // Mostrar la seleccionada
                document.getElementById('tab-' + tabId).classList.add('active');
                document.getElementById('btn-' + tabId).classList.add('active');
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }

            async function ejecutarAccion(endpoint) {
                const loader = document.getElementById('loader');
                const resultBox = document.getElementById('resultBox');
                loader.classList.remove('hidden');
                resultBox.innerText = "Consultando motor de inteligencia artificial...";

                const payload = {
                    comercio: document.getElementById('comercio').value,
                    monto: parseFloat(document.getElementById('monto').value),
                    ubicacion: document.getElementById('ubicacion').value,
                    categoria: document.getElementById('categoria').value,
                    hora: document.getElementById('hora').value
                };

                try {
                    const res = await fetch(endpoint, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    loader.classList.add('hidden');
                    
                    if (data.analisis_centinela) {
                        resultBox.innerText = data.analisis_centinela;
                    } else if (data.carta_formal_regulacion_e) {
                        resultBox.innerText = data.carta_formal_regulacion_e;
                    } else {
                        resultBox.innerText = JSON.stringify(data, null, 2);
                    }
                } catch (err) {
                    loader.classList.add('hidden');
                    resultBox.innerText = "Error de conexión con el servidor: " + err.message;
                }
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

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
