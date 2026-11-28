"""HTTP-agnostic domain service for the Credit Broker module (Entregable 5B).

Zero imports from FastAPI, Starlette, routers, dependencies, or main.
All monetary calculations use exact integer cents and basis points (bps).
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Optional

from sqlalchemy import func, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from models import (
    BrokerCaseModel,
    BrokerCommissionModel,
    BrokerLenderModel,
    BrokerSubmissionModel,
    CustomerModel,
    LeadModel,
    UserModel,
)
from services.audit import record_audit_event

VALID_BROKER_LENDER_TYPES = {
    "BANK",
    "SOFOM",
    "FINTECH",
    "CREDIT_UNION",
    "PRIVATE_LENDER",
    "OTHER",
}
VALID_BROKER_CASE_STATUSES = {
    "DRAFT",
    "SUBMITTED",
    "OFFERS_RECEIVED",
    "FUNDED",
    "CLOSED_LOST",
    "CANCELLED",
}
ACTIVE_BROKER_CASE_STATUSES = {"DRAFT", "SUBMITTED", "OFFERS_RECEIVED"}
TERMINAL_BROKER_CASE_STATUSES = {"FUNDED", "CLOSED_LOST", "CANCELLED"}
VALID_BROKER_SUBMISSION_STATUSES = {
    "DRAFT",
    "SUBMITTED",
    "IN_UNDERWRITING",
    "APPROVED",
    "DECLINED",
    "WITHDRAWN",
    "SELECTED",
}
TERMINAL_BROKER_SUBMISSION_STATUSES = {"DECLINED", "WITHDRAWN", "SELECTED"}
VALID_BROKER_COMMISSION_STATUSES = {"PENDING", "INVOICED", "PAID", "CANCELLED"}
TERMINAL_BROKER_COMMISSION_STATUSES = {"PAID", "CANCELLED"}

MAX_COMMISSION_BPS = 5000
MAX_MONEY_CENTS = 10_000_000_000_00  # $10B MXN cap
LENDER_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,39}$")


class BrokerServiceError(Exception):
    """Base domain error for Credit Broker operations (HTTP-agnostic)."""

    def __init__(self, message: str, *, error_code: str = "BROKER_ERROR", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class BrokerNotFoundError(BrokerServiceError):
    """Raised when a target broker entity does not exist within the tenant."""


class BrokerConflictError(BrokerServiceError):
    """Raised on duplicate keys, idempotency mismatches, or invalid state transitions."""


class BrokerValidationError(BrokerServiceError):
    """Raised on domain validation errors (amounts, ranges, enums, formats)."""


# ---------------------------------------------------------------------------
# PURE HELPERS & ATOMIC TENANT SEQUENCES
# ---------------------------------------------------------------------------
def _next_broker_sequence(db: Session, tenant_id: int, entity_type: str) -> int:
    row = db.execute(
        text("""
            INSERT INTO tenant_sequences (tenant_id, entity_type, last_value)
            VALUES (:tenant_id, :entity_type, 1)
            ON CONFLICT (tenant_id, entity_type) DO UPDATE
            SET last_value = tenant_sequences.last_value + 1
            RETURNING last_value
        """),
        {"tenant_id": tenant_id, "entity_type": entity_type},
    ).fetchone()
    return int(row[0])


def generate_next_broker_case_number(db: Session, tenant_id: int) -> str:
    return f"BRK-{_next_broker_sequence(db, tenant_id, 'broker_cases'):05d}"


def generate_next_broker_commission_number(db: Session, tenant_id: int) -> str:
    return f"COM-{_next_broker_sequence(db, tenant_id, 'broker_commissions'):05d}"


def parse_money_to_cents(
    pesos_val: Optional[float],
    cents_val: Optional[int],
    *,
    field_name: str = "amount",
    required: bool = False,
    allow_zero: bool = False,
) -> Optional[int]:
    if pesos_val is not None and cents_val is not None:
        from_pesos = parse_money_to_cents(
            pesos_val, None, field_name=field_name, required=False, allow_zero=allow_zero
        )
        if from_pesos != int(cents_val):
            raise BrokerValidationError(
                f"Conflicting {field_name}_pesos and {field_name}_cents values provided.",
                error_code="CONFLICTING_MONEY_FIELDS",
            )
    if cents_val is not None:
        val = int(cents_val)
    elif pesos_val is not None:
        try:
            d = Decimal(str(pesos_val))
        except InvalidOperation:
            raise BrokerValidationError(f"Invalid monetary value for {field_name}.", error_code="INVALID_MONEY")
        val = int((d * Decimal("100")).quantize(Decimal("1")))
    else:
        if required:
            raise BrokerValidationError(f"{field_name} is required.", error_code="MISSING_MONEY")
        return None

    if allow_zero:
        if val < 0:
            raise BrokerValidationError(f"{field_name} cannot be negative.", error_code="NEGATIVE_MONEY")
    else:
        if val <= 0:
            raise BrokerValidationError(f"{field_name} must be greater than zero.", error_code="NON_POSITIVE_MONEY")

    if val > MAX_MONEY_CENTS:
        raise BrokerValidationError(f"{field_name} exceeds maximum supported amount.", error_code="MONEY_TOO_LARGE")
    return val


def parse_optional_iso_datetime_utc(raw_value: Optional[str], *, field_name: str = "timestamp") -> Optional[datetime]:
    if raw_value is None or not str(raw_value).strip():
        return None
    s = str(raw_value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise BrokerValidationError(f"Invalid ISO datetime format for {field_name}: {raw_value}", error_code="INVALID_DATETIME")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_commission_cents(funded_amount_cents: int, commission_rate_bps: int) -> int:
    if funded_amount_cents <= 0:
        raise BrokerValidationError("funded_amount_cents must be positive.", error_code="INVALID_FUNDED_AMOUNT")
    if commission_rate_bps < 0 or commission_rate_bps > MAX_COMMISSION_BPS:
        raise BrokerValidationError(
            f"commission_rate_bps must be between 0 and {MAX_COMMISSION_BPS}.",
            error_code="INVALID_COMMISSION_BPS",
        )
    return (int(funded_amount_cents) * int(commission_rate_bps)) // 10_000


def _resolve_tenant_user_id(db: Session, tenant_id: int, requested_user_id: Optional[int], fallback_user_id: int) -> int:
    if requested_user_id is None:
        return fallback_user_id
    exists = (
        db.query(UserModel.id)
        .filter(UserModel.id == requested_user_id, UserModel.tenant_id == tenant_id)
        .first()
    )
    if not exists:
        raise BrokerNotFoundError("Assigned owner user not found in this organization.", error_code="OWNER_NOT_FOUND")
    return requested_user_id


# ---------------------------------------------------------------------------
# SERIALIZERS
# ---------------------------------------------------------------------------
def serialize_lender(lender: BrokerLenderModel) -> dict:
    return {
        "id": lender.id,
        "code": lender.code,
        "name": lender.name,
        "lender_type": lender.lender_type,
        "default_commission_bps": int(lender.default_commission_bps or 0),
        "default_commission_percent": round(int(lender.default_commission_bps or 0) / 100.0, 2),
        "contact_name": lender.contact_name,
        "contact_email": lender.contact_email,
        "contact_phone": lender.contact_phone,
        "notes": lender.notes,
        "is_active": bool(lender.is_active),
        "created_by_user_id": lender.created_by_user_id,
        "created_at": lender.created_at.isoformat() if lender.created_at else None,
        "updated_at": lender.updated_at.isoformat() if lender.updated_at else None,
    }


def serialize_submission(sub: BrokerSubmissionModel) -> dict:
    lender = sub.lender
    return {
        "id": sub.id,
        "broker_case_id": sub.broker_case_id,
        "lender_id": sub.lender_id,
        "lender_code": lender.code if lender else None,
        "lender_name": lender.name if lender else None,
        "lender_type": lender.lender_type if lender else None,
        "status": sub.status,
        "external_reference": sub.external_reference,
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
        "decided_at": sub.decided_at.isoformat() if sub.decided_at else None,
        "offered_amount_cents": int(sub.offered_amount_cents) if sub.offered_amount_cents is not None else None,
        "offered_rate_bps": int(sub.offered_rate_bps) if sub.offered_rate_bps is not None else None,
        "offered_term_months": int(sub.offered_term_months) if sub.offered_term_months is not None else None,
        "offered_monthly_payment_cents": (
            int(sub.offered_monthly_payment_cents) if sub.offered_monthly_payment_cents is not None else None
        ),
        "offered_commission_bps": int(sub.offered_commission_bps) if sub.offered_commission_bps is not None else None,
        "decision_notes": sub.decision_notes,
        "created_by_user_id": sub.created_by_user_id,
        "updated_by_user_id": sub.updated_by_user_id,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
        "updated_at": sub.updated_at.isoformat() if sub.updated_at else None,
    }


def serialize_commission(comm: BrokerCommissionModel) -> dict:
    lender = comm.lender
    bcase = comm.broker_case
    return {
        "id": comm.id,
        "commission_number": comm.commission_number,
        "broker_case_id": comm.broker_case_id,
        "case_number": bcase.case_number if bcase else None,
        "submission_id": comm.submission_id,
        "lender_id": comm.lender_id,
        "lender_code": lender.code if lender else None,
        "lender_name": lender.name if lender else None,
        "currency": comm.currency,
        "funded_amount_cents": int(comm.funded_amount_cents),
        "commission_rate_bps": int(comm.commission_rate_bps),
        "commission_rate_percent": round(int(comm.commission_rate_bps) / 100.0, 2),
        "expected_commission_cents": int(comm.expected_commission_cents),
        "paid_commission_cents": int(comm.paid_commission_cents or 0),
        "status": comm.status,
        "expected_pay_date": comm.expected_pay_date.isoformat() if comm.expected_pay_date else None,
        "invoiced_at": comm.invoiced_at.isoformat() if comm.invoiced_at else None,
        "paid_at": comm.paid_at.isoformat() if comm.paid_at else None,
        "payment_reference": comm.payment_reference,
        "notes": comm.notes,
        "recorded_by_user_id": comm.recorded_by_user_id,
        "updated_by_user_id": comm.updated_by_user_id,
        "created_at": comm.created_at.isoformat() if comm.created_at else None,
        "updated_at": comm.updated_at.isoformat() if comm.updated_at else None,
    }


def serialize_case_summary(bcase: BrokerCaseModel) -> dict:
    cust = bcase.customer
    subs = bcase.submissions or []
    selected = next((s for s in subs if s.status == "SELECTED"), None)
    approved_count = sum(1 for s in subs if s.status in ("APPROVED", "SELECTED"))
    return {
        "id": bcase.id,
        "case_number": bcase.case_number,
        "customer_id": bcase.customer_id,
        "customer_number": cust.customer_number if cust else None,
        "customer_name": cust.full_legal_name if cust else None,
        "customer_phone": cust.phone_primary if cust else None,
        "source_lead_id": bcase.source_lead_id,
        "status": bcase.status,
        "product_type": bcase.product_type,
        "currency": bcase.currency,
        "requested_amount_cents": int(bcase.requested_amount_cents),
        "requested_term_months": bcase.requested_term_months,
        "purpose": bcase.purpose,
        "funded_amount_cents": int(bcase.funded_amount_cents) if bcase.funded_amount_cents is not None else None,
        "funded_at": bcase.funded_at.isoformat() if bcase.funded_at else None,
        "external_loan_reference": bcase.external_loan_reference,
        "owner_user_id": bcase.owner_user_id,
        "created_by_user_id": bcase.created_by_user_id,
        "closed_by_user_id": bcase.closed_by_user_id,
        "closed_at": bcase.closed_at.isoformat() if bcase.closed_at else None,
        "closed_reason": bcase.closed_reason,
        "notes": bcase.notes,
        "submissions_count": len(subs),
        "approved_offers_count": approved_count,
        "selected_submission_id": selected.id if selected else None,
        "selected_lender_name": (selected.lender.name if (selected and selected.lender) else None),
        "created_at": bcase.created_at.isoformat() if bcase.created_at else None,
        "updated_at": bcase.updated_at.isoformat() if bcase.updated_at else None,
    }


def serialize_case_detail(
    bcase: BrokerCaseModel,
    commission: Optional[BrokerCommissionModel] = None,
) -> dict:
    data = serialize_case_summary(bcase)
    data["submissions"] = [serialize_submission(s) for s in (bcase.submissions or [])]
    data["commission"] = serialize_commission(commission) if commission else None
    return data


# ---------------------------------------------------------------------------
# LENDERS CATALOG OPERATIONS
# ---------------------------------------------------------------------------
def list_lenders(
    db: Session,
    tenant_id: int,
    *,
    active_only: bool = False,
    search: Optional[str] = None,
) -> list[dict]:
    query = db.query(BrokerLenderModel).filter(BrokerLenderModel.tenant_id == tenant_id)
    if active_only:
        query = query.filter(BrokerLenderModel.is_active.is_(True))
    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                BrokerLenderModel.code.ilike(term),
                BrokerLenderModel.name.ilike(term),
                BrokerLenderModel.contact_name.ilike(term),
            )
        )
    rows = query.order_by(BrokerLenderModel.name.asc(), BrokerLenderModel.id.asc()).all()
    return [serialize_lender(r) for r in rows]


def create_lender(
    db: Session,
    tenant_id: int,
    actor: UserModel,
    *,
    code: str,
    name: str,
    lender_type: str = "BANK",
    default_commission_bps: int = 0,
    contact_name: Optional[str] = None,
    contact_email: Optional[str] = None,
    contact_phone: Optional[str] = None,
    notes: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict:
    code_clean = (code or "").strip().upper()
    name_clean = (name or "").strip()
    ltype = (lender_type or "BANK").strip().upper()

    if not LENDER_CODE_RE.match(code_clean):
        raise BrokerValidationError(
            "Invalid lender code. Use 2-40 uppercase alphanumeric characters, hyphens, or underscores.",
            error_code="INVALID_LENDER_CODE",
        )
    if len(name_clean) < 2:
        raise BrokerValidationError("Lender name must be at least 2 characters.", error_code="INVALID_LENDER_NAME")
    if ltype not in VALID_BROKER_LENDER_TYPES:
        raise BrokerValidationError(
            f"Invalid lender_type. Allowed: {', '.join(sorted(VALID_BROKER_LENDER_TYPES))}.",
            error_code="INVALID_LENDER_TYPE",
        )
    if default_commission_bps < 0 or default_commission_bps > MAX_COMMISSION_BPS:
        raise BrokerValidationError(
            f"default_commission_bps must be between 0 and {MAX_COMMISSION_BPS}.",
            error_code="INVALID_COMMISSION_BPS",
        )

    dup = (
        db.query(BrokerLenderModel.id)
        .filter(BrokerLenderModel.tenant_id == tenant_id, BrokerLenderModel.code == code_clean)
        .first()
    )
    if dup:
        raise BrokerConflictError(
            f"A lender with code '{code_clean}' already exists in this organization.",
            error_code="DUPLICATE_LENDER_CODE",
        )

    now_utc = datetime.now(timezone.utc)
    try:
        lender = BrokerLenderModel(
            tenant_id=tenant_id,
            code=code_clean,
            name=name_clean,
            lender_type=ltype,
            default_commission_bps=int(default_commission_bps),
            contact_name=contact_name.strip() if contact_name and contact_name.strip() else None,
            contact_email=contact_email.strip().lower() if contact_email and contact_email.strip() else None,
            contact_phone=contact_phone.strip() if contact_phone and contact_phone.strip() else None,
            notes=notes.strip() if notes and notes.strip() else None,
            is_active=True,
            created_by_user_id=actor.id,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(lender)
        db.flush()

        record_audit_event(
            db,
            "BROKER_LENDER_CREATED",
            "broker_lender",
            "SUCCESS",
            actor=actor,
            resource_id=lender.code,
            ip_address=ip_address,
            details={
                "lender_id": lender.id,
                "code": lender.code,
                "lender_type": lender.lender_type,
                "default_commission_bps": lender.default_commission_bps,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        db.refresh(lender)
        return serialize_lender(lender)
    except BrokerServiceError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise BrokerConflictError(
            f"A lender with code '{code_clean}' already exists in this organization.",
            error_code="DUPLICATE_LENDER_CODE",
        )


def update_lender(
    db: Session,
    tenant_id: int,
    lender_id: int,
    actor: UserModel,
    *,
    name: Optional[str] = None,
    lender_type: Optional[str] = None,
    default_commission_bps: Optional[int] = None,
    contact_name: Optional[str] = None,
    contact_email: Optional[str] = None,
    contact_phone: Optional[str] = None,
    notes: Optional[str] = None,
    is_active: Optional[bool] = None,
    fields_set: Optional[set[str]] = None,
    ip_address: Optional[str] = None,
) -> dict:
    updated_fields: list[str] = []
    provided = fields_set or set()
    try:
        lender = (
            db.query(BrokerLenderModel)
            .filter(BrokerLenderModel.id == lender_id, BrokerLenderModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not lender:
            raise BrokerNotFoundError("Lender not found.", error_code="LENDER_NOT_FOUND")

        if name is not None:
            name_clean = name.strip()
            if len(name_clean) < 2:
                raise BrokerValidationError("Lender name must be at least 2 characters.", error_code="INVALID_LENDER_NAME")
            lender.name = name_clean
            updated_fields.append("name")

        if lender_type is not None:
            ltype = lender_type.strip().upper()
            if ltype not in VALID_BROKER_LENDER_TYPES:
                raise BrokerValidationError(
                    f"Invalid lender_type. Allowed: {', '.join(sorted(VALID_BROKER_LENDER_TYPES))}.",
                    error_code="INVALID_LENDER_TYPE",
                )
            lender.lender_type = ltype
            updated_fields.append("lender_type")

        if default_commission_bps is not None:
            if default_commission_bps < 0 or default_commission_bps > MAX_COMMISSION_BPS:
                raise BrokerValidationError(
                    f"default_commission_bps must be between 0 and {MAX_COMMISSION_BPS}.",
                    error_code="INVALID_COMMISSION_BPS",
                )
            lender.default_commission_bps = int(default_commission_bps)
            updated_fields.append("default_commission_bps")

        if "contact_name" in provided or contact_name is not None:
            lender.contact_name = contact_name.strip() if contact_name and contact_name.strip() else None
            updated_fields.append("contact_name")

        if "contact_email" in provided or contact_email is not None:
            lender.contact_email = contact_email.strip().lower() if contact_email and contact_email.strip() else None
            updated_fields.append("contact_email")

        if "contact_phone" in provided or contact_phone is not None:
            lender.contact_phone = contact_phone.strip() if contact_phone and contact_phone.strip() else None
            updated_fields.append("contact_phone")

        if "notes" in provided or notes is not None:
            lender.notes = notes.strip() if notes and notes.strip() else None
            updated_fields.append("notes")

        if is_active is not None:
            lender.is_active = bool(is_active)
            updated_fields.append("is_active")

        lender.updated_at = datetime.now(timezone.utc)

        record_audit_event(
            db,
            "BROKER_LENDER_UPDATED",
            "broker_lender",
            "SUCCESS",
            actor=actor,
            resource_id=lender.code,
            ip_address=ip_address,
            details={
                "lender_id": lender.id,
                "is_active": lender.is_active,
                "updated_fields": ",".join(updated_fields),
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        db.refresh(lender)
        return serialize_lender(lender)
    except BrokerServiceError:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# BROKER CASES OPERATIONS
# ---------------------------------------------------------------------------
def _load_case_with_relations(db: Session, tenant_id: int, case_id: int) -> Optional[BrokerCaseModel]:
    return (
        db.query(BrokerCaseModel)
        .options(
            joinedload(BrokerCaseModel.customer),
            selectinload(BrokerCaseModel.submissions).joinedload(BrokerSubmissionModel.lender),
        )
        .filter(BrokerCaseModel.id == case_id, BrokerCaseModel.tenant_id == tenant_id)
        .first()
    )


def _load_case_commission(db: Session, tenant_id: int, case_id: int) -> Optional[BrokerCommissionModel]:
    return (
        db.query(BrokerCommissionModel)
        .options(joinedload(BrokerCommissionModel.lender), joinedload(BrokerCommissionModel.broker_case))
        .filter(BrokerCommissionModel.tenant_id == tenant_id, BrokerCommissionModel.broker_case_id == case_id)
        .first()
    )


def list_cases(
    db: Session,
    tenant_id: int,
    *,
    status: Optional[str] = None,
    customer_id: Optional[int] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    query = db.query(BrokerCaseModel).filter(BrokerCaseModel.tenant_id == tenant_id)

    if status and status != "ALL":
        st = status.strip().upper()
        if st not in VALID_BROKER_CASE_STATUSES:
            raise BrokerValidationError("Invalid broker case status filter.", error_code="INVALID_STATUS")
        query = query.filter(BrokerCaseModel.status == st)

    if customer_id is not None:
        query = query.filter(BrokerCaseModel.customer_id == customer_id)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                BrokerCaseModel.case_number.ilike(term),
                BrokerCaseModel.product_type.ilike(term),
                BrokerCaseModel.purpose.ilike(term),
                BrokerCaseModel.external_loan_reference.ilike(term),
            )
        )

    total = query.count()
    offset = (page - 1) * page_size
    items = (
        query.options(
            joinedload(BrokerCaseModel.customer),
            selectinload(BrokerCaseModel.submissions).joinedload(BrokerSubmissionModel.lender),
        )
        .order_by(BrokerCaseModel.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [serialize_case_summary(c) for c in items],
    }


def get_case_detail(db: Session, tenant_id: int, case_id: int) -> dict:
    bcase = _load_case_with_relations(db, tenant_id, case_id)
    if not bcase:
        raise BrokerNotFoundError("Broker case not found.", error_code="CASE_NOT_FOUND")
    comm = _load_case_commission(db, tenant_id, case_id)
    return serialize_case_detail(bcase, commission=comm)


def create_case(
    db: Session,
    tenant_id: int,
    actor: UserModel,
    *,
    idempotency_key: str,
    customer_id: int,
    source_lead_id: Optional[int],
    product_type: str,
    requested_amount_pesos: Optional[float],
    requested_amount_cents: Optional[int],
    requested_term_months: Optional[int] = None,
    purpose: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    notes: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict:
    idem_clean = (idempotency_key or "").strip()
    if len(idem_clean) < 8 or len(idem_clean) > 64:
        raise BrokerValidationError("idempotency_key must be between 8 and 64 characters.", error_code="INVALID_IDEMPOTENCY_KEY")

    product_clean = (product_type or "").strip()
    if len(product_clean) < 2:
        raise BrokerValidationError("product_type must be at least 2 characters.", error_code="INVALID_PRODUCT_TYPE")

    amount_cents = parse_money_to_cents(
        requested_amount_pesos,
        requested_amount_cents,
        field_name="requested_amount",
        required=True,
        allow_zero=False,
    )

    if requested_term_months is not None and (requested_term_months < 1 or requested_term_months > 360):
        raise BrokerValidationError("requested_term_months must be between 1 and 360.", error_code="INVALID_TERM")

    customer = (
        db.query(CustomerModel)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == tenant_id)
        .first()
    )
    if not customer:
        raise BrokerNotFoundError("Customer not found.", error_code="CUSTOMER_NOT_FOUND")
    if customer.status != "ACTIVE":
        raise BrokerValidationError(
            "Cannot open a broker case for a non-active customer.",
            error_code="CUSTOMER_NOT_ACTIVE",
        )

    if source_lead_id is not None:
        lead = (
            db.query(LeadModel)
            .filter(LeadModel.id == source_lead_id, LeadModel.tenant_id == tenant_id)
            .first()
        )
        if not lead:
            raise BrokerNotFoundError("Source lead not found.", error_code="LEAD_NOT_FOUND")
        if lead.stage != "CONVERTED" or lead.converted_customer_id != customer.id:
            raise BrokerValidationError(
                "Source lead must be in CONVERTED stage and linked to the selected customer.",
                error_code="LEAD_CUSTOMER_MISMATCH",
            )

    owner_id = _resolve_tenant_user_id(db, tenant_id, owner_user_id, customer.owner_user_id or actor.id)

    canonical = {
        "customer_id": customer_id,
        "source_lead_id": source_lead_id,
        "product_type": product_clean,
        "requested_amount_cents": amount_cents,
        "requested_term_months": requested_term_months,
        "purpose": purpose.strip() if purpose and purpose.strip() else None,
    }
    payload_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()

    existing = (
        db.query(BrokerCaseModel)
        .filter(BrokerCaseModel.tenant_id == tenant_id, BrokerCaseModel.idempotency_key == idem_clean)
        .first()
    )
    if existing:
        if existing.request_payload_hash == payload_hash:
            loaded = _load_case_with_relations(db, tenant_id, existing.id)
            comm = _load_case_commission(db, tenant_id, existing.id)
            return {"idempotent_replay": True, "case": serialize_case_detail(loaded, commission=comm)}
        raise BrokerConflictError(
            "Idempotency key was already used with different broker case parameters.",
            error_code="IDEMPOTENCY_CONFLICT",
        )

    now_utc = datetime.now(timezone.utc)
    try:
        case_number = generate_next_broker_case_number(db, tenant_id)
        new_case = BrokerCaseModel(
            tenant_id=tenant_id,
            case_number=case_number,
            idempotency_key=idem_clean,
            request_payload_hash=payload_hash,
            customer_id=customer.id,
            source_lead_id=source_lead_id,
            status="DRAFT",
            product_type=product_clean,
            currency="MXN",
            requested_amount_cents=amount_cents,
            requested_term_months=requested_term_months,
            purpose=purpose.strip() if purpose and purpose.strip() else None,
            owner_user_id=owner_id,
            created_by_user_id=actor.id,
            notes=notes.strip() if notes and notes.strip() else None,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(new_case)
        db.flush()

        record_audit_event(
            db,
            "BROKER_CASE_CREATED",
            "broker_case",
            "SUCCESS",
            actor=actor,
            resource_id=new_case.case_number,
            ip_address=ip_address,
            details={
                "case_number": new_case.case_number,
                "customer_id": new_case.customer_id,
                "requested_amount_cents": new_case.requested_amount_cents,
                "product_type": new_case.product_type,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        loaded = _load_case_with_relations(db, tenant_id, new_case.id)
        return {"idempotent_replay": False, "case": serialize_case_detail(loaded, commission=None)}
    except BrokerServiceError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing_race = (
            db.query(BrokerCaseModel)
            .filter(BrokerCaseModel.tenant_id == tenant_id, BrokerCaseModel.idempotency_key == idem_clean)
            .first()
        )
        if existing_race and existing_race.request_payload_hash == payload_hash:
            loaded = _load_case_with_relations(db, tenant_id, existing_race.id)
            comm = _load_case_commission(db, tenant_id, existing_race.id)
            return {"idempotent_replay": True, "case": serialize_case_detail(loaded, commission=comm)}
        raise BrokerConflictError("Idempotency key conflict under concurrency.", error_code="IDEMPOTENCY_CONFLICT")


def update_case(
    db: Session,
    tenant_id: int,
    case_id: int,
    actor: UserModel,
    *,
    product_type: Optional[str] = None,
    requested_amount_pesos: Optional[float] = None,
    requested_amount_cents: Optional[int] = None,
    requested_term_months: Optional[int] = None,
    purpose: Optional[str] = None,
    owner_user_id: Optional[int] = None,
    status: Optional[str] = None,
    closed_reason: Optional[str] = None,
    notes: Optional[str] = None,
    fields_set: Optional[set[str]] = None,
    ip_address: Optional[str] = None,
) -> dict:
    updated_fields: list[str] = []
    provided = fields_set or set()
    try:
        bcase = (
            db.query(BrokerCaseModel)
            .filter(BrokerCaseModel.id == case_id, BrokerCaseModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not bcase:
            raise BrokerNotFoundError("Broker case not found.", error_code="CASE_NOT_FOUND")
        if bcase.status in TERMINAL_BROKER_CASE_STATUSES:
            raise BrokerConflictError(
                f"Broker case {bcase.case_number} is terminal ({bcase.status}) and cannot be modified.",
                error_code="CASE_TERMINAL",
            )

        if product_type is not None:
            pclean = product_type.strip()
            if len(pclean) < 2:
                raise BrokerValidationError("product_type must be at least 2 characters.", error_code="INVALID_PRODUCT_TYPE")
            bcase.product_type = pclean
            updated_fields.append("product_type")

        if requested_amount_pesos is not None or requested_amount_cents is not None:
            bcase.requested_amount_cents = parse_money_to_cents(
                requested_amount_pesos,
                requested_amount_cents,
                field_name="requested_amount",
                required=True,
                allow_zero=False,
            )
            updated_fields.append("requested_amount_cents")

        if "requested_term_months" in provided or requested_term_months is not None:
            if requested_term_months is not None and (requested_term_months < 1 or requested_term_months > 360):
                raise BrokerValidationError("requested_term_months must be between 1 and 360.", error_code="INVALID_TERM")
            bcase.requested_term_months = requested_term_months
            updated_fields.append("requested_term_months")

        if "purpose" in provided or purpose is not None:
            bcase.purpose = purpose.strip() if purpose and purpose.strip() else None
            updated_fields.append("purpose")

        if owner_user_id is not None:
            bcase.owner_user_id = _resolve_tenant_user_id(db, tenant_id, owner_user_id, bcase.owner_user_id)
            updated_fields.append("owner_user_id")

        if "notes" in provided or notes is not None:
            bcase.notes = notes.strip() if notes and notes.strip() else None
            updated_fields.append("notes")

        if status is not None:
            new_st = status.strip().upper()
            if new_st not in VALID_BROKER_CASE_STATUSES:
                raise BrokerValidationError("Invalid broker case status.", error_code="INVALID_STATUS")
            if new_st == "FUNDED":
                raise BrokerConflictError(
                    "Use POST /api/broker/cases/{case_id}/fund to mark a case as FUNDED.",
                    error_code="USE_FUND_ENDPOINT",
                )
            if new_st in ("CLOSED_LOST", "CANCELLED"):
                reason_clean = (closed_reason or "").strip()
                if len(reason_clean) < 3:
                    raise BrokerValidationError(
                        f"closed_reason (at least 3 characters) is required when moving a case to {new_st}.",
                        error_code="CLOSED_REASON_REQUIRED",
                    )
                now_utc = datetime.now(timezone.utc)
                bcase.status = new_st
                bcase.closed_by_user_id = actor.id
                bcase.closed_at = now_utc
                bcase.closed_reason = reason_clean
                updated_fields.append("status")
            elif new_st != bcase.status:
                allowed_non_terminal = {
                    "DRAFT": {"SUBMITTED", "OFFERS_RECEIVED"},
                    "SUBMITTED": {"OFFERS_RECEIVED"},
                    "OFFERS_RECEIVED": set(),
                }
                if new_st not in allowed_non_terminal.get(bcase.status, set()):
                    raise BrokerConflictError(
                        f"Invalid case status transition from {bcase.status} to {new_st}.",
                        error_code="INVALID_CASE_TRANSITION",
                    )
                bcase.status = new_st
                updated_fields.append("status")

        bcase.updated_at = datetime.now(timezone.utc)

        record_audit_event(
            db,
            "BROKER_CASE_UPDATED",
            "broker_case",
            "SUCCESS",
            actor=actor,
            resource_id=bcase.case_number,
            ip_address=ip_address,
            details={
                "case_number": bcase.case_number,
                "status": bcase.status,
                "updated_fields": ",".join(updated_fields),
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        loaded = _load_case_with_relations(db, tenant_id, bcase.id)
        comm = _load_case_commission(db, tenant_id, bcase.id)
        return serialize_case_detail(loaded, commission=comm)
    except BrokerServiceError:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# SUBMISSIONS & OFFERS OPERATIONS
# ---------------------------------------------------------------------------
def create_submission(
    db: Session,
    tenant_id: int,
    case_id: int,
    actor: UserModel,
    *,
    lender_id: int,
    status: str = "SUBMITTED",
    external_reference: Optional[str] = None,
    offered_amount_pesos: Optional[float] = None,
    offered_amount_cents: Optional[int] = None,
    offered_rate_bps: Optional[int] = None,
    offered_term_months: Optional[int] = None,
    offered_monthly_payment_pesos: Optional[float] = None,
    offered_monthly_payment_cents: Optional[int] = None,
    offered_commission_bps: Optional[int] = None,
    decision_notes: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict:
    st = (status or "SUBMITTED").strip().upper()
    if st not in ("DRAFT", "SUBMITTED"):
        raise BrokerValidationError(
            "Initial submission status must be DRAFT or SUBMITTED.",
            error_code="INVALID_INITIAL_SUBMISSION_STATUS",
        )

    amt_cents = parse_money_to_cents(
        offered_amount_pesos, offered_amount_cents, field_name="offered_amount", required=False, allow_zero=False
    )
    monthly_cents = parse_money_to_cents(
        offered_monthly_payment_pesos,
        offered_monthly_payment_cents,
        field_name="offered_monthly_payment",
        required=False,
        allow_zero=True,
    )
    if offered_rate_bps is not None and offered_rate_bps < 0:
        raise BrokerValidationError("offered_rate_bps cannot be negative.", error_code="INVALID_RATE")
    if offered_term_months is not None and (offered_term_months < 1 or offered_term_months > 360):
        raise BrokerValidationError("offered_term_months must be between 1 and 360.", error_code="INVALID_TERM")
    if offered_commission_bps is not None and (offered_commission_bps < 0 or offered_commission_bps > MAX_COMMISSION_BPS):
        raise BrokerValidationError(
            f"offered_commission_bps must be between 0 and {MAX_COMMISSION_BPS}.",
            error_code="INVALID_COMMISSION_BPS",
        )

    now_utc = datetime.now(timezone.utc)
    try:
        bcase = (
            db.query(BrokerCaseModel)
            .filter(BrokerCaseModel.id == case_id, BrokerCaseModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not bcase:
            raise BrokerNotFoundError("Broker case not found.", error_code="CASE_NOT_FOUND")
        if bcase.status in TERMINAL_BROKER_CASE_STATUSES:
            raise BrokerConflictError(
                f"Cannot add submissions to a terminal broker case ({bcase.status}).",
                error_code="CASE_TERMINAL",
            )

        lender = (
            db.query(BrokerLenderModel)
            .filter(BrokerLenderModel.id == lender_id, BrokerLenderModel.tenant_id == tenant_id)
            .first()
        )
        if not lender:
            raise BrokerNotFoundError("Lender not found.", error_code="LENDER_NOT_FOUND")
        if not lender.is_active:
            raise BrokerValidationError("Cannot submit to an inactive lender.", error_code="LENDER_INACTIVE")

        dup = (
            db.query(BrokerSubmissionModel.id)
            .filter(
                BrokerSubmissionModel.tenant_id == tenant_id,
                BrokerSubmissionModel.broker_case_id == bcase.id,
                BrokerSubmissionModel.lender_id == lender.id,
            )
            .first()
        )
        if dup:
            raise BrokerConflictError(
                f"This broker case already has a submission for lender '{lender.code}'.",
                error_code="DUPLICATE_SUBMISSION",
            )

        sub = BrokerSubmissionModel(
            tenant_id=tenant_id,
            broker_case_id=bcase.id,
            lender_id=lender.id,
            status=st,
            external_reference=external_reference.strip() if external_reference and external_reference.strip() else None,
            submitted_at=now_utc if st == "SUBMITTED" else None,
            offered_amount_cents=amt_cents,
            offered_rate_bps=offered_rate_bps,
            offered_term_months=offered_term_months,
            offered_monthly_payment_cents=monthly_cents,
            offered_commission_bps=(
                offered_commission_bps if offered_commission_bps is not None else lender.default_commission_bps
            ),
            decision_notes=decision_notes.strip() if decision_notes and decision_notes.strip() else None,
            created_by_user_id=actor.id,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(sub)

        if st == "SUBMITTED" and bcase.status == "DRAFT":
            bcase.status = "SUBMITTED"
            bcase.updated_at = now_utc

        db.flush()
        record_audit_event(
            db,
            "BROKER_SUBMISSION_CREATED",
            "broker_submission",
            "SUCCESS",
            actor=actor,
            resource_id=f"{bcase.case_number}:{lender.code}",
            ip_address=ip_address,
            details={
                "case_number": bcase.case_number,
                "submission_id": sub.id,
                "lender_id": lender.id,
                "lender_code": lender.code,
                "status": sub.status,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        sub_loaded = (
            db.query(BrokerSubmissionModel)
            .options(joinedload(BrokerSubmissionModel.lender))
            .filter(BrokerSubmissionModel.id == sub.id, BrokerSubmissionModel.tenant_id == tenant_id)
            .one()
        )
        return serialize_submission(sub_loaded)
    except BrokerServiceError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise BrokerConflictError("Duplicate submission for this case and lender.", error_code="DUPLICATE_SUBMISSION")


def update_submission(
    db: Session,
    tenant_id: int,
    case_id: int,
    submission_id: int,
    actor: UserModel,
    *,
    status: Optional[str] = None,
    external_reference: Optional[str] = None,
    offered_amount_pesos: Optional[float] = None,
    offered_amount_cents: Optional[int] = None,
    offered_rate_bps: Optional[int] = None,
    offered_term_months: Optional[int] = None,
    offered_monthly_payment_pesos: Optional[float] = None,
    offered_monthly_payment_cents: Optional[int] = None,
    offered_commission_bps: Optional[int] = None,
    decision_notes: Optional[str] = None,
    fields_set: Optional[set[str]] = None,
    ip_address: Optional[str] = None,
) -> dict:
    provided = fields_set or set()
    now_utc = datetime.now(timezone.utc)
    try:
        bcase = (
            db.query(BrokerCaseModel)
            .filter(BrokerCaseModel.id == case_id, BrokerCaseModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not bcase:
            raise BrokerNotFoundError("Broker case not found.", error_code="CASE_NOT_FOUND")
        if bcase.status in TERMINAL_BROKER_CASE_STATUSES:
            raise BrokerConflictError(
                f"Cannot modify submissions on a terminal broker case ({bcase.status}).",
                error_code="CASE_TERMINAL",
            )

        sub = (
            db.query(BrokerSubmissionModel)
            .filter(
                BrokerSubmissionModel.id == submission_id,
                BrokerSubmissionModel.broker_case_id == bcase.id,
                BrokerSubmissionModel.tenant_id == tenant_id,
            )
            .with_for_update()
            .first()
        )
        if not sub:
            raise BrokerNotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")
        if sub.status in TERMINAL_BROKER_SUBMISSION_STATUSES:
            raise BrokerConflictError(
                f"Submission in status {sub.status} cannot be updated via PATCH.",
                error_code="SUBMISSION_TERMINAL",
            )

        if "external_reference" in provided or external_reference is not None:
            sub.external_reference = (
                external_reference.strip() if external_reference and external_reference.strip() else None
            )

        if offered_amount_pesos is not None or offered_amount_cents is not None:
            sub.offered_amount_cents = parse_money_to_cents(
                offered_amount_pesos,
                offered_amount_cents,
                field_name="offered_amount",
                required=True,
                allow_zero=False,
            )

        if offered_rate_bps is not None:
            if offered_rate_bps < 0:
                raise BrokerValidationError("offered_rate_bps cannot be negative.", error_code="INVALID_RATE")
            sub.offered_rate_bps = int(offered_rate_bps)

        if offered_term_months is not None:
            if offered_term_months < 1 or offered_term_months > 360:
                raise BrokerValidationError("offered_term_months must be between 1 and 360.", error_code="INVALID_TERM")
            sub.offered_term_months = int(offered_term_months)

        if offered_monthly_payment_pesos is not None or offered_monthly_payment_cents is not None:
            sub.offered_monthly_payment_cents = parse_money_to_cents(
                offered_monthly_payment_pesos,
                offered_monthly_payment_cents,
                field_name="offered_monthly_payment",
                required=True,
                allow_zero=True,
            )

        if offered_commission_bps is not None:
            if offered_commission_bps < 0 or offered_commission_bps > MAX_COMMISSION_BPS:
                raise BrokerValidationError(
                    f"offered_commission_bps must be between 0 and {MAX_COMMISSION_BPS}.",
                    error_code="INVALID_COMMISSION_BPS",
                )
            sub.offered_commission_bps = int(offered_commission_bps)

        if "decision_notes" in provided or decision_notes is not None:
            sub.decision_notes = decision_notes.strip() if decision_notes and decision_notes.strip() else None

        if status is not None:
            new_st = status.strip().upper()
            if new_st not in VALID_BROKER_SUBMISSION_STATUSES:
                raise BrokerValidationError("Invalid submission status.", error_code="INVALID_STATUS")
            if new_st == "SELECTED":
                raise BrokerConflictError(
                    "Use POST /api/broker/cases/{case_id}/submissions/{submission_id}/select to select an offer.",
                    error_code="USE_SELECT_ENDPOINT",
                )
            allowed_transitions = {
                "DRAFT": {"DRAFT", "SUBMITTED", "WITHDRAWN"},
                "SUBMITTED": {"SUBMITTED", "IN_UNDERWRITING", "APPROVED", "DECLINED", "WITHDRAWN"},
                "IN_UNDERWRITING": {"IN_UNDERWRITING", "APPROVED", "DECLINED", "WITHDRAWN"},
                "APPROVED": {"APPROVED", "DECLINED", "WITHDRAWN"},
            }
            if new_st not in allowed_transitions.get(sub.status, set()):
                raise BrokerConflictError(
                    f"Invalid submission transition from {sub.status} to {new_st}.",
                    error_code="INVALID_SUBMISSION_TRANSITION",
                )

            if new_st == "APPROVED":
                if not sub.offered_amount_cents or int(sub.offered_amount_cents) <= 0:
                    raise BrokerValidationError(
                        "An APPROVED offer requires offered_amount_cents/offered_amount_pesos greater than zero.",
                        error_code="OFFER_AMOUNT_REQUIRED",
                    )
                if sub.decided_at is None:
                    sub.decided_at = now_utc
                if sub.submitted_at is None:
                    sub.submitted_at = now_utc
                if bcase.status in ("DRAFT", "SUBMITTED"):
                    bcase.status = "OFFERS_RECEIVED"
                    bcase.updated_at = now_utc
            elif new_st == "SUBMITTED":
                if sub.submitted_at is None:
                    sub.submitted_at = now_utc
                if bcase.status == "DRAFT":
                    bcase.status = "SUBMITTED"
                    bcase.updated_at = now_utc
            elif new_st in ("DECLINED", "WITHDRAWN"):
                sub.decided_at = now_utc

            sub.status = new_st

        sub.updated_by_user_id = actor.id
        sub.updated_at = now_utc

        db.flush()
        record_audit_event(
            db,
            "BROKER_SUBMISSION_UPDATED",
            "broker_submission",
            "SUCCESS",
            actor=actor,
            resource_id=f"{bcase.case_number}:sub-{sub.id}",
            ip_address=ip_address,
            details={
                "case_number": bcase.case_number,
                "submission_id": sub.id,
                "status": sub.status,
                "offered_amount_cents": sub.offered_amount_cents,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        sub_loaded = (
            db.query(BrokerSubmissionModel)
            .options(joinedload(BrokerSubmissionModel.lender))
            .filter(BrokerSubmissionModel.id == sub.id, BrokerSubmissionModel.tenant_id == tenant_id)
            .one()
        )
        return serialize_submission(sub_loaded)
    except BrokerServiceError:
        db.rollback()
        raise


def select_submission_offer(
    db: Session,
    tenant_id: int,
    case_id: int,
    submission_id: int,
    actor: UserModel,
    *,
    decision_notes: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict:
    now_utc = datetime.now(timezone.utc)
    try:
        bcase = (
            db.query(BrokerCaseModel)
            .filter(BrokerCaseModel.id == case_id, BrokerCaseModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not bcase:
            raise BrokerNotFoundError("Broker case not found.", error_code="CASE_NOT_FOUND")
        if bcase.status in TERMINAL_BROKER_CASE_STATUSES:
            raise BrokerConflictError(
                f"Cannot select an offer on a terminal broker case ({bcase.status}).",
                error_code="CASE_TERMINAL",
            )

        all_subs = (
            db.query(BrokerSubmissionModel)
            .filter(
                BrokerSubmissionModel.tenant_id == tenant_id,
                BrokerSubmissionModel.broker_case_id == bcase.id,
            )
            .with_for_update()
            .all()
        )
        target = next((s for s in all_subs if s.id == submission_id), None)
        if not target:
            raise BrokerNotFoundError("Submission not found.", error_code="SUBMISSION_NOT_FOUND")
        if target.status not in ("APPROVED", "SELECTED"):
            raise BrokerConflictError(
                f"Only APPROVED offers can be selected. Current status: {target.status}.",
                error_code="SUBMISSION_NOT_APPROVED",
            )
        if not target.offered_amount_cents or int(target.offered_amount_cents) <= 0:
            raise BrokerValidationError("Selected offer must have a positive offered amount.", error_code="OFFER_AMOUNT_REQUIRED")

        # Release any previously selected offer on this non-funded case before marking target SELECTED
        for other in all_subs:
            if other.id != target.id and other.status == "SELECTED":
                other.status = "APPROVED"
                other.updated_by_user_id = actor.id
                other.updated_at = now_utc
        db.flush()

        target.status = "SELECTED"
        if decision_notes is not None and decision_notes.strip():
            target.decision_notes = decision_notes.strip()
        target.updated_by_user_id = actor.id
        target.updated_at = now_utc

        if bcase.status in ("DRAFT", "SUBMITTED"):
            bcase.status = "OFFERS_RECEIVED"
        bcase.updated_at = now_utc

        db.flush()
        record_audit_event(
            db,
            "BROKER_OFFER_SELECTED",
            "broker_case",
            "SUCCESS",
            actor=actor,
            resource_id=bcase.case_number,
            ip_address=ip_address,
            details={
                "case_number": bcase.case_number,
                "submission_id": target.id,
                "lender_id": target.lender_id,
                "offered_amount_cents": target.offered_amount_cents,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        loaded = _load_case_with_relations(db, tenant_id, bcase.id)
        comm = _load_case_commission(db, tenant_id, bcase.id)
        return serialize_case_detail(loaded, commission=comm)
    except BrokerServiceError:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# FUNDING & COMMISSIONS OPERATIONS
# ---------------------------------------------------------------------------
def fund_case(
    db: Session,
    tenant_id: int,
    case_id: int,
    actor: UserModel,
    *,
    funded_amount_pesos: Optional[float] = None,
    funded_amount_cents: Optional[int] = None,
    external_loan_reference: Optional[str] = None,
    funded_at: Optional[str] = None,
    commission_rate_bps: Optional[int] = None,
    expected_pay_date: Optional[date] = None,
    notes: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> dict:
    explicit_funded = parse_money_to_cents(
        funded_amount_pesos,
        funded_amount_cents,
        field_name="funded_amount",
        required=False,
        allow_zero=False,
    )
    funded_at_dt = parse_optional_iso_datetime_utc(funded_at, field_name="funded_at") or datetime.now(timezone.utc)
    now_utc = datetime.now(timezone.utc)

    try:
        bcase = (
            db.query(BrokerCaseModel)
            .filter(BrokerCaseModel.id == case_id, BrokerCaseModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not bcase:
            raise BrokerNotFoundError("Broker case not found.", error_code="CASE_NOT_FOUND")
        if bcase.status == "FUNDED":
            raise BrokerConflictError(f"Broker case {bcase.case_number} is already FUNDED.", error_code="CASE_ALREADY_FUNDED")
        if bcase.status in TERMINAL_BROKER_CASE_STATUSES:
            raise BrokerConflictError(
                f"Cannot fund broker case {bcase.case_number} in status {bcase.status}.",
                error_code="CASE_TERMINAL",
            )

        selected_sub = (
            db.query(BrokerSubmissionModel)
            .filter(
                BrokerSubmissionModel.tenant_id == tenant_id,
                BrokerSubmissionModel.broker_case_id == bcase.id,
                BrokerSubmissionModel.status == "SELECTED",
            )
            .with_for_update()
            .first()
        )
        if not selected_sub:
            raise BrokerConflictError(
                "A SELECTED lender offer is required before marking a broker case as FUNDED.",
                error_code="NO_SELECTED_OFFER",
            )

        lender = (
            db.query(BrokerLenderModel)
            .filter(BrokerLenderModel.id == selected_sub.lender_id, BrokerLenderModel.tenant_id == tenant_id)
            .first()
        )

        effective_funded_cents = (
            explicit_funded
            if explicit_funded is not None
            else (int(selected_sub.offered_amount_cents) if selected_sub.offered_amount_cents else 0)
        )
        if effective_funded_cents <= 0:
            raise BrokerValidationError("Funded amount must be greater than zero.", error_code="INVALID_FUNDED_AMOUNT")

        if commission_rate_bps is not None:
            effective_bps = int(commission_rate_bps)
        elif selected_sub.offered_commission_bps is not None:
            effective_bps = int(selected_sub.offered_commission_bps)
        elif lender is not None:
            effective_bps = int(lender.default_commission_bps or 0)
        else:
            effective_bps = 0

        expected_comm_cents = compute_commission_cents(effective_funded_cents, effective_bps)
        commission_number = generate_next_broker_commission_number(db, tenant_id)

        bcase.status = "FUNDED"
        bcase.funded_amount_cents = effective_funded_cents
        bcase.funded_at = funded_at_dt
        if external_loan_reference and external_loan_reference.strip():
            bcase.external_loan_reference = external_loan_reference.strip()
        bcase.closed_by_user_id = actor.id
        bcase.closed_at = now_utc
        bcase.updated_at = now_utc

        comm = BrokerCommissionModel(
            tenant_id=tenant_id,
            commission_number=commission_number,
            broker_case_id=bcase.id,
            submission_id=selected_sub.id,
            lender_id=selected_sub.lender_id,
            currency=bcase.currency,
            funded_amount_cents=effective_funded_cents,
            commission_rate_bps=effective_bps,
            expected_commission_cents=expected_comm_cents,
            paid_commission_cents=0,
            status="PENDING",
            expected_pay_date=expected_pay_date,
            notes=notes.strip() if notes and notes.strip() else None,
            recorded_by_user_id=actor.id,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(comm)
        db.flush()

        record_audit_event(
            db,
            "BROKER_CASE_FUNDED",
            "broker_case",
            "SUCCESS",
            actor=actor,
            resource_id=bcase.case_number,
            ip_address=ip_address,
            details={
                "case_number": bcase.case_number,
                "submission_id": selected_sub.id,
                "lender_id": selected_sub.lender_id,
                "funded_amount_cents": effective_funded_cents,
                "commission_number": comm.commission_number,
                "expected_commission_cents": expected_comm_cents,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        loaded = _load_case_with_relations(db, tenant_id, bcase.id)
        loaded_comm = _load_case_commission(db, tenant_id, bcase.id)
        return serialize_case_detail(loaded, commission=loaded_comm)
    except BrokerServiceError:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise BrokerConflictError(
            "A commission already exists for this broker case.",
            error_code="COMMISSION_ALREADY_EXISTS",
        )


def list_commissions(
    db: Session,
    tenant_id: int,
    *,
    status: Optional[str] = None,
    lender_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    query = db.query(BrokerCommissionModel).filter(BrokerCommissionModel.tenant_id == tenant_id)
    if status and status != "ALL":
        st = status.strip().upper()
        if st not in VALID_BROKER_COMMISSION_STATUSES:
            raise BrokerValidationError("Invalid commission status filter.", error_code="INVALID_STATUS")
        query = query.filter(BrokerCommissionModel.status == st)
    if lender_id is not None:
        query = query.filter(BrokerCommissionModel.lender_id == lender_id)

    total = query.count()
    offset = (page - 1) * page_size
    items = (
        query.options(joinedload(BrokerCommissionModel.lender), joinedload(BrokerCommissionModel.broker_case))
        .order_by(BrokerCommissionModel.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [serialize_commission(c) for c in items],
    }


def update_commission(
    db: Session,
    tenant_id: int,
    commission_id: int,
    actor: UserModel,
    *,
    status: Optional[str] = None,
    paid_commission_pesos: Optional[float] = None,
    paid_commission_cents: Optional[int] = None,
    expected_pay_date: Optional[date] = None,
    payment_reference: Optional[str] = None,
    paid_at: Optional[str] = None,
    notes: Optional[str] = None,
    fields_set: Optional[set[str]] = None,
    ip_address: Optional[str] = None,
) -> dict:
    provided = fields_set or set()
    now_utc = datetime.now(timezone.utc)
    explicit_paid_cents = parse_money_to_cents(
        paid_commission_pesos,
        paid_commission_cents,
        field_name="paid_commission",
        required=False,
        allow_zero=True,
    )
    explicit_paid_at = parse_optional_iso_datetime_utc(paid_at, field_name="paid_at")

    try:
        comm = (
            db.query(BrokerCommissionModel)
            .filter(BrokerCommissionModel.id == commission_id, BrokerCommissionModel.tenant_id == tenant_id)
            .with_for_update()
            .first()
        )
        if not comm:
            raise BrokerNotFoundError("Commission not found.", error_code="COMMISSION_NOT_FOUND")
        if comm.status in TERMINAL_BROKER_COMMISSION_STATUSES:
            raise BrokerConflictError(
                f"Commission {comm.commission_number} is terminal ({comm.status}) and cannot be modified.",
                error_code="COMMISSION_TERMINAL",
            )

        if "expected_pay_date" in provided or expected_pay_date is not None:
            comm.expected_pay_date = expected_pay_date

        if "payment_reference" in provided or payment_reference is not None:
            comm.payment_reference = (
                payment_reference.strip() if payment_reference and payment_reference.strip() else None
            )

        if "notes" in provided or notes is not None:
            comm.notes = notes.strip() if notes and notes.strip() else None

        if explicit_paid_cents is not None:
            comm.paid_commission_cents = explicit_paid_cents

        if status is not None:
            new_st = status.strip().upper()
            if new_st not in VALID_BROKER_COMMISSION_STATUSES:
                raise BrokerValidationError("Invalid commission status.", error_code="INVALID_STATUS")
            allowed = {
                "PENDING": {"PENDING", "INVOICED", "PAID", "CANCELLED"},
                "INVOICED": {"INVOICED", "PAID", "CANCELLED"},
            }
            if new_st not in allowed.get(comm.status, set()):
                raise BrokerConflictError(
                    f"Invalid commission transition from {comm.status} to {new_st}.",
                    error_code="INVALID_COMMISSION_TRANSITION",
                )
            if new_st == "INVOICED" and comm.invoiced_at is None:
                comm.invoiced_at = now_utc
            elif new_st == "PAID":
                if explicit_paid_cents is None and int(comm.paid_commission_cents or 0) == 0:
                    comm.paid_commission_cents = int(comm.expected_commission_cents)
                comm.paid_at = explicit_paid_at or now_utc
            comm.status = new_st

        comm.updated_by_user_id = actor.id
        comm.updated_at = now_utc

        db.flush()
        record_audit_event(
            db,
            "BROKER_COMMISSION_UPDATED",
            "broker_commission",
            "SUCCESS",
            actor=actor,
            resource_id=comm.commission_number,
            ip_address=ip_address,
            details={
                "commission_number": comm.commission_number,
                "status": comm.status,
                "paid_commission_cents": comm.paid_commission_cents,
            },
            commit=False,
            scope="TENANT",
            tenant_id=tenant_id,
        )
        db.commit()
        loaded = (
            db.query(BrokerCommissionModel)
            .options(joinedload(BrokerCommissionModel.lender), joinedload(BrokerCommissionModel.broker_case))
            .filter(BrokerCommissionModel.id == comm.id, BrokerCommissionModel.tenant_id == tenant_id)
            .one()
        )
        return serialize_commission(loaded)
    except BrokerServiceError:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# DECOUPLED DASHBOARD & CUSTOMER SUMMARIES (EXACT INTEGER CENTS)
# ---------------------------------------------------------------------------
def _build_commission_summary_from_rows(rows: list[tuple]) -> dict:
    """Aggregate (status, count, sum_expected_cents, sum_paid_cents) tuples using exact integer cents."""
    counts = {st: 0 for st in ("PENDING", "INVOICED", "PAID", "CANCELLED")}
    expected_by_status = {st: 0 for st in ("PENDING", "INVOICED", "PAID", "CANCELLED")}
    paid_by_status = {st: 0 for st in ("PENDING", "INVOICED", "PAID", "CANCELLED")}

    for status_val, cnt, sum_expected, sum_paid in rows:
        if status_val in counts:
            counts[status_val] = int(cnt or 0)
            expected_by_status[status_val] = int(sum_expected or 0)
            paid_by_status[status_val] = int(sum_paid or 0)

    pending_cents = expected_by_status["PENDING"]
    invoiced_cents = expected_by_status["INVOICED"]
    paid_cents = paid_by_status["PAID"]
    cancelled_cents = expected_by_status["CANCELLED"]
    total_earned_count = counts["PENDING"] + counts["INVOICED"] + counts["PAID"]
    total_earned_cents = pending_cents + invoiced_cents + paid_cents
    total_expected_cents = (
        expected_by_status["PENDING"] + expected_by_status["INVOICED"] + expected_by_status["PAID"]
    )

    return {
        "pending_count": counts["PENDING"],
        "pending_cents": pending_cents,
        "invoiced_count": counts["INVOICED"],
        "invoiced_cents": invoiced_cents,
        "paid_count": counts["PAID"],
        "paid_cents": paid_cents,
        "cancelled_count": counts["CANCELLED"],
        "cancelled_cents": cancelled_cents,
        "total_earned_count": total_earned_count,
        "total_earned_cents": total_earned_cents,
        "total_expected_cents": total_expected_cents,
    }


def get_broker_dashboard_summary(
    db: Session,
    tenant_id: int,
    *,
    recent_limit: int = 10,
) -> dict:
    """Broker pipeline, funding volume, commission totals, lender counts, and recent cases (tenant-scoped)."""
    case_rows = (
        db.query(
            BrokerCaseModel.status,
            func.count(BrokerCaseModel.id),
            func.coalesce(func.sum(BrokerCaseModel.requested_amount_cents), 0),
            func.coalesce(func.sum(BrokerCaseModel.funded_amount_cents), 0),
        )
        .filter(BrokerCaseModel.tenant_id == tenant_id)
        .group_by(BrokerCaseModel.status)
        .all()
    )

    status_order = ("DRAFT", "SUBMITTED", "OFFERS_RECEIVED", "FUNDED", "CLOSED_LOST", "CANCELLED")
    cases_by_status = {st: 0 for st in status_order}
    active_pipeline_cents = 0
    funded_volume_cents = 0

    for status_val, cnt, sum_req, sum_funded in case_rows:
        cnt_int = int(cnt or 0)
        if status_val in cases_by_status:
            cases_by_status[status_val] = cnt_int
        if status_val in ACTIVE_BROKER_CASE_STATUSES:
            active_pipeline_cents += int(sum_req or 0)
        elif status_val == "FUNDED":
            funded_volume_cents += int(sum_funded or 0)

    active_cases_count = sum(cases_by_status[st] for st in ACTIVE_BROKER_CASE_STATUSES)
    total_cases_count = sum(cases_by_status.values())

    comm_rows = (
        db.query(
            BrokerCommissionModel.status,
            func.count(BrokerCommissionModel.id),
            func.coalesce(func.sum(BrokerCommissionModel.expected_commission_cents), 0),
            func.coalesce(func.sum(BrokerCommissionModel.paid_commission_cents), 0),
        )
        .filter(BrokerCommissionModel.tenant_id == tenant_id)
        .group_by(BrokerCommissionModel.status)
        .all()
    )
    commissions_summary = _build_commission_summary_from_rows(comm_rows)

    lender_rows = (
        db.query(BrokerLenderModel.is_active, func.count(BrokerLenderModel.id))
        .filter(BrokerLenderModel.tenant_id == tenant_id)
        .group_by(BrokerLenderModel.is_active)
        .all()
    )
    active_lenders_count = 0
    total_lenders_count = 0
    for is_act, cnt in lender_rows:
        c_int = int(cnt or 0)
        total_lenders_count += c_int
        if bool(is_act):
            active_lenders_count += c_int

    limit_clamped = max(1, min(int(recent_limit or 10), 50))
    recent_cases = (
        db.query(BrokerCaseModel)
        .options(
            joinedload(BrokerCaseModel.customer),
            selectinload(BrokerCaseModel.submissions).joinedload(BrokerSubmissionModel.lender),
        )
        .filter(BrokerCaseModel.tenant_id == tenant_id)
        .order_by(BrokerCaseModel.updated_at.desc(), BrokerCaseModel.id.desc())
        .limit(limit_clamped)
        .all()
    )

    return {
        "active_pipeline_cents": active_pipeline_cents,
        "funded_volume_cents": funded_volume_cents,
        "active_lenders_count": active_lenders_count,
        "total_lenders_count": total_lenders_count,
        "cases_by_status": cases_by_status,
        "counts": {
            "total_cases": total_cases_count,
            "active_cases": active_cases_count,
            "draft_cases": cases_by_status["DRAFT"],
            "submitted_cases": cases_by_status["SUBMITTED"],
            "offers_received_cases": cases_by_status["OFFERS_RECEIVED"],
            "funded_cases": cases_by_status["FUNDED"],
            "closed_lost_cases": cases_by_status["CLOSED_LOST"],
            "cancelled_cases": cases_by_status["CANCELLED"],
            "active_lenders": active_lenders_count,
            "total_lenders": total_lenders_count,
        },
        "commissions": commissions_summary,
        "recent_cases": [serialize_case_summary(c) for c in recent_cases],
    }


def get_broker_customer_summary(
    db: Session,
    tenant_id: int,
    customer_id: int,
) -> dict:
    """Broker cases, submissions/offers counts, funded total, and commissions for one customer (tenant-scoped)."""
    customer_exists = (
        db.query(CustomerModel.id)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == tenant_id)
        .first()
    )
    if not customer_exists:
        raise BrokerNotFoundError("Customer not found.", error_code="CUSTOMER_NOT_FOUND")

    cases = (
        db.query(BrokerCaseModel)
        .options(
            joinedload(BrokerCaseModel.customer),
            selectinload(BrokerCaseModel.submissions).joinedload(BrokerSubmissionModel.lender),
        )
        .filter(BrokerCaseModel.tenant_id == tenant_id, BrokerCaseModel.customer_id == customer_id)
        .order_by(BrokerCaseModel.id.desc())
        .all()
    )

    case_ids = [c.id for c in cases]
    commissions: list[BrokerCommissionModel] = []
    if case_ids:
        commissions = (
            db.query(BrokerCommissionModel)
            .options(joinedload(BrokerCommissionModel.lender), joinedload(BrokerCommissionModel.broker_case))
            .filter(
                BrokerCommissionModel.tenant_id == tenant_id,
                BrokerCommissionModel.broker_case_id.in_(case_ids),
            )
            .order_by(BrokerCommissionModel.id.desc())
            .all()
        )
    comm_by_case_id = {c.broker_case_id: c for c in commissions}

    status_order = ("DRAFT", "SUBMITTED", "OFFERS_RECEIVED", "FUNDED", "CLOSED_LOST", "CANCELLED")
    cases_by_status = {st: 0 for st in status_order}
    active_pipeline_cents = 0
    total_funded_cents = 0
    total_submissions = 0
    approved_offers = 0
    selected_offers = 0

    for c in cases:
        if c.status in cases_by_status:
            cases_by_status[c.status] += 1
        if c.status in ACTIVE_BROKER_CASE_STATUSES:
            active_pipeline_cents += int(c.requested_amount_cents or 0)
        elif c.status == "FUNDED":
            total_funded_cents += int(c.funded_amount_cents or 0)
        for sub in (c.submissions or []):
            total_submissions += 1
            if sub.status in ("APPROVED", "SELECTED"):
                approved_offers += 1
            if sub.status == "SELECTED":
                selected_offers += 1

    # Aggregate commissions for this customer's cases with exact integer arithmetic
    comm_buckets: dict[str, list[int]] = {}
    for comm in commissions:
        bucket = comm_buckets.setdefault(comm.status, [0, 0, 0])
        bucket[0] += 1
        bucket[1] += int(comm.expected_commission_cents or 0)
        bucket[2] += int(comm.paid_commission_cents or 0)
    comm_rows = [(st, vals[0], vals[1], vals[2]) for st, vals in comm_buckets.items()]
    commissions_summary = _build_commission_summary_from_rows(comm_rows)

    active_cases_count = sum(cases_by_status[st] for st in ACTIVE_BROKER_CASE_STATUSES)

    return {
        "customer_id": customer_id,
        "broker_summary": {
            "total_cases": len(cases),
            "active_cases": active_cases_count,
            "funded_cases": cases_by_status["FUNDED"],
            "closed_lost_cases": cases_by_status["CLOSED_LOST"],
            "cancelled_cases": cases_by_status["CANCELLED"],
            "cases_by_status": cases_by_status,
            "total_submissions": total_submissions,
            "approved_offers": approved_offers,
            "selected_offers": selected_offers,
            "active_pipeline_cents": active_pipeline_cents,
            "total_funded_cents": total_funded_cents,
            "commissions": commissions_summary,
        },
        "cases": [serialize_case_detail(c, commission=comm_by_case_id.get(c.id)) for c in cases],
        "commissions": [serialize_commission(comm) for comm in commissions],
    }
