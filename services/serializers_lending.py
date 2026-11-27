from datetime import date
import json
from typing import Optional

from sqlalchemy.orm import Session

from models import (
    AuditLogModel,
    InteractionModel,
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentModel,
)


def serialize_loan_summary(l: LoanModel, today_local: date) -> dict:
    is_live_obligation = l.status in ("ACTIVE", "DEFAULTED")
    overdue_amt = 0
    due_today_amt = 0
    accrued_int = 0
    overdue_inst_count = 0
    oldest_overdue_date: Optional[date] = None

    if (is_live_obligation or l.status == "PAID_OFF") and l.installments:
        for inst in l.installments:
            if inst.remaining_amount_cents > 0:
                if inst.due_date < today_local:
                    overdue_amt += inst.remaining_amount_cents
                    overdue_inst_count += 1
                    if oldest_overdue_date is None or inst.due_date < oldest_overdue_date:
                        oldest_overdue_date = inst.due_date
                elif inst.due_date == today_local:
                    due_today_amt += inst.remaining_amount_cents

            if inst.due_date <= today_local:
                accrued_int += max(0, inst.interest_due_cents - inst.interest_paid_cents)

    days_past_due = (today_local - oldest_overdue_date).days if oldest_overdue_date else 0
    cust = l.customer

    return {
        "id": l.id,
        "loan_number": l.loan_number,
        "customer_id": l.customer_id,
        "customer_number": cust.customer_number if cust else f"CUST-{l.customer_id:05d}",
        "customer_name": cust.full_legal_name if cust else f"Customer #{l.customer_id}",
        "customer_phone": cust.phone_primary if cust else None,
        "status": l.status,
        "product_name": l.product_name,
        "currency": l.currency,
        "principal_cents": l.principal_cents,
        "monthly_interest_rate_percent": l.monthly_interest_rate_bps / 100.0,
        "term_installments_count": l.term_installments_count,
        "unpaid_principal_cents": l.unpaid_principal_cents,
        "remaining_contractual_interest_cents": l.remaining_contractual_interest_cents,
        "remaining_contract_total_cents": l.remaining_contract_total_cents,
        "accrued_interest_cents": accrued_int,
        "due_today_cents": due_today_amt,
        "overdue_amount_cents": overdue_amt,
        "overdue_installments_count": overdue_inst_count,
        "days_past_due": days_past_due,
        "current_payable_amount_cents": due_today_amt + overdue_amt,
        "start_date": l.start_date.isoformat(),
        "first_payment_date": l.first_payment_date.isoformat(),
        "maturity_date": l.maturity_date.isoformat(),
        "created_at": l.created_at.isoformat() if l.created_at else None
    }


def compute_early_payoff_breakdown(
    l: LoanModel,
    today_local: date,
    db: Optional[Session] = None,
    base_summary: Optional[dict] = None
) -> dict:
    rem_contract_total = int(
        (base_summary or {}).get("remaining_contract_total_cents")
        or getattr(l, "remaining_contract_total_cents", 0)
        or 0
    )
    if l.status != "ACTIVE":
        return {
            "unpaid_accrued_interest_cents": 0,
            "early_payoff_rebate_cents": 0,
            "early_payoff_amount_cents": rem_contract_total
        }

    installments = list(getattr(l, "installments", None) or [])
    if not installments and db is not None:
        installments = (
            db.query(LoanInstallmentModel)
            .filter(
                LoanInstallmentModel.tenant_id == l.tenant_id,
                LoanInstallmentModel.loan_id == l.id
            )
            .order_by(LoanInstallmentModel.installment_number.asc())
            .all()
        )

    if not installments:
        return {
            "unpaid_accrued_interest_cents": 0,
            "early_payoff_rebate_cents": 0,
            "early_payoff_amount_cents": rem_contract_total
        }

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

    return {
        "unpaid_accrued_interest_cents": unpaid_accrued_interest,
        "early_payoff_rebate_cents": unearned_future_interest,
        "early_payoff_amount_cents": unpaid_principal + unpaid_accrued_interest
    }


