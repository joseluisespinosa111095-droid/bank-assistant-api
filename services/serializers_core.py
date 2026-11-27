from datetime import datetime, timezone
import re
from typing import Optional

from models import CustomerModel, InteractionModel, LeadModel, TenantModel


def serialize_tenant(t: TenantModel) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "slug": t.slug,
        "legal_name": t.legal_name,
        "is_active": bool(t.is_active),
        "created_at": t.created_at.isoformat() if t.created_at else None
    }


# --- CUSTOMER NAME (PURE HELPERS) BEGIN ---
# Limites: 50 + 1 + 50 + 1 + 75 = 177 caracteres, siempre cabe en full_legal_name (180).
def normalize_name_part(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def compose_full_legal_name(first: Optional[str], middle: Optional[str], last: Optional[str]) -> str:
    return " ".join(
        p for p in (normalize_name_part(first), normalize_name_part(middle), normalize_name_part(last)) if p
    )


def resolve_customer_name_for_create(entity_type, first_name, middle_name, last_name, full_legal_name) -> dict:
    # Devuelve los 4 campos de nombre a guardar o lanza ValueError con el mensaje para el usuario.
    if entity_type == "BUSINESS":
        legal_name = normalize_name_part(full_legal_name)
        if len(legal_name) < 3:
            raise ValueError("Legal entity name is required for Business (Persona Moral) customers.")
        return {"first_name": None, "middle_name": None, "last_name": None, "full_legal_name": legal_name}

    first = normalize_name_part(first_name)
    middle = normalize_name_part(middle_name)
    last = normalize_name_part(last_name)
    if not first or not last:
        raise ValueError("First name and last name are required for individual customers.")
    return {
        "first_name": first,
        "middle_name": middle or None,
        "last_name": last,
        "full_legal_name": compose_full_legal_name(first, middle, last),
    }


def resolve_customer_name_for_update(final_entity_type, type_changed, current_first, current_middle, current_last,
                                     first_name, middle_name, last_name, full_legal_name) -> Optional[dict]:
    # Devuelve {campo: valor} con lo que hay que cambiar, None si el nombre no cambia, o lanza ValueError.
    if final_entity_type == "BUSINESS":
        out = {}
        if full_legal_name is not None:
            legal_name = normalize_name_part(full_legal_name)
            if len(legal_name) < 3:
                raise ValueError("Legal entity name must have at least 3 characters.")
            out["full_legal_name"] = legal_name
        elif type_changed:
            raise ValueError("Legal entity name is required for Business (Persona Moral) customers.")
        if type_changed or current_first or current_middle or current_last:
            out.update({"first_name": None, "middle_name": None, "last_name": None})
        return out or None

    # INDIVIDUAL
    parts_sent = any(p is not None for p in (first_name, middle_name, last_name))
    if parts_sent or type_changed:
        first = normalize_name_part(first_name) if first_name is not None else normalize_name_part(current_first)
        middle = normalize_name_part(middle_name) if middle_name is not None else normalize_name_part(current_middle)
        last = normalize_name_part(last_name) if last_name is not None else normalize_name_part(current_last)
        if not first or not last:
            raise ValueError("First name and last name are required for individual customers.")
        return {
            "first_name": first,
            "middle_name": middle or None,
            "last_name": last,
            "full_legal_name": compose_full_legal_name(first, middle, last),
        }

    if full_legal_name is not None:
        # Clientes anteriores al cambio (sin partes) pueden seguir editando solo el nombre completo.
        if normalize_name_part(current_first) or normalize_name_part(current_last):
            raise ValueError("To change an individual's name, send first_name, middle_name and last_name.")
        legal_name = normalize_name_part(full_legal_name)
        if len(legal_name) < 3:
            raise ValueError("Name must have at least 3 characters.")
        return {"full_legal_name": legal_name}
    return None
# --- CUSTOMER NAME (PURE HELPERS) END ---


def serialize_customer(
    c: CustomerModel,
    active_loans_count: int = 0,
    unpaid_principal_cents: int = 0,
    last_payment_date: Optional[str] = None
) -> dict:
    return {
        "id": c.id,
        "customer_number": c.customer_number,
        "entity_type": c.entity_type,
        "status": c.status,
        "verification_status": c.verification_status,
        "full_legal_name": c.full_legal_name,
        "first_name": c.first_name,
        "middle_name": c.middle_name,
        "last_name": c.last_name,
        "trade_name": c.trade_name,
        "legal_representative_name": c.legal_representative_name,
        "tax_id_rfc": c.tax_id_rfc,
        "date_of_birth": c.date_of_birth.isoformat() if c.date_of_birth else None,
        "phone_primary": c.phone_primary,
        "phone_secondary": c.phone_secondary,
        "email": c.email,
        "address_full": c.address_full,
        "employment_status": c.employment_status,
        "employer_or_business_name": c.employer_or_business_name,
        "occupation_or_industry": c.occupation_or_industry,
        "employment_address": c.employment_address,
        "owner_user_id": c.owner_user_id,
        "created_by_user_id": c.created_by_user_id,
        "customer_since": c.customer_since.isoformat() if c.customer_since else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "current_outstanding_balance_cents": unpaid_principal_cents,
        "current_outstanding_principal_cents": unpaid_principal_cents,
        "active_loans_count": active_loans_count,
        "last_payment_date": last_payment_date
    }


def serialize_interaction(i: InteractionModel, user_map: dict) -> dict:
    author = user_map.get(i.created_by_user_id)
    return {
        "id": i.id,
        "lead_id": i.lead_id,
        "customer_id": i.customer_id,
        "interaction_type": i.interaction_type,
        "direction": i.direction,
        "visibility": i.visibility,
        "outcome": i.outcome,
        "content": i.content,
        "next_follow_up_at": i.next_follow_up_at.isoformat() if i.next_follow_up_at else None,
        "created_by_user_id": i.created_by_user_id,
        "created_by_name": (author.full_name or author.email) if author else f"User #{i.created_by_user_id}",
        "created_by_role": author.role if author else "TEAM",
        "created_at": i.created_at.isoformat() if i.created_at else None
    }


def compute_call_bucket(lead: LeadModel, today_start_utc: datetime, today_end_utc: datetime) -> Optional[str]:
    if lead.stage in ("CONVERTED", "LOST"):
        return None
    if lead.next_follow_up_at:
        nfa = lead.next_follow_up_at
        if nfa.tzinfo is None:
            nfa = nfa.replace(tzinfo=timezone.utc)
        if nfa < today_start_utc:
            return "OVERDUE"
        if nfa <= today_end_utc:
            return "TODAY"
        return "UPCOMING"
    if lead.stage == "NEW":
        return "TODAY"
    return None


def serialize_lead(l: LeadModel, today_start_utc: datetime, today_end_utc: datetime) -> dict:
    return {
        "id": l.id,
        "lead_number": l.lead_number,
        "full_name": l.full_name,
        "entity_type": l.entity_type,
        "trade_name": l.trade_name,
        "legal_representative_name": l.legal_representative_name,
        "tax_id_rfc": l.tax_id_rfc,
        "phone_primary": l.phone_primary,
        "email": l.email,
        "source": l.source,
        "product_interest": l.product_interest,
        "requested_amount_cents": int(l.requested_amount_cents or 0),
        "currency": l.currency,
        "stage": l.stage,
        "priority": l.priority,
        "lost_reason": l.lost_reason,
        "last_contacted_at": l.last_contacted_at.isoformat() if l.last_contacted_at else None,
        "last_interaction_outcome": l.last_interaction_outcome,
        "next_follow_up_at": l.next_follow_up_at.isoformat() if l.next_follow_up_at else None,
        "call_bucket": compute_call_bucket(l, today_start_utc, today_end_utc),
        "summary_notes": l.summary_notes,
        "owner_user_id": l.owner_user_id,
        "created_by_user_id": l.created_by_user_id,
        "converted_customer_id": l.converted_customer_id,
        "converted_at": l.converted_at.isoformat() if l.converted_at else None,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "updated_at": l.updated_at.isoformat() if l.updated_at else None
    }
