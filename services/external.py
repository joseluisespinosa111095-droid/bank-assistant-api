import os
from typing import List

import google.generativeai as genai

from core.config import IS_PRODUCTION, logger
from core.security import mask_email_for_audit
from models import ChatMessageModel


# --- DECOUPLED AI SERVICE (NURA ASSISTANT) ---
class AIService:
    @staticmethod
    def generate_reply(user_message: str, prior_messages: List[ChatMessageModel], user_role: str) -> str:
        if not os.getenv("GEMINI_API_KEY"):
            return "⚠️ Nura Assistant is temporarily unavailable (API key not configured)."

        model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        system_rules = (
            "You are NURA, the internal intelligent assistant for a credit & pipeline CRM team. "
            f"Current authenticated team member role: {user_role}. "
            "Supported credit products: Personal Loan, Business Loan.\n"
            "STRICT SECURITY RULES:\n"
            "1. You operate strictly in advisory and read-only mode. You cannot approve loans, "
            "disburse money, modify balances, or execute financial transactions.\n"
            "2. Never invent customer balances, payments, or approval confirmations.\n"
            "3. Respond clearly and professionally in the same language used by the user."
        )

        try:
            structured_contents = []
            for m in prior_messages[-10:]:
                mapped_role = "user" if m.role == "user" else "model"
                structured_contents.append({"role": mapped_role, "parts": [m.content[:2000]]})

            structured_contents.append({"role": "user", "parts": [user_message.strip()]})

            try:
                model = genai.GenerativeModel(model_name, system_instruction=system_rules)
                res = model.generate_content(structured_contents)
            except TypeError:
                model = genai.GenerativeModel(model_name)
                fallback_prompt = f"[SYSTEM INSTRUCTIONS]\n{system_rules}\n\n[CONVERSATION HISTORY]\n"
                for m in prior_messages[-10:]:
                    fallback_prompt += f"{m.role}: {m.content[:1500]}\n"
                fallback_prompt += f"user: {user_message.strip()}\n"
                res = model.generate_content(fallback_prompt)

            text_out = res.text.strip() if res and getattr(res, "text", None) else "No response received."
            return text_out
        except Exception as e:
            logger.error(f"Gemini provider error: {type(e).__name__}")
            return "⚠️ Nura Assistant is busy right now. Your message has been saved."


# --- TRANSACTIONAL EMAIL DELIVERY INTERFACE (PROVIDER-AGNOSTIC & PROD-SAFE) ---
GENERIC_PASSWORD_RESET_MESSAGE = (
    "If an active account is associated with that email address, "
    "password reset instructions have been sent."
)


class EmailDeliveryService:
    """
    Provider-agnostic email delivery interface.
    STRICT SECURITY POLICY:
    - In production (IS_PRODUCTION == True), raw tokens, reset links, or secrets
      are NEVER written to logs under any circumstance.
    - If no real transactional email provider is configured in production,
      delivery fails safely (returns False) without faking success.
    """
    @staticmethod
    def send_password_reset_email(recipient_email: str, raw_token: str, fallback_base_url: str = "") -> bool:
        provider = os.getenv("EMAIL_PROVIDER", "NONE").strip().upper()
        base_url = os.getenv("PASSWORD_RESET_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            base_url = (fallback_base_url or "").strip().rstrip("/")

        reset_link = f"{base_url}/?reset_token={raw_token}"

        if IS_PRODUCTION:
            # Mock/console logging is strictly forbidden in production
            if provider in ("", "NONE", "DEV_CONSOLE", "MOCK"):
                logger.error("Password reset email delivery aborted: EMAIL_PROVIDER is not configured in production.")
                return False
            # Hook for authorized external transactional provider (e.g., Resend / SendGrid / SES)
            logger.error(f"Password reset email delivery aborted: provider '{provider}' adapter not yet active.")
            return False

        # Local / development environment only (IS_PRODUCTION == False)
        if provider in ("", "NONE", "DEV_CONSOLE", "MOCK"):
            logger.info(
                "[DEV ONLY - LOCAL PASSWORD RESET] Account=%s | Reset Link: %s",
                mask_email_for_audit(recipient_email),
                reset_link
            )
            return True

        return False
