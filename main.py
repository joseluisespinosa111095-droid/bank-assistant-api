import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import google.generativeai as genai

# Inicializar aplicación FastAPI
app = FastAPI(
    title="Bank Assistant API",
    description="Backend fintech para prevención de fraude y defensa legal bajo Regulación E",
    version="1.0.0"
)

# Configurar la API Key de Gemini desde las variables de entorno
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Esquema de datos para las transacciones entrantes
class TransaccionInput(BaseModel):
    comercio: str
    monto: float
    ubicacion: str
    categoria: str
    hora: str
    usuario_id: str = "cliente_principal"

@app.get("/")
def read_root():
    return {
        "status": "online",
        "system": "Bank Assistant Core",
        "framework": "FastAPI + Gemini AI",
        "compliance": "12 CFR § 1005.11 (Regulation E)"
    }

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

        Indica:
        1. Nivel de Riesgo (Bajo, Moderado, Crítico).
        2. Tipo de fraude probable (ej. Zelle Account Takeover, Skimmer en gasolinera, Suscripción trampa).
        3. Probabilidad de éxito en recuperación de fondos bajo ley federal.
        """
        response = model.generate_content(prompt)
        return {
            "transaccion": data.dict(),
            "analisis_centinela": response.text,
            "marco_legal": "Regulación E - Protección al Consumidor"
        }
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
        return {
            "estado": "Documento generado exitosamente",
            "carta_formal_regulacion_e": response.text
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al redactar documento: {str(e)}")
