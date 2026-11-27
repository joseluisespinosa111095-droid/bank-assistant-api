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
    UserModel,
)
from schemas.platform import (
    PlatformTenantCreateRequest,
    PlatformTenantModulesUpdateRequest,
    PlatformTenantUpdateRequest,
)
from services.audit import record_audit_event
from services.modules import (
    CORE_MODULE_CODE,
    LENDING_ENTITY_NAME_KEY,
    LENDING_MODULE_CODE,
    ModuleServiceError,
    ensure_tenant_sequences_for_modules,
    get_module_dependency_graph,
    get_tenant_modules_detail,
    get_tenants_modules_summary_bulk,
    list_catalog_modules_with_dependencies,
    normalize_module_code,
    set_tenant_module_state,
    validate_module_config,
    validate_module_set_for_tenant,
)
from services.serializers_core import serialize_tenant

router = APIRouter()

# --- PLATFORM SAAS TENANT ADMINISTRATION (SUPERADMIN ONLY) ---
TENANT_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@router.get("/api/platform/modules")
def platform_list_modules(
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    return list_catalog_modules_with_dependencies(db)


@router.get("/api/platform/tenants")
def platform_list_tenants(
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db),
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
    modules_by_tenant = get_tenants_modules_summary_bulk(db)
    result = []
    for t in tenants:
        item = serialize_tenant(t)
        item["users_count"] = users_counts.get(t.id, 0)
        item["customers_count"] = customers_counts.get(t.id, 0)
        item["loans_count"] = loans_counts.get(t.id, 0)
        if t.slug == PLATFORM_TENANT_SLUG:
            item["enabled_modules"] = []
            item["lending_entity_name"] = None
        else:
            mod_info = modules_by_tenant.get(t.id) or {"enabled_modules": [], "lending_entity_name": None}
            item["enabled_modules"] = mod_info["enabled_modules"]
            item["lending_entity_name"] = mod_info["lending_entity_name"]
        result.append(item)
    return result


@router.get("/api/platform/tenants/{tenant_id}")
def platform_get_tenant_detail(
    tenant_id: int,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db),
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

    modules_detail = get_tenant_modules_detail(db, tenant.id, tenant_slug=tenant.slug)
    item = serialize_tenant(tenant)
    item.update({
        "users_count": int(users_count),
        "active_users_count": int(active_users_count),
        "customers_count": int(customers_count),
        "leads_count": int(leads_count),
        "loans_count": int(loans_count),
        "enabled_modules": modules_detail["enabled_modules"],
        "lending_entity_name": modules_detail["lending_entity_name"],
        "modules": modules_detail["modules"],
    })
    return item


@router.get("/api/platform/tenants/{tenant_id}/users")
def platform_list_tenant_users(
    tenant_id: int,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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

    # Always include mandatory CORE for every commercial tenant, then validate requested modules
    requested_modules = [CORE_MODULE_CODE]
    if payload.modules:
        requested_modules.extend(payload.modules)

    try:
        validated_modules = validate_module_set_for_tenant(db, requested_modules, tenant_slug=slug_clean)
    except ModuleServiceError as exc:
        raise HTTPException(status_code=400, detail=exc.message)

    raw_lending_entity = payload.lending_entity_name
    lending_config: Optional[dict] = None
    if raw_lending_entity is not None and raw_lending_entity.strip():
        if LENDING_MODULE_CODE not in validated_modules:
            raise HTTPException(
                status_code=400,
                detail="lending_entity_name can only be configured when the LENDING module is enabled.",
            )
        try:
            lending_config = validate_module_config(
                LENDING_MODULE_CODE,
                {LENDING_ENTITY_NAME_KEY: raw_lending_entity},
            )
        except ModuleServiceError as exc:
            raise HTTPException(status_code=400, detail=exc.message)

    try:
        new_tenant = TenantModel(
            name=name_clean,
            slug=slug_clean,
            legal_name=legal_name_clean,
            is_active=True
        )
        db.add(new_tenant)
        db.flush()

        # Assign validated modules (CORE first so dependencies are satisfied in order)
        ordered_modules = [CORE_MODULE_CODE] + [m for m in validated_modules if m != CORE_MODULE_CODE]
        for mod_code in ordered_modules:
            is_lending = mod_code == LENDING_MODULE_CODE
            set_tenant_module_state(
                db,
                new_tenant.id,
                mod_code,
                is_enabled=True,
                config_json=lending_config if (is_lending and lending_config is not None) else None,
                update_config=bool(is_lending and lending_config is not None),
                updated_by_user_id=superadmin.id,
                tenant_slug=new_tenant.slug,
            )

        # Initialize ONLY sequences belonging to enabled modules (CORE -> customers/leads; LENDING -> loans/loan_payments)
        ensure_tenant_sequences_for_modules(db, new_tenant.id, ordered_modules)

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
            details={
                "slug": new_tenant.slug,
                "owner_user_id": owner_user.id,
                "enabled_modules": ",".join(validated_modules),
            },
            commit=False,
            scope="PLATFORM",
            tenant_id=None
        )
        db.commit()
        db.refresh(new_tenant)
        db.refresh(owner_user)
        modules_detail = get_tenant_modules_detail(db, new_tenant.id, tenant_slug=new_tenant.slug)
        return {
            "tenant": {
                **serialize_tenant(new_tenant),
                "enabled_modules": modules_detail["enabled_modules"],
                "lending_entity_name": modules_detail["lending_entity_name"],
            },
            "owner_admin": {
                "id": owner_user.id,
                "email": owner_user.email,
                "full_name": owner_user.full_name,
                "role": owner_user.role,
                "is_superadmin": bool(owner_user.is_superadmin)
            },
            "enabled_modules": modules_detail["enabled_modules"],
        }
    except HTTPException:
        db.rollback()
        raise
    except ModuleServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message)
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
    db: Session = Depends(get_db),
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