def serialize_loan_detail(l: LoanModel, today_local: date, db: Optional[Session] = None) -> dict:
    base = serialize_loan_summary(l, today_local)
    base.update(compute_early_payoff_breakdown(l, today_local, db=db, base_summary=base))

    defaulted_at_iso = None
    defaulted_by_user_id = None
    default_reason_str = None

    if l.status == "DEFAULTED" and db is not None:
        audit_row = (
            db.query(AuditLogModel)
            .filter(
                AuditLogModel.tenant_id == l.tenant_id,
                AuditLogModel.scope == "TENANT",
                AuditLogModel.event_type == "LOAN_MARKED_DEFAULTED",
                AuditLogModel.resource_id == l.loan_number
            )
            .order_by(AuditLogModel.id.desc())
            .first()
        )
        if audit_row:
            defaulted_at_iso = audit_row.created_at.isoformat() if audit_row.created_at else None
            defaulted_by_user_id = audit_row.actor_user_id

        default_int = (
            db.query(InteractionModel)
            .filter(
                InteractionModel.tenant_id == l.tenant_id,
                InteractionModel.customer_id == l.customer_id,
                InteractionModel.content.like(f"%LOAN MARKED AS DEFAULTED ({l.loan_number})%")
            )
            .order_by(InteractionModel.id.desc())
            .first()
        )
        if default_int and "Reason: " in default_int.content:
            default_reason_str = default_int.content.split("Reason: ", 1)[1].strip()

    base.update({
        "source_lead_id": l.source_lead_id,
        "product_version": l.product_version,
        "interest_calculation_method": l.interest_calculation_method,
        "interest_basis": l.interest_basis,
        "interest_accrual_convention": l.interest_accrual_convention,
        "assumed_interest_months": l.assumed_interest_months,
        "payment_frequency": l.payment_frequency,
        "rounding_policy": l.rounding_policy,
        "contract_rules": json.loads(l.contract_rules_json) if l.contract_rules_json else None,
        "monthly_interest_amount_cents": l.monthly_interest_amount_cents,
        "total_interest_cents": l.total_interest_cents,
        "total_repayment_cents": l.total_repayment_cents,
        "regular_installment_cents": l.regular_installment_cents,
        "final_installment_cents": l.final_installment_cents,
        "amount_paid_cents": l.amount_paid_cents,
        "owner_user_id": l.owner_user_id,
        "created_by_user_id": l.created_by_user_id,
        "approved_by_user_id": l.approved_by_user_id,
        "approved_at": l.approved_at.isoformat() if l.approved_at else None,
        "approval_notes": l.approval_notes,
        "activated_by_user_id": l.activated_by_user_id,
        "activated_at": l.activated_at.isoformat() if l.activated_at else None,
        "disbursed_at": l.disbursed_at.isoformat() if l.disbursed_at else None,
        "disbursement_method": l.disbursement_method,
        "disbursement_reference": l.disbursement_reference,
        "disbursement_notes": l.disbursement_notes,
        "cancelled_by_user_id": l.cancelled_by_user_id,
        "cancelled_at": l.cancelled_at.isoformat() if l.cancelled_at else None,
        "cancellation_reason": l.cancellation_reason,
        "defaulted_by_user_id": defaulted_by_user_id,
        "defaulted_at": defaulted_at_iso,
        "default_reason": default_reason_str,
        "notes": l.notes,
        "installments": []
    })

    for inst in l.installments:
        if inst.remaining_amount_cents == 0:
            status = "PAID"
        elif l.status in ("ACTIVE", "DEFAULTED") and inst.due_date < today_local:
            status = "OVERDUE"
        elif l.status in ("ACTIVE", "DEFAULTED") and inst.due_date == today_local:
            status = "DUE_TODAY"
        elif inst.amount_paid_cents > 0 and inst.remaining_amount_cents > 0:
            status = "PARTIALLY_PAID"
        else:
            status = "SCHEDULED"

        base["installments"].append({
            "id": inst.id,
            "installment_number": inst.installment_number,
            "due_date": inst.due_date.isoformat(),
            "beginning_principal_cents": inst.beginning_principal_cents,
            "principal_due_cents": inst.principal_due_cents,
            "interest_due_cents": inst.interest_due_cents,
            "scheduled_payment_cents": inst.scheduled_payment_cents,
            "ending_principal_cents": inst.ending_principal_cents,
            "principal_paid_cents": inst.principal_paid_cents,
            "interest_paid_cents": inst.interest_paid_cents,
            "amount_paid_cents": inst.amount_paid_cents,
            "remaining_amount_cents": inst.remaining_amount_cents,
            "paid_at": inst.paid_at.isoformat() if inst.paid_at else None,
            "status": status
        })
    return base


def serialize_loan_payment(p: LoanPaymentModel, include_allocations: bool = False, is_top_reversible: bool = False) -> dict:
    data = {
        "id": p.id,
        "payment_number": p.payment_number,
        "loan_id": p.loan_id,
        "status": p.status,
        "amount_cents": p.amount_cents,
        "interest_applied_cents": p.interest_applied_cents,
        "principal_applied_cents": p.principal_applied_cents,
        "payment_method": p.payment_method,
        "payment_reference": p.payment_reference,
        "paid_at": p.paid_at.isoformat(),
        "notes": p.notes,
        "recorded_by_user_id": p.recorded_by_user_id,
        "reversed_by_user_id": p.reversed_by_user_id,
        "reversed_at": p.reversed_at.isoformat() if p.reversed_at else None,
        "reversal_reason": p.reversal_reason,
        "created_at": p.created_at.isoformat(),
        "is_top_reversible": is_top_reversible
    }
    if include_allocations and p.allocations:
        data["allocations"] = [{
            "installment_number": a.installment_number,
            "interest_allocated_cents": a.interest_allocated_cents,
            "principal_allocated_cents": a.principal_allocated_cents,
            "total_allocated_cents": a.total_allocated_cents
        } for a in p.allocations]
    return data
