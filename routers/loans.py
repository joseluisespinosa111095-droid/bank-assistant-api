import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from core.config import (
    SUPPORTED_PRODUCTS,
    VALID_CALL_OUTCOMES,
    VALID_DISBURSEMENT_METHODS,
    VALID_INTERACTION_TYPES,
    VALID_PAYMENT_METHODS,
    logger,
)
from core.security import get_client_ip
from core.utils import (
    generate_next_loan_number,
    get_crm_timezone,
    get_local_day_bounds_utc,
    parse_optional_datetime_utc,
    parse_pesos_to_cents,
    parse_rebate_detail,
    serialize_rebate_detail,
)
from dependencies.auth import require_roles, verify_csrf
from dependencies.db import get_db
from models import (
    CustomerModel,
    InteractionModel,
    LeadModel,
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentModel,
    UserModel,
)
from schemas.lending import (
    LoanActivateRequest,
    LoanApproveRequest,
    LoanCancelRequest,
    LoanCollectionLogRequest,
    LoanCreateRequest,
    LoanDefaultRequest,
    LoanPaymentCreateRequest,
    LoanPaymentReverseRequest,
)
from services.audit import record_audit_event
from services.financial_engine import LoanFinancialEngine, LoanPaymentEngine
from services.serializers_core import serialize_interaction
from services.serializers_lending import (
    serialize_loan_detail,
    serialize_loan_payment,
    serialize_loan_summary,
)

router = APIRouter()


