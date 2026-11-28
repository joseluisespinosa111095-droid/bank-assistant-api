from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from core.security import get_client_ip
from dependencies.auth import require_module, require_roles, verify_csrf
from dependencies.db import get_db
from models import UserModel
from schemas.broker import (
    BrokerCaseCreateRequest,
    BrokerCaseFundRequest,
    BrokerCaseUpdateRequest,
    BrokerCommissionUpdateRequest,
    BrokerLenderCreateRequest,
    BrokerLenderUpdateRequest,
    BrokerSubmissionCreateRequest,
    BrokerSubmissionSelectRequest,
    BrokerSubmissionUpdateRequest,
)
from services.broker import (
    BrokerConflictError,
    BrokerNotFoundError,
    BrokerServiceError,
    BrokerValidationError,
    create_case,
    create_lender,
    create_submission,
    fund_case,
    get_broker_customer_summary,
    get_broker_dashboard_summary,
    get_case_detail,
    list_cases,
    list_commissions,
    list_lenders,
    select_submission_offer,
    update_case,
    update_commission,
    update_lender,
    update_submission,
)

router = APIRouter()
require_broker_module = require_module("BROKER")


def _raise_http_for_broker_error(exc: BrokerServiceError) -> None:
    if isinstance(exc, BrokerNotFoundError):
        raise HTTPException(status_code=404, detail=exc.message)
    if isinstance(exc, BrokerConflictError):
        raise HTTPException(status_code=409, detail=exc.message)
    if isinstance(exc, BrokerValidationError):
        raise HTTPException(status_code=422, detail=exc.message)
    raise HTTPException(status_code=400, detail=exc.message)


# --- DECOUPLED DASHBOARD & CUSTOMER SUMMARIES ---
@router.get("/api/broker/dashboard-summary", dependencies=[Depends(require_broker_module)])
def api_get_broker_dashboard_summary(
    recent_limit: int = Query(default=10, ge=1, le=50),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db),
):
    """Broker pipeline KPIs, funded volume, commissions, and recent cases (protected by BROKER module)."""
    try:
        return get_broker_dashboard_summary(db, current_user.tenant_id, recent_limit=recent_limit)
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.get("/api/broker/customers/{customer_id}/summary", dependencies=[Depends(require_broker_module)])
def api_get_broker_customer_summary(
    customer_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db),
):
    """Broker cases, submissions/offers, funded total, and commissions for a customer (protected by BROKER module)."""
    try:
        return get_broker_customer_summary(db, current_user.tenant_id, customer_id)
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


# --- LENDERS CATALOG ---
@router.get("/api/broker/lenders", dependencies=[Depends(require_broker_module)])
def api_list_broker_lenders(
    active_only: bool = Query(default=False),
    search: Optional[str] = Query(default=None, max_length=120),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db),
):
    return list_lenders(db, current_user.tenant_id, active_only=active_only, search=search)


