from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: str = Field(default="SALES_AGENT")  # minimo privilegio por defecto


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=128)


class UserUpdateByAdmin(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserLanguageUpdateRequest(BaseModel):
    preferred_language: str = Field(..., min_length=2, max_length=10)


class UserChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class AuthLoginResponse(BaseModel):
    message: str
    email: str
    full_name: Optional[str] = None
    role: str
    tenant_id: int
    tenant_slug: str
    tenant_name: str
    is_superadmin: bool
    preferred_language: str
    enabled_modules: list[str]
    lending_entity_name: Optional[str] = None


class AuthMeResponse(BaseModel):
    id: int
    tenant_id: int
    tenant_slug: Optional[str] = None
    tenant_name: Optional[str] = None
    email: str
    full_name: Optional[str] = None
    role: str
    is_superadmin: bool
    preferred_language: str
    is_active: bool
    supported_products: list[str]
    enabled_modules: list[str]
    lending_entity_name: Optional[str] = None
