from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.utils import get_crm_timezone, get_local_day_bounds_utc
from dependencies.auth import require_commercial_tenant
from dependencies.db import get_db
from models import CustomerModel, LeadModel, LoanInstallmentModel, LoanModel, LoanPaymentModel, UserModel

router = APIRouter()


@router.get("/api/dashboard/summary")
def get_dashboard_summary(
    current_user: UserModel = Depends(require_commercial_tenant),
    db: Session = Depends(get_db)
):
    _, _, _, today_local = get_local_day_bounds_utc()
    active_loans = (
        db.query(LoanModel)
        .filter(LoanModel.tenant_id == current_user.tenant_id, LoanModel.status == "ACTIVE")
        .all()
    )
    paid_off_loans_count = (
        db.query(LoanModel)
        .filter(LoanModel.tenant_id == current_user.tenant_id, LoanModel.status == "PAID_OFF")
        .count()
    )
    defaulted_loans_count = (
        db.query(LoanModel)
        .filter(LoanModel.tenant_id == current_user.tenant_id, LoanModel.status == "DEFAULTED")
        .count()
    )

    active_portfolio_cents = sum(l.unpaid_principal_cents for l in active_loans)
    total_outstanding_principal_cents = active_portfolio_cents
    total_contract_due_cents = sum(l.remaining_contract_total_cents for l in active_loans)

    due_today_cents = 0
    overdue_amount_cents = 0
    due_today_installments_count = 0
    overdue_installments_count = 0
    overdue_loan_ids = set()

    if active_loans:
        loan_ids = [l.id for l in active_loans]
        unpaid_insts = (
            db.query(LoanInstallmentModel)
            .filter(
                LoanInstallmentModel.tenant_id == current_user.tenant_id,
                LoanInstallmentModel.loan_id.in_(loan_ids),
                LoanInstallmentModel.remaining_amount_cents > 0
            )
            .all()
        )
        for inst in unpaid_insts:
            if inst.due_date == today_local:
                due_today_cents += inst.remaining_amount_cents
                due_today_installments_count += 1
            elif inst.due_date < today_local:
                overdue_amount_cents += inst.remaining_amount_cents
                overdue_installments_count += 1
                overdue_loan_ids.add(inst.loan_id)

    customers_count = db.query(CustomerModel).filter(CustomerModel.tenant_id == current_user.tenant_id).count()
    open_leads_count = (
        db.query(LeadModel)
        .filter(
            LeadModel.tenant_id == current_user.tenant_id,
            LeadModel.stage.notin_(["CONVERTED", "LOST"])
        )
        .count()
    )
    active_loans_count = len(active_loans)
    overdue_loans_count = len(overdue_loan_ids)
    current_loans_count = max(0, active_loans_count - overdue_loans_count)

    # Bloque G-2: Semana ISO para pagos cobrados
    crm_tz = get_crm_timezone()
    now_local = datetime.now(crm_tz)
    monday_local = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday_local = (monday_local + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)

    collected_week_query = db.query(
        func.coalesce(func.sum(LoanPaymentModel.amount_cents), 0),
        func.count(LoanPaymentModel.id)
    ).filter(
        LoanPaymentModel.tenant_id == current_user.tenant_id,
        LoanPaymentModel.status == 'POSTED',
        LoanPaymentModel.paid_at >= monday_local.astimezone(timezone.utc),
        LoanPaymentModel.paid_at <= sunday_local.astimezone(timezone.utc)
    ).first()

    collected_week_cents = int(collected_week_query[0] or 0) if collected_week_query else 0
    collected_week_payments_count = int(collected_week_query[1] or 0) if collected_week_query else 0

    return {
        "active_loan_portfolio_cents": active_portfolio_cents,
        "due_today_cents": due_today_cents,
        "overdue_amount_cents": overdue_amount_cents,
        "total_outstanding_principal_cents": total_outstanding_principal_cents,
        "total_amount_due_cents": total_contract_due_cents,
        "collected_this_week_enabled": True,
        "collected_this_week_cents": collected_week_cents,
        "counts": {
            "active_loans": active_loans_count,
            "current_loans": current_loans_count,
            "overdue_loans": overdue_loans_count,
            "paid_off_loans": paid_off_loans_count,
            "defaulted_loans": defaulted_loans_count,
            "due_today_installments": due_today_installments_count,
            "overdue_installments": overdue_installments_count,
            "collected_week_payments": collected_week_payments_count,
            "customers": customers_count,
            "open_leads": open_leads_count
        }
    }
