"""External provider integrations (Gemini AI & transactional email)."""
from services.external.email import EmailDeliveryService, GENERIC_PASSWORD_RESET_MESSAGE
from services.external.gemini import (
    AIService,
    DEFAULT_GEMINI_MODEL,
    clear_gemini_client_cache,
    get_gemini_client,
)

__all__ = [
    "AIService",
    "DEFAULT_GEMINI_MODEL",
    "EmailDeliveryService",
    "GENERIC_PASSWORD_RESET_MESSAGE",
    "clear_gemini_client_cache",
    "get_gemini_client",
]
