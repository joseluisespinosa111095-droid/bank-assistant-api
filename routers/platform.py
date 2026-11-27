import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.config import PLATFORM_TENANT_SLUG, logger, pwd_context
from core.security import get_client_ip
from dependencies.auth import require_superadmin, verify_csrf
from dependencies.db import get_db
from models import (
    AuditLogModel,
    CustomerModel,
    LeadModel,
    LoanModel,
    SessionModel,
    TenantModel,
    TenantSequenceModel,
    UserModel,
)
from schemas.platform import PlatformTenantCreateRequest, PlatformTenantUpdateRequest
from services.audit import record_audit_event
from services.serializers_core import serialize_tenant

router = APIRouter()

# --- PLATFORM SAAS TENANT ADMINISTRATION (SUPERADMIN ONLY) ---
TENANT_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TENANT_SEQUENCE_ENTITIES = ("customers", "leads", "loans", "loan_payments")


@router.get("/api/platform/tenants")
def platform_list_tenants(
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db)
):
    tenants = db.query(TenantModel).order_by(TenantModel.id.asc()).all()
    users_counts = {
        tid: int(cnt)
        for tid, cnt in db.query(UserModel.tenant_id, func.count(UserModel.id))
        .group_by(UserModel.tenant_id)
        .all()
    }
    customers_counts = {
        tid: int(cnt)
        for tid, cnt in db.query(CustomerModel.tenant_id, func.count(CustomerModel.id))
        .group_by(CustomerModel.tenant_id)
        .all()
    }
    loans_counts = {
        tid: int(cnt)
        for tid, cnt in db.query(LoanModel.tenant_id, func.count(LoanModel.id))
        .group_by(LoanModel.tenant_id)
        .all()
    }
    result = []
    for t in tenants:
        item = serialize_tenant(t)
        item["users_count"] = users_counts.get(t.id, 0)
        item["customers_count"] = customers_counts.get(t.id, 0)
        item["loans_count"] = loans_counts.get(t.id, 0)
        result.append(item)
    return result


