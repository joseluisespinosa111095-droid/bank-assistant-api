from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.utils import generate_next_payment_number
from models import (
    LoanInstallmentModel,
    LoanModel,
    LoanPaymentAllocationModel,
    LoanPaymentModel,
)


# --- AUTHORITATIVE FINANCIAL ENGINE (FLAT & DECLINING BALANCE) ---
class LoanFinancialEngine:
    @staticmethod
    def add_days_iso(iso_date_str: str, days_to_add: int) -> str:
        parts = [int(p) for p in iso_date_str.split("-")]
        dt = date(parts[0], parts[1], parts[2]) + timedelta(days=days_to_add)
        return dt.isoformat()

    @staticmethod
    def calculate_flat_schedule(
        principal_pesos: float,
        monthly_rate_percent: float,
        assumed_interest_months: int,
        term_weeks: int,
        first_payment_date_iso: str,
        interest_calculation_method: str = "FLAT_ORIGINAL_PRINCIPAL"
    ) -> dict:
        import math
        principal_cents = max(0, int(round(Decimal(str(principal_pesos)) * Decimal("100"))))
        rate_bps = max(0, int(round(Decimal(str(monthly_rate_percent)) * Decimal("100"))))
        months_count = max(1, int(assumed_interest_months))
        installments_count = max(1, int(term_weeks))
        method = (interest_calculation_method or "FLAT_ORIGINAL_PRINCIPAL").strip().upper()

        monthly_interest_cents = int(
            ((Decimal(principal_cents) * Decimal(rate_bps)) / Decimal("10000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

        schedule = []
        remaining_principal_cents = principal_cents
        accumulated_principal_cents = 0
        accumulated_interest_cents = 0

        if method == "DECLINING_BALANCE":
            if rate_bps == 0:
                weekly_rate = 0.0
                regular_payment_cents = principal_cents // installments_count
            else:
                weekly_rate = (rate_bps * 12.0) / (10000.0 * 52.0)
                factor = (1.0 + weekly_rate) ** installments_count
                raw_pmt = principal_cents * (weekly_rate * factor) / (factor - 1.0)
                regular_payment_cents = int(math.floor(raw_pmt + 0.5))

            for i in range(1, installments_count + 1):
                is_final = (i == installments_count)
                due_date_str = LoanFinancialEngine.add_days_iso(first_payment_date_iso, (i - 1) * 7)
                due_date_obj = date.fromisoformat(due_date_str)
                beginning_principal_cents = remaining_principal_cents

                interest_component_cents = (
                    int(math.floor(beginning_principal_cents * weekly_rate + 0.5))
                    if rate_bps > 0 else 0
                )
                if is_final:
                    principal_component_cents = remaining_principal_cents
                else:
                    principal_component_cents = max(
                        0,
                        min(remaining_principal_cents, regular_payment_cents - interest_component_cents)
                    )

                scheduled_payment_cents = principal_component_cents + interest_component_cents
                ending_principal_cents = beginning_principal_cents - principal_component_cents

                accumulated_principal_cents += principal_component_cents
                accumulated_interest_cents += interest_component_cents
                remaining_principal_cents = ending_principal_cents

                schedule.append({
                    "installment_number": i,
                    "due_date": due_date_obj,
                    "beginning_principal_cents": beginning_principal_cents,
                    "principal_due_cents": principal_component_cents,
                    "interest_due_cents": interest_component_cents,
                    "scheduled_payment_cents": scheduled_payment_cents,
                    "ending_principal_cents": ending_principal_cents
                })

            total_interest_cents = accumulated_interest_cents
            total_repayment_cents = principal_cents + total_interest_cents
            monthly_interest_cents = int(math.floor((total_interest_cents / months_count) + 0.5))
        else:
            total_interest_cents = monthly_interest_cents * months_count
            total_repayment_cents = principal_cents + total_interest_cents
            regular_principal_cents = principal_cents // installments_count
            regular_interest_cents = total_interest_cents // installments_count

            for i in range(1, installments_count + 1):
                is_final = (i == installments_count)
                due_date_str = LoanFinancialEngine.add_days_iso(first_payment_date_iso, (i - 1) * 7)
                due_date_obj = date.fromisoformat(due_date_str)
                beginning_principal_cents = remaining_principal_cents

                principal_component_cents = (
                    (principal_cents - accumulated_principal_cents) if is_final else regular_principal_cents
                )
                interest_component_cents = (
                    (total_interest_cents - accumulated_interest_cents) if is_final else regular_interest_cents
                )
                scheduled_payment_cents = principal_component_cents + interest_component_cents
                ending_principal_cents = beginning_principal_cents - principal_component_cents

                accumulated_principal_cents += principal_component_cents
                accumulated_interest_cents += interest_component_cents
                remaining_principal_cents = ending_principal_cents

                schedule.append({
                    "installment_number": i,
                    "due_date": due_date_obj,
                    "beginning_principal_cents": beginning_principal_cents,
                    "principal_due_cents": principal_component_cents,
                    "interest_due_cents": interest_component_cents,
                    "scheduled_payment_cents": scheduled_payment_cents,
                    "ending_principal_cents": ending_principal_cents
                })

        return {
            "interest_calculation_method": method,
            "principal_cents": principal_cents,
            "monthly_rate_percent": float(monthly_rate_percent),
            "monthly_interest_rate_bps": rate_bps,
            "monthly_interest_amount_cents": monthly_interest_cents,
            "months_count": months_count,
            "installments_count": installments_count,
            "total_interest_cents": total_interest_cents,
            "total_repayment_cents": total_repayment_cents,
            "regular_installment_cents": schedule[0]["scheduled_payment_cents"],
            "final_installment_cents": schedule[-1]["scheduled_payment_cents"],
            "first_payment_date": date.fromisoformat(first_payment_date_iso),
            "maturity_date": schedule[-1]["due_date"],
            "schedule": schedule
        }


class LoanPaymentEngine:
    @staticmethod
    def _verify_ledger_integrity_python(loan: LoanModel, installments: List[LoanInstallmentModel], db: Session):
        posted_payments = db.query(LoanPaymentModel).filter(
            LoanPaymentModel.tenant_id == loan.tenant_id,
            LoanPaymentModel.loan_id == loan.id,
            LoanPaymentModel.status == 'POSTED'
        ).all()

        posted_amt = sum(p.amount_cents for p in posted_payments)
        inst_amt = sum(i.amount_paid_cents for i in installments)
        if loan.amount_paid_cents != posted_amt or loan.amount_paid_cents != inst_amt:
            raise RuntimeError(f"Python Ledger Violation: amount_paid_cents mismatch. Loan={loan.amount_paid_cents}, Posted={posted_amt}, Inst={inst_amt}")

        posted_int = sum(p.interest_applied_cents for p in posted_payments)
        inst_int = sum(i.interest_paid_cents for i in installments)
        if (loan.total_interest_cents - loan.remaining_contractual_interest_cents) != posted_int or posted_int != inst_int:
            raise RuntimeError("Python Ledger Violation: interest_paid mismatch.")

        posted_prin = sum(p.principal_applied_cents for p in posted_payments)
        inst_prin = sum(i.principal_paid_cents for i in installments)
        if (loan.principal_cents - loan.unpaid_principal_cents) != posted_prin or posted_prin != inst_prin:
            raise RuntimeError("Python Ledger Violation: principal_paid mismatch.")

        rem_total = sum(i.remaining_amount_cents for i in installments)
        if loan.remaining_contract_total_cents != rem_total:
            raise RuntimeError("Python Ledger Violation: remaining_contract_total_cents mismatch.")

    @staticmethod
    def execute_waterfall_allocation(db: Session, loan: LoanModel, installments: List[LoanInstallmentModel], amount_cents: int, payment_method: str, payment_reference: str, paid_at_dt: datetime, notes: str, user_id: int, idempotency_key: str, payload_hash: str, rebate_detail_json: Optional[str] = None) -> dict:
        if amount_cents <= 0 or amount_cents > loan.remaining_contract_total_cents:
            raise ValueError("Payment amount must be > 0 and cannot exceed the remaining contract total.")

        payment_number = generate_next_payment_number(db, loan.tenant_id)
        max_seq = db.query(func.max(LoanPaymentModel.loan_payment_seq)).filter(
            LoanPaymentModel.tenant_id == loan.tenant_id,
            LoanPaymentModel.loan_id == loan.id
        ).scalar()
        next_seq = (max_seq or 0) + 1

        prev_loan_status = loan.status
        prev_unpaid_principal = int(loan.unpaid_principal_cents)
        prev_remaining_interest = int(loan.remaining_contractual_interest_cents)
        prev_remaining_total = int(loan.remaining_contract_total_cents)
        prev_amount_paid = int(loan.amount_paid_cents)

        remaining_to_allocate = int(amount_cents)
        interest_applied = 0
        principal_applied = 0
        pending_allocations = []

        for inst in installments:
            if remaining_to_allocate <= 0:
                break
            if inst.remaining_amount_cents <= 0:
                continue

            prev_prin_paid = int(inst.principal_paid_cents)
            prev_int_paid = int(inst.interest_paid_cents)
            prev_amt_paid = int(inst.amount_paid_cents)
            prev_rem_amt = int(inst.remaining_amount_cents)
            prev_paid_at = inst.paid_at

            int_pending = max(0, int(inst.interest_due_cents) - prev_int_paid)
            int_alloc = min(remaining_to_allocate, int_pending)
            if int_alloc > 0:
                inst.interest_paid_cents = prev_int_paid + int_alloc
                remaining_to_allocate -= int_alloc
                interest_applied += int_alloc

            prin_pending = max(0, int(inst.principal_due_cents) - prev_prin_paid)
            prin_alloc = min(remaining_to_allocate, prin_pending)
            if prin_alloc > 0:
                inst.principal_paid_cents = prev_prin_paid + prin_alloc
                remaining_to_allocate -= prin_alloc
                principal_applied += prin_alloc

            total_alloc = int_alloc + prin_alloc
            if total_alloc > 0:
                inst.amount_paid_cents = prev_amt_paid + total_alloc
                inst.remaining_amount_cents = int(inst.scheduled_payment_cents) - int(inst.amount_paid_cents)
                if inst.remaining_amount_cents == 0:
                    inst.paid_at = paid_at_dt

                pending_allocations.append({
                    "installment_id": inst.id,
                    "installment_number": inst.installment_number,
                    "interest_allocated_cents": int_alloc,
                    "principal_allocated_cents": prin_alloc,
                    "total_allocated_cents": total_alloc,
                    "prev_principal_paid_cents": prev_prin_paid,
                    "prev_interest_paid_cents": prev_int_paid,
                    "prev_amount_paid_cents": prev_amt_paid,
                    "prev_remaining_amount_cents": prev_rem_amt,
                    "prev_paid_at": prev_paid_at
                })

        if remaining_to_allocate != 0 or (interest_applied + principal_applied) != amount_cents:
            raise RuntimeError("Waterfall allocation mismatch: unable to allocate exact payment amount across installments.")

        payment = LoanPaymentModel(
            tenant_id=loan.tenant_id,
            payment_number=payment_number,
            loan_id=loan.id,
            customer_id=loan.customer_id,
            loan_payment_seq=next_seq,
            idempotency_key=idempotency_key,
            request_payload_hash=payload_hash,
            status="POSTED",
            currency="MXN",
            amount_cents=int(amount_cents),
            interest_applied_cents=int(interest_applied),
            principal_applied_cents=int(principal_applied),
            prev_loan_status=prev_loan_status,
            prev_unpaid_principal_cents=prev_unpaid_principal,
            prev_remaining_interest_cents=prev_remaining_interest,
            prev_remaining_total_cents=prev_remaining_total,
            prev_amount_paid_cents=prev_amount_paid,
            payment_method=payment_method,
            payment_reference=payment_reference,
            paid_at=paid_at_dt,
            notes=notes,
            rebate_detail_json=rebate_detail_json,
            recorded_by_user_id=user_id
        )
        db.add(payment)
        db.flush()

        for alloc_data in pending_allocations:
            db.add(LoanPaymentAllocationModel(
                tenant_id=loan.tenant_id,
                payment_id=payment.id,
                loan_id=loan.id,
                **alloc_data
            ))

        loan.unpaid_principal_cents = prev_unpaid_principal - principal_applied
        loan.remaining_contractual_interest_cents = prev_remaining_interest - interest_applied
        loan.remaining_contract_total_cents = prev_remaining_total - int(amount_cents)
        loan.amount_paid_cents = prev_amount_paid + int(amount_cents)

        if loan.remaining_contract_total_cents == 0:
            loan.status = "PAID_OFF"
        loan.updated_at = datetime.now(timezone.utc)

        db.flush()
        if db.bind and db.bind.dialect.name == "sqlite":
            LoanPaymentEngine._verify_ledger_integrity_python(loan, installments, db)

        return {"payment": payment, "allocations_count": len(pending_allocations)}

    @staticmethod
    def execute_reversal_restoration(db: Session, loan: LoanModel, payment: LoanPaymentModel, reason: str, user_id: int):
        now_utc = datetime.now(timezone.utc)

        for alloc in payment.allocations:
            inst = db.query(LoanInstallmentModel).filter(
                LoanInstallmentModel.tenant_id == loan.tenant_id,
                LoanInstallmentModel.id == alloc.installment_id
            ).with_for_update().first()
            if not inst:
                raise ValueError(f"Data corruption: Installment {alloc.installment_id} not found.")

            inst.principal_paid_cents = alloc.prev_principal_paid_cents
            inst.interest_paid_cents = alloc.prev_interest_paid_cents
            inst.amount_paid_cents = alloc.prev_amount_paid_cents
            inst.remaining_amount_cents = alloc.prev_remaining_amount_cents
            inst.paid_at = alloc.prev_paid_at

        payment.status = "REVERSED"
        payment.reversed_by_user_id = user_id
        payment.reversed_at = now_utc
        payment.reversal_reason = reason

        loan.status = payment.prev_loan_status
        loan.unpaid_principal_cents = payment.prev_unpaid_principal_cents
        loan.remaining_contractual_interest_cents = payment.prev_remaining_interest_cents
        loan.remaining_contract_total_cents = payment.prev_remaining_total_cents
        loan.amount_paid_cents = payment.prev_amount_paid_cents

        db.flush()

        installments = db.query(LoanInstallmentModel).filter(
            LoanInstallmentModel.tenant_id == loan.tenant_id,
            LoanInstallmentModel.loan_id == loan.id
        ).all()
        if db.bind and db.bind.dialect.name == "sqlite":
            LoanPaymentEngine._verify_ledger_integrity_python(loan, installments, db)
