from datetime import date
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class BrokerLenderCreateRequest(BaseModel):
    code: str = Field(..., min_length=2, max_length=40)
    name: str = Field(..., min_length=2, max_length=160)
    lender_type: str = Field(default="BANK")
    default_commission_bps: int = Field(default=0, ge=0, le=5000)
    contact_name: Optional[str] = Field(default=None, max_length=120)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(default=None, max_length=25)
    notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerLenderUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=160)
    lender_type: Optional[str] = None
    default_commission_bps: Optional[int] = Field(default=None, ge=0, le=5000)
    contact_name: Optional[str] = Field(default=None, max_length=120)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(default=None, max_length=25)
    notes: Optional[str] = Field(default=None, max_length=2000)
    is_active: Optional[bool] = None


class BrokerCaseCreateRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=64)
    customer_id: int
    source_lead_id: Optional[int] = None
    product_type: str = Field(..., min_length=2, max_length=60)
    requested_amount_pesos: Optional[float] = Field(default=None, gt=0)
    requested_amount_cents: Optional[int] = Field(default=None, gt=0)
    requested_term_months: Optional[int] = Field(default=None, ge=1, le=360)
    purpose: Optional[str] = Field(default=None, max_length=255)
    owner_user_id: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerCaseUpdateRequest(BaseModel):
    product_type: Optional[str] = Field(default=None, min_length=2, max_length=60)
    requested_amount_pesos: Optional[float] = Field(default=None, gt=0)
    requested_amount_cents: Optional[int] = Field(default=None, gt=0)
    requested_term_months: Optional[int] = Field(default=None, ge=1, le=360)
    purpose: Optional[str] = Field(default=None, max_length=255)
    owner_user_id: Optional[int] = None
    status: Optional[str] = None
    closed_reason: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerSubmissionCreateRequest(BaseModel):
    lender_id: int
    status: str = Field(default="SUBMITTED")
    external_reference: Optional[str] = Field(default=None, max_length=120)
    offered_amount_pesos: Optional[float] = Field(default=None, gt=0)
    offered_amount_cents: Optional[int] = Field(default=None, gt=0)
    offered_rate_bps: Optional[int] = Field(default=None, ge=0)
    offered_term_months: Optional[int] = Field(default=None, ge=1, le=360)
    offered_monthly_payment_pesos: Optional[float] = Field(default=None, ge=0)
    offered_monthly_payment_cents: Optional[int] = Field(default=None, ge=0)
    offered_commission_bps: Optional[int] = Field(default=None, ge=0, le=5000)
    decision_notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerSubmissionUpdateRequest(BaseModel):
    status: Optional[str] = None
    external_reference: Optional[str] = Field(default=None, max_length=120)
    offered_amount_pesos: Optional[float] = Field(default=None, gt=0)
    offered_amount_cents: Optional[int] = Field(default=None, gt=0)
    offered_rate_bps: Optional[int] = Field(default=None, ge=0)
    offered_term_months: Optional[int] = Field(default=None, ge=1, le=360)
    offered_monthly_payment_pesos: Optional[float] = Field(default=None, ge=0)
    offered_monthly_payment_cents: Optional[int] = Field(default=None, ge=0)
    offered_commission_bps: Optional[int] = Field(default=None, ge=0, le=5000)
    decision_notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerSubmissionSelectRequest(BaseModel):
    decision_notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerCaseFundRequest(BaseModel):
    funded_amount_pesos: Optional[float] = Field(default=None, gt=0)
    funded_amount_cents: Optional[int] = Field(default=None, gt=0)
    external_loan_reference: Optional[str] = Field(default=None, max_length=120)
    funded_at: Optional[str] = None
    commission_rate_bps: Optional[int] = Field(default=None, ge=0, le=5000)
    expected_pay_date: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class BrokerCommissionUpdateRequest(BaseModel):
    status: Optional[str] = None
    paid_commission_pesos: Optional[float] = Field(default=None, ge=0)
    paid_commission_cents: Optional[int] = Field(default=None, ge=0)
    expected_pay_date: Optional[date] = None
    payment_reference: Optional[str] = Field(default=None, max_length=120)
    paid_at: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