# --- STAGE 3 & BLOCK J: LOANS, AMORTIZATION SCHEDULES & DELINQUENCY METRICS ---
@router.get("/api/loans")
def list_loans(
    search: Optional[str] = Query(default=None, max_length=120),
    status: Optional[str] = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    _, _, _, today_local = get_local_day_bounds_utc()
    query = db.query(LoanModel).filter(LoanModel.tenant_id == current_user.tenant_id)

    if status and status != "ALL":
        query = query.filter(LoanModel.status == status)

    if customer_id:
        query = query.filter(LoanModel.customer_id == customer_id)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            LoanModel.loan_number.ilike(term),
            LoanModel.product_name.ilike(term)
        ))

    total = query.count()
    offset = (page - 1) * page_size
    items = (
        query.options(joinedload(LoanModel.customer), selectinload(LoanModel.installments))
        .order_by(LoanModel.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [serialize_loan_summary(l, today_local) for l in items]
    }


@router.post("/api/loans", dependencies=[Depends(verify_csrf)])
def create_loan(
    payload: LoanCreateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if payload.product_name not in SUPPORTED_PRODUCTS:
        raise HTTPException(status_code=422, detail=f"Supported products: {', '.join(SUPPORTED_PRODUCTS)}")

    if payload.term_installments_count < 1 or payload.term_installments_count > 104:
        raise HTTPException(status_code=422, detail="Loan term must be between 1 and 104 weekly installments.")

    if payload.assumed_interest_months < 1 or payload.assumed_interest_months > 36:
        raise HTTPException(status_code=422, detail="Assumed interest months must be between 1 and 36.")

    customer = (
        db.query(CustomerModel)
        .filter(CustomerModel.id == payload.customer_id, CustomerModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if customer.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Cannot originate a loan for a non-active customer.")

    if payload.source_lead_id is not None:
        lead = (
            db.query(LeadModel)
            .filter(LeadModel.id == payload.source_lead_id, LeadModel.tenant_id == current_user.tenant_id)
            .first()
        )
        if not lead:
            raise HTTPException(status_code=404, detail="Source lead not found.")
        if lead.stage != "CONVERTED" or lead.converted_customer_id != customer.id:
            raise HTTPException(
                status_code=400,
                detail="Source lead must be in CONVERTED stage and linked to the selected customer."
            )

    if payload.principal_pesos is not None and payload.principal_cents is not None:
        pesos_in_cents = parse_pesos_to_cents(payload.principal_pesos, None)
        if pesos_in_cents != int(payload.principal_cents):
            raise HTTPException(
                status_code=422,
                detail="Conflicting principal_pesos and principal_cents values provided."
            )

    principal_cents = parse_pesos_to_cents(payload.principal_pesos, payload.principal_cents)
    if principal_cents <= 0:
        raise HTTPException(status_code=422, detail="Loan principal amount must be greater than zero.")
    if principal_cents > 1_000_000_000:
        raise HTTPException(
            status_code=422,
            detail="Loan principal amount cannot exceed $10,000,000.00 MXN."
        )

    try:
        start_dt = date.fromisoformat(payload.start_date)
        first_pay_dt = date.fromisoformat(payload.first_payment_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    _, _, _, today_local = get_local_day_bounds_utc()
    if start_dt < today_local:
        raise HTTPException(
            status_code=422,
            detail=f"Contract start date ({start_dt.isoformat()}) cannot be earlier than current local date ({today_local.isoformat()})."
        )

    if first_pay_dt < start_dt:
        raise HTTPException(status_code=422, detail="First payment date cannot be earlier than start date.")

    calc_method = (payload.interest_calculation_method or "FLAT_ORIGINAL_PRINCIPAL").strip().upper()
    if calc_method not in ("FLAT_ORIGINAL_PRINCIPAL", "DECLINING_BALANCE"):
        raise HTTPException(
            status_code=422,
            detail="Invalid interest_calculation_method. Allowed: FLAT_ORIGINAL_PRINCIPAL, DECLINING_BALANCE."
        )
    interest_basis_val = "DECLINING_PRINCIPAL" if calc_method == "DECLINING_BALANCE" else "ORIGINAL_PRINCIPAL"
    rounding_policy_val = "FRENCH_LEVEL_EXACT_FINAL" if calc_method == "DECLINING_BALANCE" else "FLOOR_REGULAR_EXACT_FINAL"

    # Calculate deterministic schedule on server
    calc = LoanFinancialEngine.calculate_flat_schedule(
        principal_pesos=principal_cents / 100.0,
        monthly_rate_percent=payload.monthly_interest_rate_percent,
        assumed_interest_months=payload.assumed_interest_months,
        term_weeks=payload.term_installments_count,
        first_payment_date_iso=payload.first_payment_date,
        interest_calculation_method=calc_method
    )

    canonical_payload = {
        "customer_id": payload.customer_id,
        "source_lead_id": payload.source_lead_id,
        "product_name": payload.product_name,
        "interest_calculation_method": calc_method,
        "principal_cents": calc["principal_cents"],
        "monthly_interest_rate_bps": calc["monthly_interest_rate_bps"],
        "assumed_interest_months": calc["months_count"],
        "term_installments_count": calc["installments_count"],
        "start_date": payload.start_date,
        "first_payment_date": payload.first_payment_date
    }
    payload_json = json.dumps(canonical_payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    existing = (
        db.query(LoanModel)
        .filter(
            LoanModel.tenant_id == current_user.tenant_id,
            LoanModel.idempotency_key == payload.idempotency_key
        )
        .first()
    )
    if existing:
        if existing.request_payload_hash == payload_hash:
            _, _, _, today_local = get_local_day_bounds_utc()
            return {"idempotent_replay": True, "loan": serialize_loan_detail(existing, today_local)}
        else:
            raise HTTPException(status_code=409, detail="Idempotency key was already used with different loan parameters.")

    try:
        loan_number = generate_next_loan_number(db, current_user.tenant_id)

        contract_rules = {
            "engine_version": "1.1.0",
            "interest_calculation_method": calc_method,
            "interest_basis": interest_basis_val,
            "interest_accrual_convention": "ON_INSTALLMENT_DUE_DATE",
            "assumed_interest_months": calc["months_count"],
            "term_installments_count": calc["installments_count"],
            "payment_frequency": "WEEKLY",
            "rounding_policy": rounding_policy_val,
            "partial_period_prorating_rule": "NOT_DEFINED_EXPLICIT_MONTHS_USED",
            "early_payoff_rebate_rule": "NOT_DEFINED_PENDING_POLICY",
            "late_fee_moratory_rule": "NOT_DEFINED_PENDING_POLICY"
        }

        new_loan = LoanModel(
            tenant_id=current_user.tenant_id,
            loan_number=loan_number,
            idempotency_key=payload.idempotency_key,
            request_payload_hash=payload_hash,
            customer_id=payload.customer_id,
            source_lead_id=payload.source_lead_id,
            status="DRAFT",
            product_name=payload.product_name,
            product_version="v1.1",
            currency="MXN",
            principal_cents=calc["principal_cents"],
            monthly_interest_rate_bps=calc["monthly_interest_rate_bps"],
            interest_calculation_method=calc_method,
            interest_basis=interest_basis_val,
            interest_accrual_convention="ON_INSTALLMENT_DUE_DATE",
            assumed_interest_months=calc["months_count"],
            payment_frequency="WEEKLY",
            term_installments_count=calc["installments_count"],
            rounding_policy=rounding_policy_val,
            contract_rules_json=json.dumps(contract_rules),
            monthly_interest_amount_cents=calc["monthly_interest_amount_cents"],
            total_interest_cents=calc["total_interest_cents"],
            total_repayment_cents=calc["total_repayment_cents"],
            regular_installment_cents=calc["regular_installment_cents"],
            final_installment_cents=calc["final_installment_cents"],
            start_date=start_dt,
            first_payment_date=calc["first_payment_date"],
            maturity_date=calc["maturity_date"],
            unpaid_principal_cents=0,
            remaining_contractual_interest_cents=0,
            remaining_contract_total_cents=0,
            amount_paid_cents=0,
            owner_user_id=customer.owner_user_id,
            created_by_user_id=current_user.id,
            notes=payload.notes.strip() if payload.notes else None
        )
        db.add(new_loan)
        db.flush()

        for inst in calc["schedule"]:
            db.add(LoanInstallmentModel(
                tenant_id=current_user.tenant_id,
                loan_id=new_loan.id,
                installment_number=inst["installment_number"],
                due_date=inst["due_date"],
                beginning_principal_cents=inst["beginning_principal_cents"],
                principal_due_cents=inst["principal_due_cents"],
                interest_due_cents=inst["interest_due_cents"],
                scheduled_payment_cents=inst["scheduled_payment_cents"],
                ending_principal_cents=inst["ending_principal_cents"],
                principal_paid_cents=0,
                interest_paid_cents=0,
                amount_paid_cents=0,
                remaining_amount_cents=inst["scheduled_payment_cents"]
            ))

        record_audit_event(
            db, "LOAN_DRAFT_CREATED", "loan", "SUCCESS",
            actor=current_user, resource_id=new_loan.loan_number, ip_address=ip,
            details={
                "loan_number": new_loan.loan_number,
                "customer_id": new_loan.customer_id,
                "principal_cents": new_loan.principal_cents
            },
            commit=False
        )
        db.commit()
        db.refresh(new_loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return {"idempotent_replay": False, "loan": serialize_loan_detail(new_loan, today_local)}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing_race = (
            db.query(LoanModel)
            .filter(
                LoanModel.tenant_id == current_user.tenant_id,
                LoanModel.idempotency_key == payload.idempotency_key
            )
            .first()
        )
        if existing_race:
            if existing_race.request_payload_hash == payload_hash:
                _, _, _, today_local = get_local_day_bounds_utc()
                return {"idempotent_replay": True, "loan": serialize_loan_detail(existing_race, today_local)}
            raise HTTPException(status_code=409, detail="Idempotency key conflict under concurrency.")
        logger.exception("Integrity error creating loan draft")
        raise HTTPException(status_code=500, detail="Could not create loan draft.")
    except Exception as e:
        db.rollback()
        logger.exception("Failed to create loan draft")
        raise HTTPException(status_code=500, detail=f"Could not create loan draft: {type(e).__name__}")


@router.get("/api/loans/{loan_id}")
def get_loan_detail(
    loan_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    loan = (
        db.query(LoanModel)
        .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
    _, _, _, today_local = get_local_day_bounds_utc()
    return serialize_loan_detail(loan, today_local, db=db)


@router.post("/api/loans/{loan_id}/approve", dependencies=[Depends(verify_csrf)])
def approve_loan(
    loan_id: int,
    payload: LoanApproveRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        loan = (
            db.query(LoanModel)
            .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status != "DRAFT":
            raise HTTPException(status_code=409, detail=f"Only DRAFT loans can be approved. Current status: {loan.status}")

        now_utc = datetime.now(timezone.utc)
        loan.status = "APPROVED"
        loan.approved_by_user_id = current_user.id
        loan.approved_at = now_utc
        if payload.approval_notes:
            loan.approval_notes = payload.approval_notes.strip()
        loan.updated_at = now_utc

        record_audit_event(
            db, "LOAN_APPROVED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={"loan_number": loan.loan_number}, commit=False
        )
        db.commit()
        db.refresh(loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return serialize_loan_detail(loan, today_local)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to approve loan")
        raise HTTPException(status_code=500, detail=f"Could not approve loan: {type(e).__name__}")


@router.post("/api/loans/{loan_id}/activate", dependencies=[Depends(verify_csrf)])
def activate_loan(
    loan_id: int,
    payload: LoanActivateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if not payload.confirm_disbursement:
        raise HTTPException(status_code=422, detail="Disbursement confirmation is required to activate a loan.")
    if payload.disbursement_method not in VALID_DISBURSEMENT_METHODS:
        raise HTTPException(status_code=422, detail=f"Invalid disbursement method. Allowed: {', '.join(sorted(VALID_DISBURSEMENT_METHODS))}")

    disbursed_at_dt = parse_optional_datetime_utc(payload.disbursed_at) or datetime.now(timezone.utc)
    if disbursed_at_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Disbursement time cannot be in the future.")

    try:
        loan = (
            db.query(LoanModel)
            .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")

        if loan.status == "ACTIVE":
            raise HTTPException(status_code=409, detail=f"Loan {loan.loan_number} is already active.")
        if loan.status == "CANCELLED":
            raise HTTPException(status_code=409, detail=f"Cannot activate loan {loan.loan_number} because it was cancelled.")
        if loan.status != "APPROVED":
            raise HTTPException(status_code=409, detail=f"Loan must be in APPROVED status before activation. Current: {loan.status}")

        now_utc = datetime.now(timezone.utc)

        # Asignación limpia y ordenada exigida por el trigger de Postgres
        loan.unpaid_principal_cents = int(loan.principal_cents)
        loan.remaining_contractual_interest_cents = int(loan.total_interest_cents)
        loan.remaining_contract_total_cents = int(loan.total_repayment_cents)
        loan.amount_paid_cents = 0

        loan.activated_by_user_id = current_user.id
        loan.activated_at = now_utc
        loan.disbursed_at = disbursed_at_dt
        loan.disbursement_method = payload.disbursement_method
        loan.disbursement_reference = payload.disbursement_reference.strip()
        if payload.disbursement_notes:
            loan.disbursement_notes = payload.disbursement_notes.strip()

        loan.status = "ACTIVE"
        loan.updated_at = now_utc

        db.flush() # Fuerza la validación del trigger antes del commit final

        record_audit_event(
            db, "LOAN_ACTIVATED_DISBURSED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "method": loan.disbursement_method,
                "reference": loan.disbursement_reference
            },
            commit=False
        )
        db.commit()
        db.refresh(loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return serialize_loan_detail(loan, today_local)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to activate loan")
        raise HTTPException(status_code=500, detail="Could not activate loan.")


@router.post("/api/loans/{loan_id}/cancel", dependencies=[Depends(verify_csrf)])
def cancel_loan(
    loan_id: int,
    payload: LoanCancelRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    reason = payload.cancellation_reason.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Cancellation reason must be at least 3 characters.")

    try:
        loan = (
            db.query(LoanModel)
            .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status == "ACTIVE":
            raise HTTPException(status_code=409, detail="Active disbursed loans cannot be cancelled.")
        if loan.status == "CANCELLED":
            raise HTTPException(status_code=409, detail="Loan is already cancelled.")
        if loan.status not in ("DRAFT", "APPROVED"):
            raise HTTPException(status_code=409, detail=f"Loans in status {loan.status} cannot be cancelled.")

        now_utc = datetime.now(timezone.utc)
        loan.status = "CANCELLED"
        loan.cancelled_by_user_id = current_user.id
        loan.cancelled_at = now_utc
        loan.cancellation_reason = reason
        loan.unpaid_principal_cents = 0
        loan.remaining_contractual_interest_cents = 0
        loan.remaining_contract_total_cents = 0
        loan.amount_paid_cents = 0
        loan.updated_at = now_utc

        record_audit_event(
            db, "LOAN_CANCELLED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={"loan_number": loan.loan_number, "reason": reason},
            commit=False
        )
        db.commit()
        db.refresh(loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return serialize_loan_detail(loan, today_local, db=db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to cancel loan")
        raise HTTPException(status_code=500, detail=f"Could not cancel loan: {type(e).__name__}")


@router.post("/api/loans/{loan_id}/default", dependencies=[Depends(verify_csrf)])
def mark_loan_defaulted(
    loan_id: int,
    payload: LoanDefaultRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if not payload.confirm_default:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required to mark a loan as DEFAULTED.")

    reason = payload.default_reason.strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="Default reason must be at least 5 characters.")

    try:
        loan = (
            db.query(LoanModel)
            .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status == "DEFAULTED":
            raise HTTPException(status_code=409, detail=f"Loan {loan.loan_number} is already marked as DEFAULTED.")
        if loan.status != "ACTIVE":
            raise HTTPException(
                status_code=409,
                detail=f"Only ACTIVE loans can be transitioned to DEFAULTED. Current status: {loan.status}"
            )
        if loan.remaining_contract_total_cents <= 0:
            raise HTTPException(status_code=409, detail="Cannot default a loan with zero remaining balance.")

        now_utc = datetime.now(timezone.utc)
        _, _, _, today_local = get_local_day_bounds_utc()
        summary_snapshot = serialize_loan_summary(loan, today_local)

        # Keep immutable origination loan.notes untouched
        loan.status = "DEFAULTED"
        loan.updated_at = now_utc

        db.add(InteractionModel(
            tenant_id=current_user.tenant_id,
            lead_id=loan.source_lead_id,
            customer_id=loan.customer_id,
            interaction_type="NOTE",
            direction="INTERNAL",
            visibility="INTERNAL",
            outcome="FOLLOW_UP_REQUIRED",
            content=(
                f"⚠️ LOAN MARKED AS DEFAULTED ({loan.loan_number}) — "
                f"Unpaid Principal: ${loan.unpaid_principal_cents / 100:,.2f} MXN | "
                f"Remaining Total: ${loan.remaining_contract_total_cents / 100:,.2f} MXN | "
                f"Days Past Due: {summary_snapshot['days_past_due']}d | "
                f"Reason: {reason}"
            ),
            created_by_user_id=current_user.id
        ))

        record_audit_event(
            db, "LOAN_MARKED_DEFAULTED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "customer_id": loan.customer_id,
                "unpaid_principal_cents": loan.unpaid_principal_cents,
                "remaining_total_cents": loan.remaining_contract_total_cents,
                "overdue_amount_cents": summary_snapshot["overdue_amount_cents"],
                "days_past_due": summary_snapshot["days_past_due"]
            },
            commit=False
        )
        db.commit()
        db.refresh(loan)
        return serialize_loan_detail(loan, today_local, db=db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to mark loan as defaulted")
        raise HTTPException(status_code=500, detail=f"Could not mark loan as defaulted: {type(e).__name__}")


@router.post("/api/loans/{loan_id}/collections-log", dependencies=[Depends(verify_csrf)])
def create_loan_collection_log(
    loan_id: int,
    payload: LoanCollectionLogRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    loan = (
        db.query(LoanModel)
        .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
    if loan.status not in ("ACTIVE", "DEFAULTED"):
        raise HTTPException(status_code=409, detail="Collection logs can only be recorded on ACTIVE or DEFAULTED loans.")

    itype = payload.interaction_type.strip().upper()
    outcome = payload.outcome.strip().upper()
    if itype not in VALID_INTERACTION_TYPES:
        raise HTTPException(status_code=422, detail="Invalid interaction_type.")
    if outcome not in VALID_CALL_OUTCOMES:
        raise HTTPException(status_code=422, detail="Invalid call outcome.")

    follow_up_utc = parse_optional_datetime_utc(payload.next_follow_up_at)
    promised_cents = parse_pesos_to_cents(payload.promised_amount_pesos, None) if payload.promised_amount_pesos else 0

    header_parts = [f"[COLLECTION {loan.loan_number}]"]
    if promised_cents > 0:
        header_parts.append(f"Promised: ${promised_cents / 100:,.2f} MXN")
    if follow_up_utc:
        header_parts.append(f"Follow-up: {follow_up_utc.astimezone(get_crm_timezone()).strftime('%Y-%m-%d %H:%M')}")
    formatted_content = f"{' • '.join(header_parts)} — {payload.notes.strip()}"

    try:
        interaction = InteractionModel(
            tenant_id=current_user.tenant_id,
            lead_id=loan.source_lead_id,
            customer_id=loan.customer_id,
            interaction_type=itype,
            direction="OUTBOUND",
            visibility="INTERNAL",
            outcome=outcome,
            content=formatted_content,
            next_follow_up_at=follow_up_utc,
            created_by_user_id=current_user.id
        )
        db.add(interaction)
        db.flush()

        record_audit_event(
            db, "LOAN_COLLECTION_LOGGED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "customer_id": loan.customer_id,
                "interaction_type": itype,
                "outcome": outcome,
                "promised_cents": promised_cents
            },
            commit=False
        )
        db.commit()
        db.refresh(interaction)
        return serialize_interaction(interaction, {current_user.id: current_user})
    except Exception as e:
        db.rollback()
        logger.exception("Failed to record collection log")
        raise HTTPException(status_code=500, detail=f"Could not save collection log: {type(e).__name__}")


@router.get("/api/collections/queue")
def get_collections_queue(
    bucket: str = Query(default="ALL"),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    _, _, _, today_local = get_local_day_bounds_utc()
    candidate_loans = (
        db.query(LoanModel)
        .options(joinedload(LoanModel.customer), selectinload(LoanModel.installments))
        .filter(
            LoanModel.tenant_id == current_user.tenant_id,
            LoanModel.status.in_(["ACTIVE", "DEFAULTED"])
        )
        .order_by(LoanModel.id.desc())
        .all()
    )

    cust_ids = {l.customer_id for l in candidate_loans}
    latest_interactions = {}
    if cust_ids:
        int_rows = (
            db.query(InteractionModel)
            .filter(
                InteractionModel.tenant_id == current_user.tenant_id,
                InteractionModel.customer_id.in_(cust_ids)
            )
            .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
            .all()
        )
        for ir in int_rows:
            if ir.customer_id not in latest_interactions:
                latest_interactions[ir.customer_id] = ir

    queue_items = []
    overdue_active_count = 0
    due_today_active_count = 0
    defaulted_count = 0
    total_overdue_cents = 0
    total_defaulted_cents = 0

    bucket_norm = (bucket or "ALL").strip().upper()

    for l in candidate_loans:
        s = serialize_loan_summary(l, today_local)
        is_defaulted = (l.status == "DEFAULTED")
        is_overdue = (l.status == "ACTIVE" and s["overdue_amount_cents"] > 0)
        is_due_today = (l.status == "ACTIVE" and s["overdue_amount_cents"] == 0 and s["due_today_cents"] > 0)

        if not (is_defaulted or is_overdue or is_due_today):
            continue

        if is_defaulted:
            defaulted_count += 1
            total_defaulted_cents += l.remaining_contract_total_cents
        elif is_overdue:
            overdue_active_count += 1
            total_overdue_cents += s["overdue_amount_cents"]
        elif is_due_today:
            due_today_active_count += 1

        if bucket_norm == "OVERDUE" and not is_overdue:
            continue
        if bucket_norm == "DEFAULTED" and not is_defaulted:
            continue
        if bucket_norm == "DUE_TODAY" and not is_due_today:
            continue

        last_int = latest_interactions.get(l.customer_id)
        s["last_collection_outcome"] = last_int.outcome if last_int else None
        s["last_collection_note"] = last_int.content[:180] if last_int else None
        s["last_contacted_at"] = last_int.created_at.isoformat() if last_int and last_int.created_at else None
        s["next_follow_up_at"] = last_int.next_follow_up_at.isoformat() if last_int and last_int.next_follow_up_at else None
        queue_items.append(s)

    queue_items.sort(
        key=lambda x: (
            1 if x["status"] == "DEFAULTED" else 0,
            x["days_past_due"],
            x["overdue_amount_cents"],
            x["current_payable_amount_cents"]
        ),
        reverse=True
    )

    return {
        "counts": {
            "overdue_active": overdue_active_count,
            "due_today_active": due_today_active_count,
            "defaulted": defaulted_count,
            "total_actionable": overdue_active_count + due_today_active_count + defaulted_count
        },
        "totals": {
            "overdue_active_cents": total_overdue_cents,
            "defaulted_remaining_cents": total_defaulted_cents
        },
        "items": queue_items
    }


@router.get("/api/loans/{loan_id}/payments")
def list_loan_payments(
    loan_id: int,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    loan = (
        db.query(LoanModel)
        .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")

    payments = (
        db.query(LoanPaymentModel)
        .filter(
            LoanPaymentModel.tenant_id == current_user.tenant_id,
            LoanPaymentModel.loan_id == loan_id
        )
        .order_by(LoanPaymentModel.loan_payment_seq.desc())
        .all()
    )
    top_posted_id = next((p.id for p in payments if p.status == 'POSTED'), None)

    return [serialize_loan_payment(p, include_allocations=True, is_top_reversible=(p.id == top_posted_id)) for p in payments]


@router.post("/api/loans/{loan_id}/payments", dependencies=[Depends(verify_csrf)])
def create_loan_payment(
    loan_id: int,
    payload: LoanPaymentCreateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    amount_cents = parse_pesos_to_cents(payload.amount_pesos, payload.amount_cents)
    if amount_cents <= 0:
        raise HTTPException(status_code=422, detail="Payment amount must be greater than zero.")
    if payload.payment_method not in VALID_PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="Invalid payment method.")

    explicit_paid_at_dt = parse_optional_datetime_utc(payload.paid_at)
    if explicit_paid_at_dt and explicit_paid_at_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Payment effective date cannot be in the future.")

    payment_ref_clean = (payload.payment_reference or "").strip() or None
    notes_clean = (payload.notes or "").strip() or None
    apply_rebate = bool(getattr(payload, "apply_early_payoff_rebate", False))

    # Hash determinista: si el cliente omite paid_at se hashea None, nunca datetime.now()
    canonical = {
        "loan_id": loan_id,
        "amount_cents": amount_cents,
        "payment_method": payload.payment_method,
        "payment_reference": payment_ref_clean.upper() if payment_ref_clean else None,
        "paid_at": explicit_paid_at_dt.isoformat() if explicit_paid_at_dt else None,
        "apply_early_payoff_rebate": apply_rebate,
        "notes": notes_clean
    }
    payload_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()

    def _build_replay_response(existing_pay: LoanPaymentModel, target_loan: Optional[LoanModel] = None) -> dict:
        _, _, _, today_replay = get_local_day_bounds_utc()
        loan_obj = target_loan or (
            db.query(LoanModel)
            .filter(LoanModel.id == existing_pay.loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .first()
        )
        latest_posted = (
            db.query(LoanPaymentModel.id)
            .filter(
                LoanPaymentModel.tenant_id == current_user.tenant_id,
                LoanPaymentModel.loan_id == existing_pay.loan_id,
                LoanPaymentModel.status == "POSTED"
            )
            .order_by(LoanPaymentModel.loan_payment_seq.desc())
            .first()
        )
        is_top = bool(latest_posted and latest_posted[0] == existing_pay.id)
        return {
            "idempotent_replay": True,
            "payment": serialize_loan_payment(existing_pay, include_allocations=True, is_top_reversible=is_top),
            "loan": serialize_loan_detail(loan_obj, today_replay, db=db) if loan_obj else None
        }

    existing = (
        db.query(LoanPaymentModel)
        .filter(
            LoanPaymentModel.tenant_id == current_user.tenant_id,
            LoanPaymentModel.idempotency_key == payload.idempotency_key
        )
        .first()
    )
    if existing:
        if existing.request_payload_hash == payload_hash:
            return _build_replay_response(existing)
        raise HTTPException(status_code=409, detail="Idempotency key collision with different payload.")

    try:
        loan = (
            db.query(LoanModel)
            .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")

        # Re-verificar idempotencia bajo el lock del préstamo ANTES de validar estatus, saldo o referencia duplicada
        existing_locked = (
            db.query(LoanPaymentModel)
            .filter(
                LoanPaymentModel.tenant_id == current_user.tenant_id,
                LoanPaymentModel.idempotency_key == payload.idempotency_key
            )
            .first()
        )
        if existing_locked:
            if existing_locked.request_payload_hash == payload_hash:
                replay_resp = _build_replay_response(existing_locked, target_loan=loan)
                db.rollback()
                return replay_resp
            db.rollback()
            raise HTTPException(status_code=409, detail="Concurrency collision on idempotency key.")

        if loan.status != "ACTIVE":
            raise HTTPException(status_code=409, detail=f"Payments can only be applied to ACTIVE loans. Current: {loan.status}")
        if amount_cents > loan.remaining_contract_total_cents:
            raise HTTPException(status_code=422, detail=f"Amount exceeds remaining balance. Max allowed: ${loan.remaining_contract_total_cents / 100:,.2f} MXN")

        if payload.payment_method in ("SPEI_TRANSFER", "BANK_DEPOSIT", "CHECK") and payment_ref_clean:
            ref_upper = payment_ref_clean.upper()
            dup_ref = db.query(LoanPaymentModel).filter(
                LoanPaymentModel.tenant_id == current_user.tenant_id,
                LoanPaymentModel.payment_method == payload.payment_method,
                func.upper(func.trim(LoanPaymentModel.payment_reference)) == ref_upper,
                LoanPaymentModel.status == "POSTED"
            ).first()
            if dup_ref:
                raise HTTPException(status_code=409, detail=f"Duplicate payment reference detected: {ref_upper}")

        paid_at_dt = explicit_paid_at_dt or datetime.now(timezone.utc)

        installments = db.query(LoanInstallmentModel).filter(
            LoanInstallmentModel.tenant_id == current_user.tenant_id,
            LoanInstallmentModel.loan_id == loan_id,
            LoanInstallmentModel.remaining_amount_cents > 0
        ).order_by(LoanInstallmentModel.installment_number.asc()).with_for_update().all()

        _, _, _, today_local = get_local_day_bounds_utc()
        rebated_installments_map = {}
        total_rebated_interest_cents = 0
        effective_notes = notes_clean

        if apply_rebate:
            if current_user.role not in ("OWNER_ADMIN", "CREDIT_ADVISOR"):
                raise HTTPException(
                    status_code=403,
                    detail="Only OWNER_ADMIN or CREDIT_ADVISOR can authorize an early payoff interest rebate."
                )

            unpaid_principal = 0
            unpaid_accrued_interest = 0
            unearned_future_interest = 0
            for inst in installments:
                rem_p = max(0, int(inst.principal_due_cents or 0) - int(inst.principal_paid_cents or 0))
                rem_i = max(0, int(inst.interest_due_cents or 0) - int(inst.interest_paid_cents or 0))
                unpaid_principal += rem_p
                if inst.due_date and inst.due_date <= today_local:
                    unpaid_accrued_interest += rem_i
                else:
                    unearned_future_interest += rem_i

            expected_early_cents = unpaid_principal + unpaid_accrued_interest
            if unearned_future_interest <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="This loan has no unearned future interest eligible for early payoff rebate."
                )
            if amount_cents != expected_early_cents:
                raise HTTPException(
                    status_code=422,
                    detail=f"Early payoff with interest rebate requires exact settlement amount of ${expected_early_cents / 100:,.2f} MXN."
                )

            for inst in installments:
                if inst.due_date and inst.due_date > today_local:
                    rem_i = max(0, int(inst.interest_due_cents or 0) - int(inst.interest_paid_cents or 0))
                    if rem_i > 0:
                        rebated_installments_map[str(inst.id)] = rem_i
                        total_rebated_interest_cents += rem_i
                        inst.interest_due_cents = int(inst.interest_paid_cents or 0)
                        inst.scheduled_payment_cents = int(inst.principal_due_cents or 0) + inst.interest_due_cents
                        if hasattr(inst, "remaining_interest_cents"):
                            inst.remaining_interest_cents = 0
                        inst.remaining_amount_cents = max(0, inst.scheduled_payment_cents - int(inst.amount_paid_cents or 0))

            loan.total_interest_cents = max(0, int(loan.total_interest_cents or 0) - total_rebated_interest_cents)
            loan.total_repayment_cents = int(loan.principal_cents or 0) + loan.total_interest_cents
            loan.remaining_contractual_interest_cents = max(0, int(loan.remaining_contractual_interest_cents or 0) - total_rebated_interest_cents)
            loan.remaining_contract_total_cents = int(loan.unpaid_principal_cents or 0) + loan.remaining_contractual_interest_cents

            rebate_tag = f"[Early Payoff Rebate: -${total_rebated_interest_cents / 100:,.2f} MXN future interest forgiven]"
            effective_notes = f"{effective_notes} {rebate_tag}".strip() if effective_notes else rebate_tag

        result = LoanPaymentEngine.execute_waterfall_allocation(
            db=db, loan=loan, installments=installments, amount_cents=amount_cents,
            payment_method=payload.payment_method, payment_reference=payment_ref_clean,
            paid_at_dt=paid_at_dt, notes=effective_notes, user_id=current_user.id,
            idempotency_key=payload.idempotency_key, payload_hash=payload_hash,
            rebate_detail_json=serialize_rebate_detail(rebated_installments_map)
        )

        record_audit_event(
            db, "LOAN_PAYMENT_RECORDED", "loan", "SUCCESS", actor=current_user,
            resource_id=result["payment"].payment_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "amount_cents": amount_cents,
                "method": payload.payment_method,
                "early_payoff_rebate_cents": total_rebated_interest_cents,
                "rebated_installments_count": len(rebated_installments_map)
            },
            commit=False
        )
        db.commit()
        db.refresh(result["payment"])
        return {
            "idempotent_replay": False,
            "payment": serialize_loan_payment(result["payment"], include_allocations=True, is_top_reversible=True),
            "loan": serialize_loan_detail(loan, today_local, db=db)
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as e:
        db.rollback()
        orig_msg = str(getattr(e, "orig", e))
        if "uq_loan_payments_posted_reference" in orig_msg or "uq_loan_payments_posted_method_ref" in orig_msg:
            raise HTTPException(status_code=409, detail="Duplicate payment reference detected.")
        if "uq_loan_payments_tenant_idempotency_key" in orig_msg or "uq_loan_payments_idempotency_key" in orig_msg:
            raise HTTPException(status_code=409, detail="Concurrency collision on idempotency key.")
        logger.exception("Integrity error while recording payment")
        raise HTTPException(status_code=500, detail="Payment could not be recorded.")
    except Exception:
        db.rollback()
        logger.exception("Failed to record payment")
        raise HTTPException(status_code=500, detail="Payment could not be recorded.")


@router.post("/api/loans/{loan_id}/payments/{payment_id}/reverse", dependencies=[Depends(verify_csrf)])
def reverse_loan_payment(
    loan_id: int,
    payment_id: int,
    payload: LoanPaymentReverseRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        loan = (
            db.query(LoanModel)
            .filter(LoanModel.id == loan_id, LoanModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status == "DEFAULTED":
            raise HTTPException(status_code=409, detail="Cannot reverse payments on a DEFAULTED loan.")

        target_payment = db.query(LoanPaymentModel).filter(
            LoanPaymentModel.id == payment_id,
            LoanPaymentModel.loan_id == loan_id,
            LoanPaymentModel.tenant_id == current_user.tenant_id
        ).with_for_update().first()

        if not target_payment:
            raise HTTPException(status_code=404, detail="Payment not found.")
        if target_payment.status == "REVERSED":
            raise HTTPException(status_code=409, detail="Payment is already reversed.")

        latest_payment = db.query(LoanPaymentModel.id).filter(
            LoanPaymentModel.tenant_id == current_user.tenant_id,
            LoanPaymentModel.loan_id == loan_id,
            LoanPaymentModel.status == 'POSTED'
        ).order_by(LoanPaymentModel.loan_payment_seq.desc()).first()

        if not latest_payment or latest_payment[0] != payment_id:
            raise HTTPException(status_code=409, detail="Only the most recent POSTED payment can be reversed to preserve ledger integrity.")

        LoanPaymentEngine.execute_reversal_restoration(db, loan, target_payment, payload.reversal_reason.strip(), current_user.id)

        # Restore rebated future interest if this payment was an early payoff settlement.
        # Fuente de verdad: loan_payments.rebate_detail_json. Cualquier error aqui aborta TODA la
        # reversa (rollback): nunca se debe dejar el prestamo a medio restaurar.
        rebated_map = parse_rebate_detail(target_payment.rebate_detail_json)
        if rebated_map:
            rebated_insts = (
                db.query(LoanInstallmentModel)
                .filter(
                    LoanInstallmentModel.tenant_id == current_user.tenant_id,
                    LoanInstallmentModel.loan_id == loan.id,
                    LoanInstallmentModel.id.in_(list(rebated_map.keys()))
                )
                .with_for_update()
                .all()
            )
            inst_by_id = {int(i.id): i for i in rebated_insts}
            missing_ids = set(rebated_map.keys()) - set(inst_by_id.keys())
            if missing_ids:
                raise ValueError(f"Data corruption: rebated installments not found: {sorted(missing_ids)}")

            restored_total_cents = 0
            for inst_id, add_cents in rebated_map.items():
                inst_obj = inst_by_id[inst_id]
                inst_obj.interest_due_cents = int(inst_obj.interest_due_cents or 0) + add_cents
                inst_obj.scheduled_payment_cents = int(inst_obj.principal_due_cents or 0) + inst_obj.interest_due_cents
                inst_obj.remaining_amount_cents = max(
                    0, inst_obj.scheduled_payment_cents - int(inst_obj.amount_paid_cents or 0)
                )
                restored_total_cents += add_cents

            # execute_reversal_restoration dejo los saldos del prestamo en sus valores "prev_*"
            # (ya con el rebate aplicado); sumar de vuelta lo perdonado reconstruye el estado original.
            loan.total_interest_cents = int(loan.total_interest_cents or 0) + restored_total_cents
            loan.total_repayment_cents = int(loan.principal_cents or 0) + loan.total_interest_cents
            loan.remaining_contractual_interest_cents = (
                int(loan.remaining_contractual_interest_cents or 0) + restored_total_cents
            )
            loan.remaining_contract_total_cents = (
                int(loan.remaining_contract_total_cents or 0) + restored_total_cents
            )
            db.flush()

        record_audit_event(
            db, "LOAN_PAYMENT_REVERSED", "loan", "SUCCESS", actor=current_user,
            resource_id=target_payment.payment_number, ip_address=ip,
            details={"loan_number": loan.loan_number, "amount_cents": target_payment.amount_cents, "reason": payload.reversal_reason}, commit=False
        )
        db.commit()
        db.refresh(target_payment)
        _, _, _, today_local = get_local_day_bounds_utc()
        return {
            "message": "Payment reversed successfully",
            "payment": serialize_loan_payment(target_payment),
            "loan": serialize_loan_detail(loan, today_local, db=db)
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to reverse payment")
        raise HTTPException(status_code=500, detail=f"Reversal failed: {type(e).__name__}")