@router.post("/api/broker/lenders", dependencies=[Depends(verify_csrf), Depends(require_broker_module)])
def api_create_broker_lender(
    payload: BrokerLenderCreateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db),
):
    try:
        return create_lender(
            db,
            current_user.tenant_id,
            current_user,
            code=payload.code,
            name=payload.name,
            lender_type=payload.lender_type,
            default_commission_bps=payload.default_commission_bps,
            contact_name=payload.contact_name,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
            notes=payload.notes,
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.patch("/api/broker/lenders/{lender_id}", dependencies=[Depends(verify_csrf), Depends(require_broker_module)])
def api_update_broker_lender(
    lender_id: int,
    payload: BrokerLenderUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db),
):
    try:
        return update_lender(
            db,
            current_user.tenant_id,
            lender_id,
            current_user,
            name=payload.name,
            lender_type=payload.lender_type,
            default_commission_bps=payload.default_commission_bps,
            contact_name=payload.contact_name,
            contact_email=payload.contact_email,
            contact_phone=payload.contact_phone,
            notes=payload.notes,
            is_active=payload.is_active,
            fields_set=set(payload.model_fields_set),
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


# --- BROKER CASES ---
@router.get("/api/broker/cases", dependencies=[Depends(require_broker_module)])
def api_list_broker_cases(
    status: Optional[str] = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db),
):
    try:
        return list_cases(
            db,
            current_user.tenant_id,
            status=status,
            customer_id=customer_id,
            search=search,
            page=page,
            page_size=page_size,
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.post("/api/broker/cases", dependencies=[Depends(verify_csrf), Depends(require_broker_module)])
def api_create_broker_case(
    payload: BrokerCaseCreateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db),
):
    try:
        return create_case(
            db,
            current_user.tenant_id,
            current_user,
            idempotency_key=payload.idempotency_key,
            customer_id=payload.customer_id,
            source_lead_id=payload.source_lead_id,
            product_type=payload.product_type,
            requested_amount_pesos=payload.requested_amount_pesos,
            requested_amount_cents=payload.requested_amount_cents,
            requested_term_months=payload.requested_term_months,
            purpose=payload.purpose,
            owner_user_id=payload.owner_user_id,
            notes=payload.notes,
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.get("/api/broker/cases/{case_id}", dependencies=[Depends(require_broker_module)])
def api_get_broker_case_detail(
    case_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db),
):
    try:
        return get_case_detail(db, current_user.tenant_id, case_id)
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.patch("/api/broker/cases/{case_id}", dependencies=[Depends(verify_csrf), Depends(require_broker_module)])
def api_update_broker_case(
    case_id: int,
    payload: BrokerCaseUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db),
):
    try:
        return update_case(
            db,
            current_user.tenant_id,
            case_id,
            current_user,
            product_type=payload.product_type,
            requested_amount_pesos=payload.requested_amount_pesos,
            requested_amount_cents=payload.requested_amount_cents,
            requested_term_months=payload.requested_term_months,
            purpose=payload.purpose,
            owner_user_id=payload.owner_user_id,
            status=payload.status,
            closed_reason=payload.closed_reason,
            notes=payload.notes,
            fields_set=set(payload.model_fields_set),
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


# --- MULTI-LENDER SUBMISSIONS & OFFER SELECTION ---
@router.post(
    "/api/broker/cases/{case_id}/submissions",
    dependencies=[Depends(verify_csrf), Depends(require_broker_module)],
)
def api_create_broker_submission(
    case_id: int,
    payload: BrokerSubmissionCreateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db),
):
    try:
        return create_submission(
            db,
            current_user.tenant_id,
            case_id,
            current_user,
            lender_id=payload.lender_id,
            status=payload.status,
            external_reference=payload.external_reference,
            offered_amount_pesos=payload.offered_amount_pesos,
            offered_amount_cents=payload.offered_amount_cents,
            offered_rate_bps=payload.offered_rate_bps,
            offered_term_months=payload.offered_term_months,
            offered_monthly_payment_pesos=payload.offered_monthly_payment_pesos,
            offered_monthly_payment_cents=payload.offered_monthly_payment_cents,
            offered_commission_bps=payload.offered_commission_bps,
            decision_notes=payload.decision_notes,
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.patch(
    "/api/broker/cases/{case_id}/submissions/{submission_id}",
    dependencies=[Depends(verify_csrf), Depends(require_broker_module)],
)
def api_update_broker_submission(
    case_id: int,
    submission_id: int,
    payload: BrokerSubmissionUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db),
):
    try:
        return update_submission(
            db,
            current_user.tenant_id,
            case_id,
            submission_id,
            current_user,
            status=payload.status,
            external_reference=payload.external_reference,
            offered_amount_pesos=payload.offered_amount_pesos,
            offered_amount_cents=payload.offered_amount_cents,
            offered_rate_bps=payload.offered_rate_bps,
            offered_term_months=payload.offered_term_months,
            offered_monthly_payment_pesos=payload.offered_monthly_payment_pesos,
            offered_monthly_payment_cents=payload.offered_monthly_payment_cents,
            offered_commission_bps=payload.offered_commission_bps,
            decision_notes=payload.decision_notes,
            fields_set=set(payload.model_fields_set),
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.post(
    "/api/broker/cases/{case_id}/submissions/{submission_id}/select",
    dependencies=[Depends(verify_csrf), Depends(require_broker_module)],
)
def api_select_broker_submission(
    case_id: int,
    submission_id: int,
    payload: BrokerSubmissionSelectRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db),
):
    try:
        return select_submission_offer(
            db,
            current_user.tenant_id,
            case_id,
            submission_id,
            current_user,
            decision_notes=payload.decision_notes,
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


# --- FUNDING & COMMISSIONS ---
@router.post(
    "/api/broker/cases/{case_id}/fund",
    dependencies=[Depends(verify_csrf), Depends(require_broker_module)],
)
def api_fund_broker_case(
    case_id: int,
    payload: BrokerCaseFundRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db),
):
    try:
        return fund_case(
            db,
            current_user.tenant_id,
            case_id,
            current_user,
            funded_amount_pesos=payload.funded_amount_pesos,
            funded_amount_cents=payload.funded_amount_cents,
            external_loan_reference=payload.external_loan_reference,
            funded_at=payload.funded_at,
            commission_rate_bps=payload.commission_rate_bps,
            expected_pay_date=payload.expected_pay_date,
            notes=payload.notes,
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.get("/api/broker/commissions", dependencies=[Depends(require_broker_module)])
def api_list_broker_commissions(
    status: Optional[str] = Query(default=None),
    lender_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db),
):
    try:
        return list_commissions(
            db,
            current_user.tenant_id,
            status=status,
            lender_id=lender_id,
            page=page,
            page_size=page_size,
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)


@router.patch(
    "/api/broker/commissions/{commission_id}",
    dependencies=[Depends(verify_csrf), Depends(require_broker_module)],
)
def api_update_broker_commission(
    commission_id: int,
    payload: BrokerCommissionUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db),
):
    try:
        return update_commission(
            db,
            current_user.tenant_id,
            commission_id,
            current_user,
            status=payload.status,
            paid_commission_pesos=payload.paid_commission_pesos,
            paid_commission_cents=payload.paid_commission_cents,
            expected_pay_date=payload.expected_pay_date,
            payment_reference=payload.payment_reference,
            paid_at=payload.paid_at,
            notes=payload.notes,
            fields_set=set(payload.model_fields_set),
            ip_address=get_client_ip(request),
        )
    except BrokerServiceError as exc:
        _raise_http_for_broker_error(exc)
