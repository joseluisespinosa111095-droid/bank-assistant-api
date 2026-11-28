"""External provider integrations (Gemini AI & transactional email)."""
from services.external.email import EmailDeliveryService, GENERIC_PASSWORD_RESET_MESSAGE
from services.external.gemini import (
    AIService,
    DEFAULT_GEMINI_API_VERSION,
    DEFAULT_GEMINI_FALLBACK_MODELS,
    DEFAULT_GEMINI_MODEL,
    clear_gemini_client_cache,
    get_gemini_client,
    resolve_gemini_api_version,
    resolve_gemini_model_candidates,
)

__all__ = [
    "AIService",
    "DEFAULT_GEMINI_API_VERSION",
    "DEFAULT_GEMINI_FALLBACK_MODELS",
    "DEFAULT_GEMINI_MODEL",
    "EmailDeliveryService",
    "GENERIC_PASSWORD_RESET_MESSAGE",
    "clear_gemini_client_cache",
    "get_gemini_client",
    "resolve_gemini_api_version",
    "resolve_gemini_model_candidates",
]
