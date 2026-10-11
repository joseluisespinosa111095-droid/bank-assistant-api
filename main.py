import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI(
    title="Bank Assistant - Compliance & Fraud Platform",
    version="1.0.0"
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
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Bank Assistant | Sentinel AI & Reg E</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body {
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background-color: #000000;
                color: #f5f5f7;
                -webkit-font-smoothing: antialiased;
            }
            .apple-glass {
                background: rgba(28, 28, 30, 0.75);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 20px;
            }
            .apple-input {
                background: rgba(44, 44, 46, 0.6);
                border: 1px solid rgba(255, 255, 255, 0.12);
                color: #ffffff;
                transition: all 0.2s ease;
            }
            .apple-input:focus {
                border-color: #0a84ff;
                box-shadow: 0 0 0 3px rgba(10, 132, 255, 0.25);
                outline: none;
            }
            .apple-btn-primary {
                background: #0a84ff;
                color: #ffffff;
                font-weight: 500;
                transition: all 0.2s ease;
            }
            .apple-btn-primary:hover {
                background: #0077ed;
            }
            .apple-btn-secondary {
                background: rgba(255, 255, 255, 0.08);
                color: #f5f5f7;
                border: 1px solid rgba(255, 255, 255, 0.12);
                font-weight: 500;
                transition: all 0.2s ease;
            }
            .apple-btn-secondary:hover {
                background: rgba(255, 255, 255, 0.15);
            }
            .apple-output {
                background: rgba(18, 18, 20, 0.85);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
            }
        </style>
    </head>
    <body class="min-h-screen p-5 md:p-12 flex flex-col items-center justify-start">
        <div class="max-w-4xl w-full">
            <!-- Header minimalista estilo Apple -->
            <header class="flex items-center justify-between pb-6 mb-8 border-b border-white/10">
                <div>
                    <div class="flex items-center gap-3">
                        <h1 class="text-2xl md:text-3xl font-bold tracking-tight text-white">Bank Assistant</h1>
                        <span class="text-[11px] font-semibold px-2.5 py-0.5 bg-blue-500/20 text-blue-400 rounded-full border border-blue-500/30">Reg E Pro</span>
                    </div>
                    <p class="text-xs text-gray-400 mt-1 font-normal">Prevención inteligente de fraude financiero y cumplimiento legal 12 CFR § 1005.11</p>
                </div>
                <div class="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 px-3 py-1.5 rounded-full">
                    <div class="h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></div>
                    <span class="text-[11px] font-medium text-emerald-400">Sistema Activo</span>
                </div>
            </header>

            <!-- Grid principal -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Tarjeta de Formulario -->
                <div class="apple-glass p-6 md:p-8 shadow-2xl flex flex-col justify-between">
                    <div>
                        <h2 class="text-base font-semibold text-white mb-5 flex items-center gap-2">
                            <span class="text-blue-400">✦</span> Analizar Transacción
                        </h2>
                        <form class="space-y-4">
                            <div>
                                <label class="block text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-1.5">Comercio / Destino</label>
                                <input type="text" id="comercio" value="Zelle Transfer / Electronics Store" class="w-full apple-input rounded-xl p-3 text-sm" required>
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-1.5">Monto ($ USD)</label>
                                    <input type="number" step="0.01" id="monto" value="485.50" class="w-full apple-input rounded-xl p-3 text-sm" required>
                                </div>
                                <div>
                                    <label class="block text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-1.5">Categoría</label>
                                    <input type="text" id="categoria" value="Transferencia / Compra Web" class="w-full apple-input rounded-xl p-3 text-sm" required>
                                </div>
                            </div>
                            <div class="grid grid-cols-2 gap-3">
                                <div>
                                    <label class="block text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-1.5">Ubicación / IP</label>
                                    <input type="text" id="ubicacion" value="Miami, FL (Ubicación inusual)" class="w-full apple-input rounded-xl p-3 text-sm" required>
                                </div>
                                <div>
                                    <label class="block text-[11px] font-medium text-gray-400 uppercase tracking-wider mb-1.5">Hora del Reporte</label>
                                    <input type="text" id="hora" value="23:15 PST" class="w-full apple-input rounded-xl p-3 text-sm" required>
                                </div>
                            </div>
                        </form>
                    </div>

                    <div class="pt-6 grid grid-cols-2 gap-3 mt-6 border-t border-white/5">
                        <button type="button" onclick="ejecutarAccion('/analizar-fraude')" class="apple-btn-primary py-3 px-4 rounded-xl text-xs shadow-lg">
                            Evaluate Risk
                        </button>
                        <button type="button" onclick="ejecutarAccion('/generar-disputa-reg-e')" class="apple-btn-secondary py-3 px-4 rounded-xl text-xs">
                            Legal Dispute
                        </button>
                    </div>
                </div>

                <!-- Panel de Resultados Estilo Terminal Refinada -->
                <div class="apple-glass p-6 md:p-8 shadow-2xl flex flex-col justify-between">
                    <div>
                        <div class="flex items-center justify-between mb-5">
                            <h2 class="text-base font-semibold text-white flex items-center gap-2">
                                <span class="text-blue-400">⌘</span> Diagnóstico & Respuesta
                            </h2>
                            <span id="loader" class="hidden text-xs text-blue-400 font-medium animate-pulse">Generando con IA...</span>
                        </div>
                        <div id="resultBox" class="apple-output p-4 text-xs text-gray-300 leading-relaxed h-[360px] overflow-y-auto font-mono whitespace-pre-wrap select-text">
Selecciona una acción a la izquierda para evaluar el nivel de riesgo o redactar el reclamo formal bajo la Regulación E.
                        </div>
                    </div>
                    <div class="pt-4 text-[11px] text-gray-500 text-right">
                        Protección al Consumidor Financiero • 12 CFR § 1005.11
                    </div>
                </div>
            </div>

            <!-- Footer -->
            <footer class="mt-12 text-center text-xs text-gray-500 font-light">
                Bank Assistant Core Engine • Impulsado por Google Gemini & FastAPI
            </footer>
        </div>

        <script>
            async function ejecutarAccion(endpoint) {
                const loader = document.getElementById('loader');
                const resultBox = document.getElementById('resultBox');
                loader.classList.remove('hidden');
                resultBox.innerText = "Procesando solicitud de seguridad...";

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
