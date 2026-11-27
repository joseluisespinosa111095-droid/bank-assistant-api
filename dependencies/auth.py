import os
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import Cookie, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from core.config import PLATFORM_TENANT_SLUG
from core.security import get_client_ip, hash_token
from dependencies.db import get_db
from models import SessionModel, TenantModel, UserModel
from services.audit import record_audit_event
from services.modules import is_module_enabled, normalize_module_code


# --- CSRF PROTECTION WITH AUDIT ---
def verify_csrf(request: Request, db: Session = Depends(get_db)):
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        ip = get_client_ip(request)
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        allowed_origins = [o.strip().rstrip('/') for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
        is_dev = (
            os.getenv("ENVIRONMENT", "development").lower() != "production"
            and os.getenv("RENDER", "false").lower() != "true"
        )
        if is_dev:
            allowed_origins.extend([
                "http://localhost", "http://127.0.0.1",
                "http://localhost:8000", "http://127.0.0.1:8000"
            ])

        valid_origin = False
        if origin:
            if origin.rstrip('/') in allowed_origins:
                valid_origin = True
        elif referer:
            parsed = urlparse(referer)
            base_referer = f"{parsed.scheme}://{parsed.netloc}"
            if base_referer in allowed_origins:
                valid_origin = True

        if not valid_origin:
            record_audit_event(
                db, "CSRF_ORIGIN_DENIED", "security", "DENIED",
                resource_id=request.url.path, ip_address=ip,
                details={"origin": origin or referer or "missing"}, commit=True
            )
            raise HTTPException(status_code=403, detail="CSRF: Unauthorized origin.")

        csrf_cookie = request.cookies.get("csrf_token")
        csrf_header = request.headers.get("X-CSRF-Token")

        if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
            record_audit_event(
                db, "CSRF_TOKEN_INVALID", "security", "DENIED",
                resource_id=request.url.path, ip_address=ip, commit=True
            )
            raise HTTPException(status_code=403, detail="CSRF: Missing or invalid token.")


# --- AUTHENTICATION & RBAC DEPENDENCIES ---


def resolve_tenant_owner_user_id(
    db: Session,
    tenant_id: int,
    requested_owner_id: Optional[int],
    fallback_user_id: int
) -> int:
    if requested_owner_id is None:
        return fallback_user_id
    owner = (
        db.query(UserModel.id)
        .filter(UserModel.id == requested_owner_id, UserModel.tenant_id == tenant_id)
        .first()
    )
    if not owner:
        raise HTTPException(status_code=404, detail="Assigned owner user not found in this organization.")
    return requested_owner_id


def get_current_user(
    request: Request,
    response: Response,
    session_token: Optional[str] = Cookie(None, alias="session_token"),
    db: Session = Depends(get_db)
) -> UserModel:
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token_hash = hash_token(session_token)
    db_session = db.query(SessionModel).filter(SessionModel.token == token_hash).first()

    if not db_session or db_session.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        if db_session:
            db.delete(db_session)
            db.commit()
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    user = db.query(UserModel).filter(UserModel.id == db_session.user_id).first()
    if not user:
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        raise HTTPException(status_code=401, detail="User not found")

    if not user.is_active:
        db.query(SessionModel).filter(SessionModel.user_id == user.id).delete()
        db.commit()
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        record_audit_event(
            db, "AUTH_INACTIVE_USER_BLOCKED", "auth", "DENIED",
            actor=user, resource_id=str(user.id), ip_address=get_client_ip(request), commit=True
        )
        raise HTTPException(status_code=403, detail="Account has been deactivated.")

    tenant = db.query(TenantModel).filter(TenantModel.id == user.tenant_id).first()
    if not tenant or not tenant.is_active:
        db.query(SessionModel).filter(SessionModel.user_id == user.id).delete()
        db.commit()
        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")
        record_audit_event(
            db, "AUTH_TENANT_SUSPENDED_BLOCKED", "auth", "DENIED",
            actor=user, resource_id=str(user.tenant_id), ip_address=get_client_ip(request), commit=True
        )
        raise HTTPException(status_code=403, detail="Organization account is inactive or suspended.")

    return user


def require_commercial_tenant(
    request: Request,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> UserModel:
    tenant = db.query(TenantModel).filter(TenantModel.id == user.tenant_id).first()
    if user.is_superadmin or not tenant or tenant.slug == PLATFORM_TENANT_SLUG:
        record_audit_event(
            db,
            "COMMERCIAL_ACCESS_DENIED",
            "commercial",
            "DENIED",
            actor=user,
            resource_id=request.url.path,
            ip_address=get_client_ip(request),
            details={
                "is_superadmin": bool(user.is_superadmin),
                "tenant_slug": tenant.slug if tenant else "unknown"
            },
            commit=True,
            scope="PLATFORM",
            tenant_id=None
        )
        raise HTTPException(
            status_code=403,
            detail="Access denied: platform accounts cannot access commercial tenant routes."
        )
    return user


def require_roles(*allowed_roles: str):
    def role_checker(
        request: Request,
        user: UserModel = Depends(require_commercial_tenant),
        db: Session = Depends(get_db)
    ) -> UserModel:
        if user.role not in allowed_roles:
            record_audit_event(
                db, "RBAC_ACCESS_DENIED", "endpoint", "DENIED",
                actor=user, resource_id=request.url.path, ip_address=get_client_ip(request),
                details={"required": ",".join(allowed_roles), "actual": user.role}, commit=True
            )
            raise HTTPException(status_code=403, detail="Access denied: insufficient permissions.")
        return user
    return role_checker


def require_superadmin(
    request: Request,
    user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> UserModel:
    tenant = db.query(TenantModel).filter(TenantModel.id == user.tenant_id).first()
    if not user.is_superadmin or not tenant or tenant.slug != PLATFORM_TENANT_SLUG:
        record_audit_event(
            db, "PLATFORM_ACCESS_DENIED", "platform", "DENIED",
            actor=user, resource_id=request.url.path, ip_address=get_client_ip(request),
            details={"is_superadmin": bool(user.is_superadmin), "tenant_slug": tenant.slug if tenant else "unknown"},
            commit=True,
            scope="PLATFORM",
            tenant_id=None
        )
        raise HTTPException(status_code=403, detail="Access denied: platform superadmin privileges required.")
    return user


def require_module(module_code: str):
    normalized_code = normalize_module_code(module_code)

    def module_checker(
        request: Request,
        user: UserModel = Depends(require_commercial_tenant),
        db: Session = Depends(get_db),
    ) -> UserModel:
        if not is_module_enabled(db, user.tenant_id, normalized_code):
            record_audit_event(
                db,
                "MODULE_ACCESS_DENIED",
                "module",
                "DENIED",
                actor=user,
                resource_id=normalized_code,
                ip_address=get_client_ip(request),
                details={
                    "module_code": normalized_code,
                    "path": request.url.path,
                    "method": request.method,
                    "tenant_id": user.tenant_id,
                    "user_id": user.id,
                },
                commit=True,
                scope="TENANT",
                tenant_id=user.tenant_id,
            )
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: module '{normalized_code}' is not enabled for this organization.",
            )
        return user

    return module_checker
