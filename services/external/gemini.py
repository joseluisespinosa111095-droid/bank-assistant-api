"""Gemini AI integration using the official google-genai SDK."""
import os
from typing import List, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from core.config import logger
from models import ChatMessageModel

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_API_VERSION = "v1beta"
DEFAULT_GEMINI_FALLBACK_MODELS = (
    "gemini-3.8-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
)
_CLIENT_CACHE: dict[tuple[str, str], genai.Client] = {}


def resolve_gemini_api_version(api_version: Optional[str] = None) -> str:
    """Resolve Gemini API version (defaults to v1beta; google-genai 3.x defaults to v1)."""
    raw = api_version if api_version is not None else os.getenv("GEMINI_API_VERSION", "")
    resolved = (raw or "").strip()
    return resolved or DEFAULT_GEMINI_API_VERSION


def resolve_gemini_model_candidates(primary_model: Optional[str] = None) -> list[str]:
    """Return deduplicated ordered model candidates starting with GEMINI_MODEL."""
    raw_primary = primary_model if primary_model is not None else os.getenv("GEMINI_MODEL", "")
    primary = (raw_primary or "").strip() or DEFAULT_GEMINI_MODEL

    raw_fallbacks = (os.getenv("GEMINI_FALLBACK_MODELS") or "").strip()
    if raw_fallbacks:
        fallbacks = [m.strip() for m in raw_fallbacks.split(",") if m.strip()]
    else:
        fallbacks = list(DEFAULT_GEMINI_FALLBACK_MODELS)

    candidates: list[str] = []
    for name in [primary, *fallbacks]:
        if name and name not in candidates:
            candidates.append(name)
    return candidates


def get_gemini_client(
    api_key: Optional[str] = None,
    api_version: Optional[str] = None,
) -> Optional[genai.Client]:
    """Return a cached google-genai Client configured for the target Gemini API version."""
    resolved_key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
    if not resolved_key:
        return None

    resolved_version = resolve_gemini_api_version(api_version)
    cache_key = (resolved_key, resolved_version)
    client = _CLIENT_CACHE.get(cache_key)
    if client is None:
        client = genai.Client(
            api_key=resolved_key,
            http_options=types.HttpOptions(api_version=resolved_version),
        )
        _CLIENT_CACHE[cache_key] = client
    return client


def clear_gemini_client_cache() -> None:
    """Reset cached Gemini clients (useful in tests or key rotation)."""
    _CLIENT_CACHE.clear()


def _is_model_not_found_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    if code == 404 or status == "NOT_FOUND":
        return True
    msg = str(exc)
    return "404" in msg and ("NOT_FOUND" in msg.upper() or "not found" in msg.lower())


class AIService:
    """Decoupled advisory AI assistant backed by Google GenAI (`google-genai`)."""

    @staticmethod
    def build_system_rules(user_role: str) -> str:
        return (
            "You are NURA, the internal intelligent assistant for a credit & pipeline CRM team. "
            f"Current authenticated team member role: {user_role}. "
            "Supported credit products: Personal Loan, Business Loan.\n"
            "STRICT SECURITY RULES:\n"
            "1. You operate strictly in advisory and read-only mode. You cannot approve loans, "
            "disburse money, modify balances, or execute financial transactions.\n"
            "2. Never invent customer balances, payments, or approval confirmations.\n"
            "3. Respond clearly and professionally in the same language used by the user."
        )

    @staticmethod
    def generate_reply(user_message: str, prior_messages: List[ChatMessageModel], user_role: str) -> str:
        api_version = resolve_gemini_api_version()
        client = get_gemini_client(api_version=api_version)
        if client is None:
            return "⚠️ Nura Assistant is temporarily unavailable (API key not configured)."

        model_candidates = resolve_gemini_model_candidates()
        system_rules = AIService.build_system_rules(user_role)

        structured_contents: list[types.Content] = []
        for m in prior_messages[-10:]:
            mapped_role = "user" if m.role == "user" else "model"
            structured_contents.append(
                types.Content(
                    role=mapped_role,
                    parts=[types.Part.from_text(text=(m.content or "")[:2000])],
                )
            )

        structured_contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_message.strip())],
            )
        )

        config = types.GenerateContentConfig(system_instruction=system_rules)

        last_error: Optional[Exception] = None
        last_model = model_candidates[0]
        for idx, model_name in enumerate(model_candidates):
            last_model = model_name
            try:
                res = client.models.generate_content(
                    model=model_name,
                    contents=structured_contents,
                    config=config,
                )
                text_out = res.text.strip() if res and getattr(res, "text", None) else "No response received."
                return text_out
            except genai_errors.APIError as e:
                last_error = e
                has_next = idx + 1 < len(model_candidates)
                if _is_model_not_found_error(e) and has_next:
                    logger.warning(
                        "Gemini model '%s' returned 404 on api_version='%s'; retrying with fallback '%s'.",
                        model_name,
                        api_version,
                        model_candidates[idx + 1],
                    )
                    continue
                logger.error(
                    "Gemini provider error: %s (model=%s, api_version=%s, code=%s)",
                    type(e).__name__,
                    model_name,
                    api_version,
                    getattr(e, "code", None),
                )
                return "⚠️ Nura Assistant is busy right now. Your message has been saved."
            except Exception as e:
                last_error = e
                has_next = idx + 1 < len(model_candidates)
                if _is_model_not_found_error(e) and has_next:
                    logger.warning(
                        "Gemini model '%s' not found on api_version='%s'; retrying with fallback '%s'.",
                        model_name,
                        api_version,
                        model_candidates[idx + 1],
                    )
                    continue
                logger.error(
                    "Gemini provider error: %s (model=%s, api_version=%s)",
                    type(e).__name__,
                    model_name,
                    api_version,
                )
                return "⚠️ Nura Assistant is busy right now. Your message has been saved."

        logger.error(
            "Gemini provider exhausted model candidates (last_model=%s, api_version=%s, error=%s)",
            last_model,
            api_version,
            type(last_error).__name__ if last_error else "Unknown",
        )
        return "⚠️ Nura Assistant is busy right now. Your message has been saved."
