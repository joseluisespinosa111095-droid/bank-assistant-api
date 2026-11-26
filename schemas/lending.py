from typing import Optional
from pydantic import BaseModel, Field


class LoanCreateRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=10, max_length=64)
    customer_id: int
    source_lead_id: Optional[int] = None
    product_name: str = Field(default="Personal Loan")
    principal_pesos: Optional[float] = Field(default=None, gt=0, le=10000000)
    principal_cents: Optional[int] = Field(default=None, gt=0, le=1000000000)
    monthly_interest_rate_percent: float = Field(default=10.0, ge=0, le=100.0)
    assumed_interest_months: int = Field(default=4, ge=1, le=36)
    term_installments_count: int = Field(default=16, ge=1, le=104)
    start_date: str  # YYYY-MM-DD
    first_payment_date: str  # YYYY-MM-DD
    notes: Optional[str] = Field(default=None, max_length=1000)
    interest_calculation_method: str = Field(default="FLAT_ORIGINAL_PRINCIPAL")


class LoanApproveRequest(BaseModel):
    approval_notes: Optional[str] = Field(default=None, max_length=1000)


class LoanActivateRequest(BaseModel):
    confirm_disbursement: bool = Field(...)
    disbursement_method: str = Field(...)
    disbursement_reference: str = Field(..., min_length=3, max_length=120)
    disbursed_at: Optional[str] = None  # ISO datetime
    disbursement_notes: Optional[str] = Field(default=None, max_length=1000)


class LoanCancelRequest(BaseModel):
    cancellation_reason: str = Field(..., min_length=3, max_length=255)


class LoanPaymentCreateRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=10, max_length=64)
    amount_pesos: Optional[float] = Field(default=None, gt=0)
    amount_cents: Optional[int] = Field(default=None, gt=0)
    payment_method: str = Field(...)
    payment_reference: Optional[str] = Field(default=None, max_length=120)
    paid_at: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    apply_early_payoff_rebate: bool = Field(default=False)


class LoanPaymentReverseRequest(BaseModel):
    reversal_reason: str = Field(..., min_length=3, max_length=255)


class LoanDefaultRequest(BaseModel):
    confirm_default: bool = Field(...)
    default_reason: str = Field(..., min_length=5, max_length=500)


class LoanCollectionLogRequest(BaseModel):
    interaction_type: str = Field(default="CALL")
    outcome: str = Field(default="FOLLOW_UP_REQUIRED")
    promised_amount_pesos: Optional[float] = Field(default=None, ge=0)
    next_follow_up_at: Optional[str] = None
    notes: str = Field(..., min_length=3, max_length=2000)
