from datetime import timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.utils import get_crm_timezone, get_local_day_bounds_utc
from models import (
    CustomerModel,
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentModel,
)
from services.serializers_core import serialize_customer
from services.serializers_lending import serialize_loan_summary


def get_customer_detail_payload(db: Session, tenant_id: int, customer_id: int) -> Optional[dict]:
    """Composes the full customer detail response coordinating Core + Lending.

    Returns None when the customer does not exist in `tenant_id` so the HTTP layer
    can raise the appropriate 404 response without coupling services/ to FastAPI.
    """
    customer = (
        db.query(CustomerModel)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == tenant_id)
        .first()
    )
    if not customer:
        return None

    active_loans = (
        db.query(LoanModel)
        .filter(
            LoanModel.tenant_id == tenant_id,
            LoanModel.customer_id == customer.id,
            LoanModel.status.in_(["ACTIVE", "DEFAULTED"])
        )
        .all()
    )
    all_loans = (
        db.query(LoanModel)
        .filter(LoanModel.tenant_id == tenant_id, LoanModel.customer_id == customer.id)
        .order_by(LoanModel.id.desc())
        .all()
    )

    total_outstanding_principal = sum(l.unpaid_principal_cents for l in active_loans)
    total_amount_due = sum(l.remaining_contract_total_cents for l in active_loans)

    _, _, _, today_local = get_local_day_bounds_utc()
    overdue_amt = 0
    next_due_date = None
    remaining_inst_count = 0

    if active_loans:
        loan_ids = [l.id for l in active_loans]
        unpaid_insts = (
            db.query(LoanInstallmentModel)
            .filter(
                LoanInstallmentModel.tenant_id == tenant_id,
                LoanInstallmentModel.loan_id.in_(loan_ids),
                LoanInstallmentModel.remaining_amount_cents > 0
            )
            .order_by(LoanInstallmentModel.due_date.asc())
            .all()
        )
        remaining_inst_count = len(unpaid_insts)
        if unpaid_insts:
            next_due_date = unpaid_insts[0].due_date.isoformat()
            for inst in unpaid_insts:
                if inst.due_date < today_local:
                    overdue_amt += inst.remaining_amount_cents

    data = serialize_customer(
        customer,
        active_loans_count=len(active_loans),
        unpaid_principal_cents=total_outstanding_principal
    )
    data["credit_summary"] = {
        "current_outstanding_principal_cents": total_outstanding_principal,
        "total_amount_due_cents": total_amount_due,
        "overdue_amount_cents": overdue_amt,
        "active_loans_count": len(active_loans),
        "total_loans_count": len(all_loans),
        "last_payment_date": None,
        "next_payment_due_date": next_due_date,
        "remaining_installments": remaining_inst_count
    }
    data["loans"] = [serialize_loan_summary(l, today_local) for l in all_loans]

    last_payment = db.query(func.max(LoanPaymentModel.paid_at)).filter(
        LoanPaymentModel.tenant_id == tenant_id,
        LoanPaymentModel.customer_id == customer.id,
        LoanPaymentModel.status == "POSTED"
    ).scalar()
    if last_payment:
        if isinstance(last_payment, str):
            formatted_last_pay = last_payment[:10]
        else:
            dt_utc = last_payment if last_payment.tzinfo else last_payment.replace(tzinfo=timezone.utc)
            formatted_last_pay = dt_utc.astimezone(get_crm_timezone()).strftime("%Y-%m-%d")
        data["last_payment_date"] = formatted_last_pay
        data["credit_summary"]["last_payment_date"] = formatted_last_pay

    return data


def get_customers_loan_stats(
    db: Session,
    tenant_id: int,
    customer_ids: list[int]
) -> tuple[dict, dict]:
    """Pre-fetches active/defaulted loan balances and latest posted payment date for a page of customers."""
    loan_stats: dict = {}
    last_payments_map: dict = {}
    if not customer_ids:
        return loan_stats, last_payments_map

    active_loans = (
        db.query(LoanModel)
        .filter(
            LoanModel.tenant_id == tenant_id,
            LoanModel.customer_id.in_(customer_ids),
            LoanModel.status.in_(["ACTIVE", "DEFAULTED"])
        )
        .all()
    )
    for l in active_loans:
        st = loan_stats.setdefault(l.customer_id, {"count": 0, "principal": 0})
        st["count"] += 1
        st["principal"] += l.unpaid_principal_cents

    payment_rows = (
        db.query(
            LoanPaymentModel.customer_id,
            func.max(LoanPaymentModel.paid_at)
        )
        .filter(
            LoanPaymentModel.tenant_id == tenant_id,
            LoanPaymentModel.customer_id.in_(customer_ids),
            LoanPaymentModel.status == "POSTED"
        )
        .group_by(LoanPaymentModel.customer_id)
        .all()
    )
    crm_tz = get_crm_timezone()
    for cid, max_paid_at in payment_rows:
        if max_paid_at:
            if isinstance(max_paid_at, str):
                last_payments_map[cid] = max_paid_at[:10]
            else:
                dt_utc = max_paid_at if max_paid_at.tzinfo else max_paid_at.replace(tzinfo=timezone.utc)
                last_payments_map[cid] = dt_utc.astimezone(crm_tz).strftime("%Y-%m-%d")

    return loan_stats, last_payments_map


def get_customer_active_loan_summary(
    db: Session,
    tenant_id: int,
    customer: CustomerModel
) -> tuple[int, int]:
    """Returns (active_loans_count, unpaid_principal_cents) for a single customer."""
    active_loans_count = (
        db.query(LoanModel)
        .filter(
            LoanModel.tenant_id == tenant_id,
            LoanModel.customer_id == customer.id,
            LoanModel.status == "ACTIVE"
        )
        .count()
    )
    unpaid_principal = sum(l.unpaid_principal_cents for l in customer.loans if l.status == "ACTIVE")
    return active_loans_count, unpaid_principal