@router.patch("/api/platform/tenants/{tenant_id}/modules", dependencies=[Depends(verify_csrf)])
def platform_update_tenant_modules(
    tenant_id: int,
    payload: PlatformTenantModulesUpdateRequest,
    request: Request,
    superadmin: UserModel = Depends(require_superadmin),
    db: Session = Depends(get_db),
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
        if tenant.slug == PLATFORM_TENANT_SLUG:
            raise HTTPException(
                status_code=400,
                detail="Commercial modules cannot be modified on the platform tenant.",
            )

        before_detail = get_tenant_modules_detail(db, tenant.id, tenant_slug=tenant.slug)
        before_snapshot = {
            "enabled_modules": before_detail["enabled_modules"],
            "lending_entity_name": before_detail["lending_entity_name"],
        }

        seen_codes: set[str] = set()
        normalized_items: list[tuple[str, bool, bool, Optional[dict]]] = []
        for item in payload.modules:
            code = normalize_module_code(item.module_code)
            if code in seen_codes:
                raise HTTPException(
                    status_code=400,
                    detail=f"Duplicate module_code in request payload: '{code}'.",
                )
            seen_codes.add(code)

            has_cfg = "config_json" in item.model_fields_set
            has_lender = "lending_entity_name" in item.model_fields_set
            update_config = has_cfg or has_lender
            effective_cfg: Optional[dict] = None
            if update_config:
                if has_cfg and item.config_json is not None:
                    effective_cfg = dict(item.config_json)
                elif has_cfg and item.config_json is None and not has_lender:
                    effective_cfg = None
                if has_lender:
                    if code != LENDING_MODULE_CODE:
                        raise HTTPException(
                            status_code=400,
                            detail="lending_entity_name can only be configured on the LENDING module.",
                        )
                    effective_cfg = dict(effective_cfg or {})
                    effective_cfg[LENDING_ENTITY_NAME_KEY] = item.lending_entity_name

            normalized_items.append((code, bool(item.is_enabled), update_config, effective_cfg))

        # Apply enables first (CORE first), then disables (dependents before dependencies)
        requires_map, _ = get_module_dependency_graph(db)
        enables = [it for it in normalized_items if it[1]]
        disables = [it for it in normalized_items if not it[1]]

        enables.sort(key=lambda it: (0 if it[0] == CORE_MODULE_CODE else 1, len(requires_map.get(it[0], set())), it[0]))
        disables.sort(key=lambda it: (-len(requires_map.get(it[0], set())), it[0]))

        for code, is_enabled, update_config, effective_cfg in enables:
            set_tenant_module_state(
                db,
                tenant.id,
                code,
                is_enabled=True,
                config_json=effective_cfg,
                update_config=update_config,
                updated_by_user_id=superadmin.id,
                tenant_slug=tenant.slug,
            )
            ensure_tenant_sequences_for_modules(db, tenant.id, [code])

        for code, is_enabled, update_config, effective_cfg in disables:
            set_tenant_module_state(
                db,
                tenant.id,
                code,
                is_enabled=False,
                config_json=effective_cfg,
                update_config=update_config,
                updated_by_user_id=superadmin.id,
                tenant_slug=tenant.slug,
            )

        after_detail = get_tenant_modules_detail(db, tenant.id, tenant_slug=tenant.slug)
        after_snapshot = {
            "enabled_modules": after_detail["enabled_modules"],
            "lending_entity_name": after_detail["lending_entity_name"],
        }

        record_audit_event(
            db,
            "PLATFORM_TENANT_MODULES_UPDATED",
            "tenant",
            "SUCCESS",
            actor=superadmin,
            resource_id=str(tenant.id),
            ip_address=ip,
            details={
                "slug": tenant.slug,
                "before": json.dumps(before_snapshot, sort_keys=True),
                "after": json.dumps(after_snapshot, sort_keys=True),
            },
            commit=False,
            scope="PLATFORM",
            tenant_id=None,
        )
        db.commit()
        return {
            "tenant_id": tenant.id,
            "tenant_slug": tenant.slug,
            "enabled_modules": after_detail["enabled_modules"],
            "lending_entity_name": after_detail["lending_entity_name"],
            "modules": after_detail["modules"],
        }
    except HTTPException:
        db.rollback()
        raise
    except ModuleServiceError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.message)
    except Exception:
        db.rollback()
        logger.exception("Failed to update tenant modules")
        raise HTTPException(status_code=500, detail="Could not update organization modules.")
