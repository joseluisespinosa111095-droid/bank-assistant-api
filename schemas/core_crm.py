from datetime import date
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class ChatRequest(BaseModel):
    mensaje: str = Field(..., min_length=1, max_length=1500)
    conversation_id: Optional[int] = None


class CustomerCreateRequest(BaseModel):
    entity_type: str = Field(default="INDIVIDUAL")
    status: str = Field(default="ACTIVE")
    verification_status: str = Field(default="PENDING_VERIFICATION")
    # INDIVIDUAL: first_name y last_name obligatorios, middle_name opcional.  BUSINESS: full_legal_name (razon social) obligatorio.
    first_name: Optional[str] = Field(default=None, max_length=50)
    middle_name: Optional[str] = Field(default=None, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=75)
    full_legal_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    date_of_birth: Optional[date] = None
    phone_primary: str = Field(..., min_length=10, max_length=25)
    phone_secondary: Optional[str] = Field(default=None, max_length=25)
    email: Optional[EmailStr] = None
    address_full: Optional[str] = Field(default=None, max_length=1000)
    employment_status: Optional[str] = Field(default=None, max_length=60)
    employer_or_business_name: Optional[str] = Field(default=None, max_length=160)
    occupation_or_industry: Optional[str] = Field(default=None, max_length=120)
    employment_address: Optional[str] = Field(default=None, max_length=1000)
    owner_user_id: Optional[int] = None


class CustomerUpdateRequest(BaseModel):
    entity_type: Optional[str] = None
    status: Optional[str] = None
    verification_status: Optional[str] = None
    first_name: Optional[str] = Field(default=None, max_length=50)
    middle_name: Optional[str] = Field(default=None, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=75)
    full_legal_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    date_of_birth: Optional[date] = None
    phone_primary: Optional[str] = Field(default=None, min_length=10, max_length=25)
    phone_secondary: Optional[str] = Field(default=None, max_length=25)
    email: Optional[EmailStr] = None
    address_full: Optional[str] = Field(default=None, max_length=1000)
    employment_status: Optional[str] = Field(default=None, max_length=60)
    employer_or_business_name: Optional[str] = Field(default=None, max_length=160)
    occupation_or_industry: Optional[str] = Field(default=None, max_length=120)
    employment_address: Optional[str] = Field(default=None, max_length=1000)
    owner_user_id: Optional[int] = None


class LeadCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=180)
    entity_type: str = Field(default="INDIVIDUAL")
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    phone_primary: str = Field(..., min_length=10, max_length=25)
    email: Optional[EmailStr] = None
    source: str = Field(default="DIRECT_OUTREACH")
    product_interest: str = Field(default="Personal Loan")
    requested_amount_pesos: Optional[float] = Field(default=None, ge=0)
    requested_amount_cents: Optional[int] = Field(default=None, ge=0)
    stage: str = Field(default="NEW")
    priority: str = Field(default="MEDIUM")
    next_follow_up_at: Optional[str] = None
    summary_notes: Optional[str] = Field(default=None, max_length=2000)
    owner_user_id: Optional[int] = None


class LeadUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    entity_type: Optional[str] = None
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    phone_primary: Optional[str] = Field(default=None, min_length=10, max_length=25)
    email: Optional[EmailStr] = None
    source: Optional[str] = None
    product_interest: Optional[str] = None
    requested_amount_pesos: Optional[float] = Field(default=None, ge=0)
    requested_amount_cents: Optional[int] = Field(default=None, ge=0)
    stage: Optional[str] = None
    priority: Optional[str] = None
    lost_reason: Optional[str] = Field(default=None, max_length=160)
    next_follow_up_at: Optional[str] = None
    clear_next_follow_up: Optional[bool] = False
    summary_notes: Optional[str] = Field(default=None, max_length=2000)
    stage_change_note: Optional[str] = Field(default=None, max_length=500)
    owner_user_id: Optional[int] = None


class InteractionCreateRequest(BaseModel):
    interaction_type: str = Field(default="CALL")
    direction: str = Field(default="OUTBOUND")
    visibility: str = Field(default="INTERNAL")
    outcome: Optional[str] = None
    content: str = Field(..., min_length=2, max_length=2500)
    next_follow_up_at: Optional[str] = None
    clear_next_follow_up: Optional[bool] = False


class LeadConvertRequest(BaseModel):
    entity_type: Optional[str] = None
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    address_full: Optional[str] = Field(default=None, max_length=1000)
    notes: Optional[str] = Field(default=None, max_length=500)
