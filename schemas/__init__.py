from schemas.auth import (
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    UserChangePasswordRequest,
    UserLanguageUpdateRequest,
    UserLogin,
    UserRegister,
    UserUpdateByAdmin,
)
from schemas.core_crm import (
    ChatRequest,
    CustomerCreateRequest,
    CustomerUpdateRequest,
    InteractionCreateRequest,
    LeadConvertRequest,
    LeadCreateRequest,
    LeadUpdateRequest,
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
from schemas.platform import (
    PlatformTenantCreateRequest,
    PlatformTenantUpdateRequest,
)

__all__ = [
    "UserRegister",
    "UserLogin",
    "PasswordResetRequest",
    "PasswordResetConfirmRequest",
    "UserUpdateByAdmin",
    "UserLanguageUpdateRequest",
    "UserChangePasswordRequest",
    "PlatformTenantCreateRequest",
    "PlatformTenantUpdateRequest",
    "ChatRequest",
    "CustomerCreateRequest",
    "CustomerUpdateRequest",
    "LeadCreateRequest",
    "LeadUpdateRequest",
    "InteractionCreateRequest",
    "LeadConvertRequest",
    "LoanCreateRequest",
    "LoanApproveRequest",
    "LoanActivateRequest",
    "LoanCancelRequest",
    "LoanPaymentCreateRequest",
    "LoanPaymentReverseRequest",
    "LoanDefaultRequest",
    "LoanCollectionLogRequest",
]
