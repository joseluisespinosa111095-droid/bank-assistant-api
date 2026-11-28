"""Gemini AI integration using the official google-genai SDK."""
import os
from typing import List, Optional

from google import genai
from google.genai import types

from core.config import logger
from models import ChatMessageModel

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
_CLIENT_CACHE: dict[str, genai.Client] = {}


def get_gemini_client(api_key: Optional[str] = None) -> Optional[genai.Client]:
    """Return a cached google-genai Client for the configured GEMINI_API_KEY."""
    resolved_key = (api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")).strip()
    if not resolved_key:
        return None
    client = _CLIENT_CACHE.get(resolved_key)
    if client is None:
        client = genai.Client(api_key=resolved_key)
        _CLIENT_CACHE[resolved_key] = client
    return client


def clear_gemini_client_cache() -> None:
    """Reset cached Gemini clients (useful in tests or key rotation)."""
    _CLIENT_CACHE.clear()


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
        client = get_gemini_client()
        if client is None:
            return "⚠️ Nura Assistant is temporarily unavailable (API key not configured)."

        model_name = (os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        system_rules = AIService.build_system_rules(user_role)

        try:
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
            res = client.models.generate_content(
                model=model_name,
                contents=structured_contents,
                config=config,
            )

            text_out = res.text.strip() if res and getattr(res, "text", None) else "No response received."
            return text_out
        except Exception as e:
            logger.error(f"Gemini provider error: {type(e).__name__}")
            return "⚠️ Nura Assistant is busy right now. Your message has been saved."