@router.get("/api/platform/tenants/{tenant_id}")
def platform_get_tenant_detail(
    tenant_id: int,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db)
):
    tenant = db.query(TenantModel).filter(TenantModel.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    users_count = (
        db.query(func.count(UserModel.id))
        .filter(UserModel.tenant_id == tenant.id)
        .scalar()
        or 0
    )
    active_users_count = (
        db.query(func.count(UserModel.id))
        .filter(UserModel.tenant_id == tenant.id, UserModel.is_active.is_(True))
        .scalar()
        or 0
    )
    customers_count = (
        db.query(func.count(CustomerModel.id))
        .filter(CustomerModel.tenant_id == tenant.id)
        .scalar()
        or 0
    )
    leads_count = (
        db.query(func.count(LeadModel.id))
        .filter(LeadModel.tenant_id == tenant.id)
        .scalar()
        or 0
    )
    loans_count = (
        db.query(func.count(LoanModel.id))
        .filter(LoanModel.tenant_id == tenant.id)
        .scalar()
        or 0
    )

    item = serialize_tenant(tenant)
    item.update({
        "users_count": int(users_count),
        "active_users_count": int(active_users_count),
        "customers_count": int(customers_count),
        "leads_count": int(leads_count),
        "loans_count": int(loans_count),
    })
    return item


@router.get("/api/platform/tenants/{tenant_id}/users")
def platform_list_tenant_users(
    tenant_id: int,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db)
):
    tenant_exists = db.query(TenantModel.id).filter(TenantModel.id == tenant_id).first()
    if not tenant_exists:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    rows = (
        db.query(
            UserModel.id,
            UserModel.email,
            UserModel.full_name,
            UserModel.role,
            UserModel.is_active,
            UserModel.created_at,
        )
        .filter(UserModel.tenant_id == tenant_id)
        .order_by(UserModel.id.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "email": row.email,
            "full_name": row.full_name,
            "role": row.role,
            "is_active": bool(row.is_active),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@router.get("/api/platform/audit-logs")
def platform_list_audit_logs(
    event_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db)
):
    query = db.query(AuditLogModel).filter(AuditLogModel.scope == "PLATFORM")
    if event_type is not None and event_type.strip():
        query = query.filter(AuditLogModel.event_type == event_type.strip())
    if resource_id is not None and resource_id.strip():
        query = query.filter(AuditLogModel.resource_id == resource_id.strip())

    logs = (
        query.order_by(AuditLogModel.created_at.desc(), AuditLogModel.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "scope": l.scope,
            "created_at": l.created_at.isoformat() if l.created_at else None,
            "actor_user_id": l.actor_user_id,
            "actor_role": l.actor_role,
            "event_type": l.event_type,
            "resource_type": l.resource_type,
            "resource_id": l.resource_id,
            "outcome": l.outcome,
            "ip_address": l.ip_address,
            "details": json.loads(l.details_json) if l.details_json else None,
        }
        for l in logs
    ]


@router.post("/api/platform/tenants", dependencies=[Depends(verify_csrf)])
def platform_create_tenant(
    payload: PlatformTenantCreateRequest,
    request: Request,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    name_clean = payload.name.strip()
    slug_clean = payload.slug.strip().lower()
    legal_name_clean = payload.legal_name.strip() if payload.legal_name and payload.legal_name.strip() else None
    owner_email_clean = payload.owner_email.strip().lower()
    owner_full_name_clean = (
        payload.owner_full_name.strip() if payload.owner_full_name and payload.owner_full_name.strip() else None
    )

    if len(name_clean) < 2:
        raise HTTPException(status_code=422, detail="Organization name must be at least 2 characters.")
    if not TENANT_SLUG_RE.match(slug_clean):
        raise HTTPException(
            status_code=422,
            detail="Invalid slug. Use lowercase alphanumeric characters and hyphens only (e.g. 'acme-credit')."
        )
    if len(payload.owner_password.strip()) < 8:
        raise HTTPException(status_code=422, detail="Owner password must be at least 8 characters long.")

    if db.query(TenantModel.id).filter(TenantModel.slug == slug_clean).first():
        raise HTTPException(status_code=409, detail=f"Tenant slug '{slug_clean}' is already in use.")

    if db.query(UserModel.id).filter(UserModel.email == owner_email_clean).first():
        raise HTTPException(status_code=409, detail="Owner email is already registered.")

    try:
        new_tenant = TenantModel(
            name=name_clean,
            slug=slug_clean,
            legal_name=legal_name_clean,
            is_active=True
        )
        db.add(new_tenant)
        db.flush()

        for entity_type in TENANT_SEQUENCE_ENTITIES:
            db.add(TenantSequenceModel(
                tenant_id=new_tenant.id,
                entity_type=entity_type,
                last_value=0
            ))

        owner_user = UserModel(
            tenant_id=new_tenant.id,
            email=owner_email_clean,
            full_name=owner_full_name_clean,
            password_hash=pwd_context.hash(payload.owner_password),
            role="OWNER_ADMIN",
            is_superadmin=False,
            is_active=True
        )
        db.add(owner_user)
        db.flush()

        record_audit_event(
            db,
            "PLATFORM_TENANT_CREATED",
            "tenant",
            "SUCCESS",
            actor=superadmin,
            resource_id=str(new_tenant.id),
            ip_address=ip,
            details={"slug": new_tenant.slug, "owner_user_id": owner_user.id},
            commit=False,
            scope="PLATFORM",
            tenant_id=None
        )
        db.commit()
        db.refresh(new_tenant)
        db.refresh(owner_user)
        return {
            "tenant": serialize_tenant(new_tenant),
            "owner_admin": {
                "id": owner_user.id,
                "email": owner_user.email,
                "full_name": owner_user.full_name,
                "role": owner_user.role,
                "is_superadmin": bool(owner_user.is_superadmin)
            }
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tenant slug or owner email already exists.")
    except Exception:
        db.rollback()
        logger.exception("Failed to create platform tenant")
        raise HTTPException(status_code=500, detail="Could not create organization.")


@router.patch("/api/platform/tenants/{tenant_id}", dependencies=[Depends(verify_csrf)])
def platform_update_tenant(
    tenant_id: int,
    payload: PlatformTenantUpdateRequest,
    request: Request,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        tenant = (
            db.query(TenantModel)
            .filter(TenantModel.id == tenant_id)
            .with_for_update()
            .first()
        )
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        if payload.is_active is False and tenant.slug == PLATFORM_TENANT_SLUG:
            raise HTTPException(status_code=400, detail="Cannot suspend the platform tenant.")

        updated_fields = []
        if payload.name is not None:
            name_clean = payload.name.strip()
            if len(name_clean) < 2:
                raise HTTPException(status_code=422, detail="Organization name must be at least 2 characters.")
            tenant.name = name_clean
            updated_fields.append("name")

        if payload.legal_name is not None:
            tenant.legal_name = payload.legal_name.strip() or None
            updated_fields.append("legal_name")

        sessions_revoked = 0
        if payload.is_active is not None:
            tenant.is_active = bool(payload.is_active)
            updated_fields.append("is_active")
            if not tenant.is_active:
                tenant_user_ids = [
                    row[0] for row in db.query(UserModel.id).filter(UserModel.tenant_id == tenant.id).all()
                ]
                if tenant_user_ids:
                    sessions_revoked = (
                        db.query(SessionModel)
                        .filter(SessionModel.user_id.in_(tenant_user_ids))
                        .delete(synchronize_session=False)
                    )

        record_audit_event(
            db,
            "PLATFORM_TENANT_UPDATED",
            "tenant",
            "SUCCESS",
            actor=superadmin,
            resource_id=str(tenant.id),
            ip_address=ip,
            details={
                "slug": tenant.slug,
                "is_active": tenant.is_active,
                "updated_fields": ",".join(updated_fields),
                "sessions_revoked": sessions_revoked
            },
            commit=False,
            scope="PLATFORM",
            tenant_id=None
        )
        db.commit()
        db.refresh(tenant)
        return serialize_tenant(tenant)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to update platform tenant")
        raise HTTPException(status_code=500, detail="Could not update organization.")
