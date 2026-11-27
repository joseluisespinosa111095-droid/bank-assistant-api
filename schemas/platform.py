from typing import Any, Optional
from pydantic import BaseModel, EmailStr, Field


class PlatformTenantCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=160)
    slug: str = Field(..., min_length=2, max_length=80)
    legal_name: Optional[str] = Field(default=None, max_length=200)
    owner_email: EmailStr
    owner_password: str = Field(..., min_length=8, max_length=128)
    owner_full_name: Optional[str] = Field(default=None, max_length=120)
    modules: Optional[list[str]] = Field(default=None)
    lending_entity_name: Optional[str] = Field(default=None, max_length=200)


class PlatformTenantUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=160)
    legal_name: Optional[str] = Field(default=None, max_length=200)
    is_active: Optional[bool] = None


class PlatformTenantModuleItemUpdate(BaseModel):
    module_code: str = Field(..., min_length=1, max_length=64)
    is_enabled: bool
    config_json: Optional[dict[str, Any]] = None
    lending_entity_name: Optional[str] = Field(default=None, max_length=200)


class PlatformTenantModulesUpdateRequest(BaseModel):
    modules: list[PlatformTenantModuleItemUpdate] = Field(..., min_length=1)
