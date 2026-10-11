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
        <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
        <style>
            body { font-family: 'Plus Jakarta Sans', sans-serif; background-color: #0d0f12; color: #e2e8f0; }
            .japandi-card { background: #161a22; border: 1px solid #2a313d; border-radius: 16px; }
            .accent-btn { background-color: #d4af37; color: #0d0f12; font-weight: 600; }
            .accent-btn:hover { background-color: #c59b27; }
        </style>
    </head>
    <body class="min-h-screen p-4 md:p-8 flex flex-col items-center justify-start">
        <div class="max-w-4xl w-full">
            <!-- Header -->
            <header class="flex items-center justify-between py-6 border-b border-gray-800 mb-8">
                <div>
                    <h1 class="text-2xl font-bold tracking-tight text-white">Bank Assistant <span class="text-xs px-2 py-1 bg-amber-500/20 text-amber-400 rounded-full border border-amber-500/30">Reg E Core</span></h1>
                    <p class="text-xs text-gray-400 mt-1">Plataforma Inteligente de Prevención de Fraude y Disputas Legales (12 CFR § 1005.11)</p>
                </div>
                <div class="h-3 w-3 rounded-full bg-emerald-500 animate-pulse" title="Sistema en Línea"></div>
            </header>

            <!-- Grid principal -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Formulario -->
                <div class="japandi-card p-6 shadow-xl">
                    <h2 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
                        <span>🔍</span> Analizar Transacción Sospechosa
                    </h2>
                    <form id="fraudForm" class="space-y-4">
                        <div>
                            <label class="block text-xs font-medium text-gray-400 mb-1">Comercio / Destino</label>
                            <input type="text" id="comercio" value="Zelle Transfer / Electronics Store" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2.5 text-sm text-white focus:border-amber-500 focus:outline-none" required>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-medium text-gray-400 mb-1">Monto ($ USD)</label>
                                <input type="number" step="0.01" id="monto" value="485.50" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2.5 text-sm text-white focus:border-amber-500 focus:outline-none" required>
                            </div>
                            <div>
                                <label class="block text-xs font-medium text-gray-400 mb-1">Categoría</label>
                                <input type="text" id="categoria" value="Transferencia / Compra Web" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2.5 text-sm text-white focus:border-amber-500 focus:outline-none" required>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div>
                                <label class="block text-xs font-medium text-gray-400 mb-1">Ubicación / IP</label>
                                <input type="text" id="ubicacion" value="Miami, FL (Ubicación inusual)" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2.5 text-sm text-white focus:border-amber-500 focus:outline-none" required>
                            </div>
                            <div>
                                <label class="block text-xs font-medium text-gray-400 mb-1">Hora del Reporte</label>
                                <input type="text" id="hora" value="23:15 PST" class="w-full bg-gray-900 border border-gray-700 rounded-lg p-2.5 text-sm text-white focus:border-amber-500 focus:outline-none" required>
                            </div>
                        </div>
                        <div class="pt-2 grid grid-cols-2 gap-3">
                            <button type="button" onclick="ejecutarAccion('/analizar-fraude')" class="accent-btn py-3 px-4 rounded-lg text-xs font-bold transition-all shadow-md">
                                Evaluate Risk
                            </button>
                            <button type="button" onclick="ejecutarAccion('/generar-disputa-reg-e')" class="bg-gray-800 hover:bg-gray-700 text-amber-400 border border-amber-500/30 py-3 px-4 rounded-lg text-xs font-bold transition-all">
                                Legal Dispute
                            </button>
                        </div>
                    </form>
                </div>

                <!-- Panel de Resultados -->
                <div class="japandi-card p-6 shadow-xl flex flex-col justify-between">
                    <div>
                        <h2 class="text-lg font-semibold text-white mb-2 flex items-center justify-between">
                            <span>📋 Diagnóstico & Respuesta</span>
                            <span id="loader" class="hidden text-xs text-amber-400 animate-pulse">Procesando con Gemini AI...</span>
                        </h2>
                        <div id="resultBox" class="bg-gray-900/80 border border-gray-800 rounded-lg p-4 text-xs text-gray-300 leading-relaxed h-[320px] overflow-y-auto font-mono whitespace-pre-wrap">
Selecciona una acción a la izquierda para evaluar la transacción o redactar la carta de reclamo oficial bajo la Regulación E.
                        </div>
                    </div>
                </div>
            </div>

            <!-- Footer -->
            <footer class="mt-12 text-center text-xs text-gray-600">
                Bank Assistant Core Engine v1.0 • Impulsado por Google Gemini AI & FastAPI
            </footer>
        </div>

        <script>
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
                    resultBox.innerText = "Error de conexión o configuración en el servidor: " + err.message;
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
        model = genai.GenerativeModel("gemini-2.5-flash")
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
        model = genai.GenerativeModel("gemini-2.5-flash")
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
