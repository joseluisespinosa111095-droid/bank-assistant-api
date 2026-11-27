import json
from typing import Optional

from sqlalchemy.orm import Session

from core.config import logger
from models import AuditLogModel, UserModel

# --- AUDIT SERVICE ---
SENSITIVE_AUDIT_KEYS = {
    "password", "new_password", "password_hash", "token", "reset_token",
    "token_hash", "session_token", "csrf_token", "secret", "bootstrap_secret",
    "rate_limit_hmac_secret", "api_key", "email", "raw_email",
    "phone", "phone_primary", "phone_secondary", "address_full",
    "tax_id_rfc", "date_of_birth", "full_legal_name", "full_name",
    "first_name", "middle_name", "last_name",
    "content", "summary_notes", "reason_or_note", "approval_notes", "disbursement_notes"
}


def record_audit_event(
    db: Session,
    event_type: str,
    resource_type: str,
    outcome: str,
    actor: Optional[UserModel] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[dict] = None,
    commit: bool = True,
    scope: Optional[str] = None,
    tenant_id: Optional[int] = None,
):
    sanitized_details = {}
    if details:
        for k, v in details.items():
            if str(k).lower() in SENSITIVE_AUDIT_KEYS:
                continue
            sanitized_details[str(k)] = str(v)[:255]

    if scope is None:
        if actor is None:
            effective_scope = "ANONYMOUS"
            effective_tenant_id = None
        else:
            effective_scope = "TENANT"
            effective_tenant_id = tenant_id if tenant_id is not None else actor.tenant_id
    else:
        effective_scope = scope
        if effective_scope in ("ANONYMOUS", "PLATFORM"):
            effective_tenant_id = None
        else:
            effective_tenant_id = tenant_id if tenant_id is not None else (actor.tenant_id if actor else None)

    entry = AuditLogModel(
        tenant_id=effective_tenant_id,
        scope=effective_scope,
        actor_user_id=actor.id if actor else None,
        actor_role=actor.role if actor else None,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=str(resource_id)[:60] if resource_id is not None else None,
        outcome=outcome,
        ip_address=ip_address,
        details_json=json.dumps(sanitized_details) if sanitized_details else None
    )

    if not commit:
        db.add(entry)
        db.flush()
        return

    try:
        db.add(entry)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Audit log standalone write failed: {type(e).__name__}")
