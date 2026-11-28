"""Provider-agnostic transactional email delivery interface (prod-safe)."""
import os

from core.config import IS_PRODUCTION, logger
from core.security import mask_email_for_audit

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
                reset_link,
            )
            return True

        return False
