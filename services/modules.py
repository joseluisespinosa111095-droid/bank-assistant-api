from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import PLATFORM_TENANT_SLUG
from models import (
    PlatformModuleDependencyModel,
    PlatformModuleModel,
    TenantModel,
    TenantModuleModel,
)

CORE_MODULE_CODE = "CORE"
LENDING_MODULE_CODE = "LENDING"
BROKER_MODULE_CODE = "BROKER"
LENDING_ENTITY_NAME_KEY = "lending_entity_name"
MAX_LENDING_ENTITY_NAME_LEN = 200

MODULE_SEQUENCE_ENTITIES: dict[str, tuple[str, ...]] = {
    CORE_MODULE_CODE: ("customers", "leads"),
    LENDING_MODULE_CODE: ("loans", "loan_payments"),
    BROKER_MODULE_CODE: ("broker_cases", "broker_commissions"),
}


class ModuleServiceError(Exception):
    """Base domain error for tenant module entitlement operations (HTTP-agnostic)."""

    def __init__(self, message: str, *, error_code: str = "MODULE_ERROR", details: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.details = details or {}


class ModuleNotFoundError(ModuleServiceError):
    """Raised when a requested module code does not exist in platform_modules."""

    def __init__(self, module_code: str):
        super().__init__(
            f"Unknown platform module: '{module_code}'.",
            error_code="MODULE_NOT_FOUND",
            details={"module_code": module_code},
        )


class ModuleValidationError(ModuleServiceError):
    """Raised when a module entitlement transition violates catalog or dependency rules."""


def normalize_module_code(module_code: str) -> str:
    if not isinstance(module_code, str) or not module_code.strip():
        raise ModuleValidationError(
            "Module code must be a non-empty string.",
            error_code="INVALID_MODULE_CODE",
        )
    return module_code.strip().upper()


def _resolve_tenant_slug(db: Session, tenant_id: Optional[int], tenant_slug: Optional[str] = None) -> Optional[str]:
    if tenant_slug is not None:
        return tenant_slug
    if tenant_id is None:
        return None
    row = db.query(TenantModel.slug).filter(TenantModel.id == tenant_id).first()
    return row[0] if row else None


def get_catalog_modules(db: Session, *, active_only: bool = False) -> list[PlatformModuleModel]:
    query = db.query(PlatformModuleModel)
    if active_only:
        query = query.filter(PlatformModuleModel.is_active.is_(True))
    return query.order_by(PlatformModuleModel.code.asc()).all()


def get_module_dependency_graph(db: Session) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Returns (requires_map, required_by_map) built from platform_module_dependencies."""
    requires_map: dict[str, set[str]] = {}
    required_by_map: dict[str, set[str]] = {}
    rows = db.query(
        PlatformModuleDependencyModel.module_code,
        PlatformModuleDependencyModel.required_module_code,
    ).all()
    for mod_code, req_code in rows:
        requires_map.setdefault(mod_code, set()).add(req_code)
        required_by_map.setdefault(req_code, set()).add(mod_code)
    return requires_map, required_by_map


def get_enabled_module_codes(
    db: Session,
    tenant_id: Optional[int],
    *,
    is_superadmin: bool = False,
    tenant_slug: Optional[str] = None,
) -> list[str]:
    """Returns a sorted list of active, enabled module codes for a commercial tenant.

    Always returns [] for SUPERADMIN or the platform tenant.
    """
    if is_superadmin or tenant_id is None:
        return []
    resolved_slug = _resolve_tenant_slug(db, tenant_id, tenant_slug)
    if resolved_slug is None or resolved_slug == PLATFORM_TENANT_SLUG:
        return []

    rows = (
        db.query(TenantModuleModel.module_code)
        .join(PlatformModuleModel, PlatformModuleModel.code == TenantModuleModel.module_code)
        .filter(
            TenantModuleModel.tenant_id == tenant_id,
            TenantModuleModel.is_enabled.is_(True),
            PlatformModuleModel.is_active.is_(True),
        )
        .order_by(TenantModuleModel.module_code.asc())
        .all()
    )
    return [row[0] for row in rows]


def is_module_enabled(
    db: Session,
    tenant_id: Optional[int],
    module_code: str,
    *,
    is_superadmin: bool = False,
    tenant_slug: Optional[str] = None,
) -> bool:
    """Fail-closed check: returns True only if module is active in catalog and enabled for tenant."""
    if is_superadmin or tenant_id is None:
        return False
    resolved_slug = _resolve_tenant_slug(db, tenant_id, tenant_slug)
    if resolved_slug is None or resolved_slug == PLATFORM_TENANT_SLUG:
        return False

    code = normalize_module_code(module_code)
    row = (
        db.query(TenantModuleModel.id)
        .join(PlatformModuleModel, PlatformModuleModel.code == TenantModuleModel.module_code)
        .filter(
            TenantModuleModel.tenant_id == tenant_id,
            TenantModuleModel.module_code == code,
            TenantModuleModel.is_enabled.is_(True),
            PlatformModuleModel.is_active.is_(True),
        )
        .first()
    )
    return row is not None


def get_tenant_module_row(db: Session, tenant_id: int, module_code: str) -> Optional[TenantModuleModel]:
    code = normalize_module_code(module_code)
    return (
        db.query(TenantModuleModel)
        .filter(
            TenantModuleModel.tenant_id == tenant_id,
            TenantModuleModel.module_code == code,
        )
        .first()
    )


def get_tenant_module_config(
    db: Session,
    tenant_id: Optional[int],
    module_code: str,
    *,
    require_enabled: bool = True,
) -> Optional[dict]:
    """Returns a shallow copy of config_json for a tenant module, or None."""
    if tenant_id is None:
        return None
    code = normalize_module_code(module_code)
    query = (
        db.query(TenantModuleModel)
        .join(PlatformModuleModel, PlatformModuleModel.code == TenantModuleModel.module_code)
        .filter(
            TenantModuleModel.tenant_id == tenant_id,
            TenantModuleModel.module_code == code,
        )
    )
    if require_enabled:
        query = query.filter(
            TenantModuleModel.is_enabled.is_(True),
            PlatformModuleModel.is_active.is_(True),
        )
    row = query.first()
    if not row or not isinstance(row.config_json, dict):
        return None
    return dict(row.config_json)


def get_lending_entity_name(
    db: Session,
    tenant_id: Optional[int],
    *,
    is_superadmin: bool = False,
    tenant_slug: Optional[str] = None,
) -> Optional[str]:
    """Resolves the legal lender name strictly from LENDING module config_json.

    Returns None if LENDING is disabled or lending_entity_name is unset/blank.
    NEVER falls back to tenants.legal_name or tenants.name.
    """
    if is_superadmin or tenant_id is None:
        return None
    resolved_slug = _resolve_tenant_slug(db, tenant_id, tenant_slug)
    if resolved_slug is None or resolved_slug == PLATFORM_TENANT_SLUG:
        return None

    cfg = get_tenant_module_config(db, tenant_id, LENDING_MODULE_CODE, require_enabled=True)
    if not cfg:
        return None
    raw_name = cfg.get(LENDING_ENTITY_NAME_KEY)
    if not isinstance(raw_name, str):
        return None
    cleaned = raw_name.strip()
    return cleaned or None


def validate_module_config(module_code: str, config_json: Optional[dict]) -> Optional[dict]:
    """Validates and normalizes module-specific config_json."""
    code = normalize_module_code(module_code)
    if config_json is None:
        return None
    if not isinstance(config_json, dict):
        raise ModuleValidationError(
            "config_json must be a JSON object or null.",
            error_code="INVALID_MODULE_CONFIG",
            details={"module_code": code},
        )

    normalized = dict(config_json)
    if code == LENDING_MODULE_CODE and LENDING_ENTITY_NAME_KEY in normalized:
        val = normalized[LENDING_ENTITY_NAME_KEY]
        if val is None:
            normalized[LENDING_ENTITY_NAME_KEY] = None
        elif isinstance(val, str):
            stripped = val.strip()
            if len(stripped) > MAX_LENDING_ENTITY_NAME_LEN:
                raise ModuleValidationError(
                    f"{LENDING_ENTITY_NAME_KEY} cannot exceed {MAX_LENDING_ENTITY_NAME_LEN} characters.",
                    error_code="INVALID_LENDING_ENTITY_NAME",
                    details={"module_code": code},
                )
            normalized[LENDING_ENTITY_NAME_KEY] = stripped or None
        else:
            raise ModuleValidationError(
                f"{LENDING_ENTITY_NAME_KEY} must be a string or null.",
                error_code="INVALID_LENDING_ENTITY_NAME",
                details={"module_code": code},
            )
    return normalized


def validate_module_set_for_tenant(
    db: Session,
    requested_codes: Iterable[str],
    *,
    tenant_slug: Optional[str] = None,
) -> list[str]:
    """Validates a full set of modules to assign to a commercial tenant (e.g. onboarding)."""
    if tenant_slug == PLATFORM_TENANT_SLUG:
        raise ModuleValidationError(
            "Commercial modules cannot be assigned to the platform tenant.",
            error_code="PLATFORM_TENANT_MODULES_FORBIDDEN",
        )

    normalized_set = {normalize_module_code(c) for c in requested_codes}
    catalog_rows = db.query(PlatformModuleModel).all()
    catalog_by_code = {m.code: m for m in catalog_rows}

    for code in sorted(normalized_set):
        mod = catalog_by_code.get(code)
        if not mod:
            raise ModuleNotFoundError(code)
        if not mod.is_active:
            raise ModuleValidationError(
                f"Module '{code}' is inactive and cannot be assigned.",
                error_code="MODULE_INACTIVE",
                details={"module_code": code},
            )
        if not mod.is_assignable:
            raise ModuleValidationError(
                f"Module '{code}' is not assignable in the current platform release.",
                error_code="MODULE_NOT_ASSIGNABLE",
                details={"module_code": code},
            )

    mandatory_codes = {m.code for m in catalog_rows if m.is_core_mandatory and m.is_active}
    missing_mandatory = sorted(mandatory_codes - normalized_set)
    if missing_mandatory:
        raise ModuleValidationError(
            f"Mandatory core module(s) required for commercial tenants: {', '.join(missing_mandatory)}.",
            error_code="MANDATORY_CORE_REQUIRED",
            details={"missing_mandatory": missing_mandatory},
        )

    requires_map, _ = get_module_dependency_graph(db)
    for code in sorted(normalized_set):
        missing_deps = sorted(requires_map.get(code, set()) - normalized_set)
        if missing_deps:
            raise ModuleValidationError(
                f"Module '{code}' requires active module(s): {', '.join(missing_deps)}.",
                error_code="MISSING_MODULE_DEPENDENCIES",
                details={"module_code": code, "missing_dependencies": missing_deps},
            )

    return sorted(normalized_set)


def validate_module_enable(
    db: Session,
    tenant_id: int,
    module_code: str,
    *,
    tenant_slug: Optional[str] = None,
) -> PlatformModuleModel:
    """Validates enabling a single module for a commercial tenant against catalog and dependency graph."""
    resolved_slug = _resolve_tenant_slug(db, tenant_id, tenant_slug)
    if resolved_slug == PLATFORM_TENANT_SLUG:
        raise ModuleValidationError(
            "Commercial modules cannot be enabled on the platform tenant.",
            error_code="PLATFORM_TENANT_MODULES_FORBIDDEN",
        )

    code = normalize_module_code(module_code)
    mod = db.query(PlatformModuleModel).filter(PlatformModuleModel.code == code).first()
    if not mod:
        raise ModuleNotFoundError(code)
    if not mod.is_active:
        raise ModuleValidationError(
            f"Module '{code}' is inactive and cannot be enabled.",
            error_code="MODULE_INACTIVE",
            details={"module_code": code},
        )
    if not mod.is_assignable:
        raise ModuleValidationError(
            f"Module '{code}' is not assignable in the current platform release.",
            error_code="MODULE_NOT_ASSIGNABLE",
            details={"module_code": code},
        )

    requires_map, _ = get_module_dependency_graph(db)
    required_codes = requires_map.get(code, set())
    if required_codes:
        currently_enabled = set(get_enabled_module_codes(db, tenant_id, tenant_slug=resolved_slug))
        missing = sorted(required_codes - currently_enabled)
        if missing:
            raise ModuleValidationError(
                f"Cannot enable '{code}' without required active module(s): {', '.join(missing)}.",
                error_code="MISSING_MODULE_DEPENDENCIES",
                details={"module_code": code, "missing_dependencies": missing},
            )
    return mod


def validate_module_disable(
    db: Session,
    tenant_id: int,
    module_code: str,
    *,
    tenant_slug: Optional[str] = None,
) -> PlatformModuleModel:
    """Validates disabling a single module for a commercial tenant against CORE protection and dependents."""
    resolved_slug = _resolve_tenant_slug(db, tenant_id, tenant_slug)
    if resolved_slug == PLATFORM_TENANT_SLUG:
        raise ModuleValidationError(
            "Commercial modules cannot be modified on the platform tenant.",
            error_code="PLATFORM_TENANT_MODULES_FORBIDDEN",
        )

    code = normalize_module_code(module_code)
    mod = db.query(PlatformModuleModel).filter(PlatformModuleModel.code == code).first()
    if not mod:
        raise ModuleNotFoundError(code)
    if mod.is_core_mandatory or code == CORE_MODULE_CODE:
        raise ModuleValidationError(
            f"Mandatory core module '{code}' cannot be disabled for a commercial tenant.",
            error_code="CORE_MODULE_IMMUTABLE",
            details={"module_code": code},
        )

    _, required_by_map = get_module_dependency_graph(db)
    dependents = required_by_map.get(code, set())
    if dependents:
        currently_enabled = set(get_enabled_module_codes(db, tenant_id, tenant_slug=resolved_slug))
        active_dependents = sorted(dependents & currently_enabled)
        if active_dependents:
            raise ModuleValidationError(
                f"Cannot disable '{code}' while dependent module(s) remain enabled: {', '.join(active_dependents)}.",
                error_code="ACTIVE_DEPENDENT_MODULES",
                details={"module_code": code, "active_dependents": active_dependents},
            )
    return mod


def set_tenant_module_state(
    db: Session,
    tenant_id: int,
    module_code: str,
    *,
    is_enabled: bool,
    config_json: Optional[dict] = None,
    update_config: bool = False,
    updated_by_user_id: Optional[int] = None,
    tenant_slug: Optional[str] = None,
) -> TenantModuleModel:
    """Validates and applies an enable/disable + optional config_json update for a tenant module.

    Flushes changes without committing so callers retain transaction control.
    """
    code = normalize_module_code(module_code)
    if is_enabled:
        validate_module_enable(db, tenant_id, code, tenant_slug=tenant_slug)
    else:
        validate_module_disable(db, tenant_id, code, tenant_slug=tenant_slug)

    normalized_config = validate_module_config(code, config_json) if update_config else None
    now_utc = datetime.now(timezone.utc)

    row = get_tenant_module_row(db, tenant_id, code)
    if row is None:
        row = TenantModuleModel(
            tenant_id=tenant_id,
            module_code=code,
            is_enabled=is_enabled,
            config_json=normalized_config if update_config else None,
            enabled_at=now_utc if is_enabled else None,
            disabled_at=None if is_enabled else now_utc,
            updated_by_user_id=updated_by_user_id,
            created_at=now_utc,
            updated_at=now_utc,
        )
        db.add(row)
    else:
        row.is_enabled = is_enabled
        if is_enabled:
            if row.enabled_at is None or row.disabled_at is not None:
                row.enabled_at = now_utc
            row.disabled_at = None
        else:
            row.disabled_at = now_utc
        if update_config:
            row.config_json = normalized_config
        row.updated_by_user_id = updated_by_user_id
        row.updated_at = now_utc

    db.flush()
    return row


def ensure_tenant_sequences_for_modules(
    db: Session,
    tenant_id: int,
    module_codes: Iterable[str],
) -> list[str]:
    """Idempotently initializes tenant_sequences rows for the given module codes (ON CONFLICT DO NOTHING)."""
    entities: list[str] = []
    seen: set[str] = set()
    for raw_code in module_codes:
        code = normalize_module_code(raw_code)
        for entity_type in MODULE_SEQUENCE_ENTITIES.get(code, ()):
            if entity_type not in seen:
                seen.add(entity_type)
                entities.append(entity_type)

    for entity_type in entities:
        db.execute(
            text("""
                INSERT INTO tenant_sequences (tenant_id, entity_type, last_value)
                VALUES (:tenant_id, :entity_type, 0)
                ON CONFLICT (tenant_id, entity_type) DO NOTHING
            """),
            {"tenant_id": tenant_id, "entity_type": entity_type},
        )
    return entities


def list_catalog_modules_with_dependencies(db: Session) -> list[dict]:
    """Returns all catalog modules with their dependency and dependent module code lists."""
    modules = get_catalog_modules(db, active_only=False)
    requires_map, required_by_map = get_module_dependency_graph(db)
    return [
        {
            "code": m.code,
            "name": m.name,
            "description": m.description,
            "is_core_mandatory": bool(m.is_core_mandatory),
            "is_assignable": bool(m.is_assignable),
            "is_active": bool(m.is_active),
            "dependencies": sorted(requires_map.get(m.code, set())),
            "dependents": sorted(required_by_map.get(m.code, set())),
        }
        for m in modules
    ]


def _extract_lending_entity_name_from_config(config_json: Optional[dict]) -> Optional[str]:
    if not isinstance(config_json, dict):
        return None
    raw = config_json.get(LENDING_ENTITY_NAME_KEY)
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def get_tenants_modules_summary_bulk(db: Session) -> dict[int, dict]:
    """Single-query bulk loader of enabled_modules and lending_entity_name for all tenants (no N+1)."""
    rows = (
        db.query(
            TenantModuleModel.tenant_id,
            TenantModuleModel.module_code,
            TenantModuleModel.is_enabled,
            TenantModuleModel.config_json,
        )
        .join(PlatformModuleModel, PlatformModuleModel.code == TenantModuleModel.module_code)
        .filter(
            TenantModuleModel.is_enabled.is_(True),
            PlatformModuleModel.is_active.is_(True),
        )
        .order_by(TenantModuleModel.tenant_id.asc(), TenantModuleModel.module_code.asc())
        .all()
    )
    summary: dict[int, dict] = {}
    for tenant_id, module_code, _is_enabled, config_json in rows:
        entry = summary.setdefault(
            tenant_id,
            {"enabled_modules": [], "lending_entity_name": None},
        )
        entry["enabled_modules"].append(module_code)
        if module_code == LENDING_MODULE_CODE:
            entry["lending_entity_name"] = _extract_lending_entity_name_from_config(config_json)
    return summary


def get_tenant_modules_detail(
    db: Session,
    tenant_id: int,
    *,
    tenant_slug: Optional[str] = None,
) -> dict:
    """Returns enabled_modules, lending_entity_name, and detailed module states for a single tenant."""
    resolved_slug = _resolve_tenant_slug(db, tenant_id, tenant_slug)
    if resolved_slug == PLATFORM_TENANT_SLUG:
        return {
            "enabled_modules": [],
            "lending_entity_name": None,
            "modules": [],
        }

    rows = (
        db.query(TenantModuleModel, PlatformModuleModel.is_active)
        .join(PlatformModuleModel, PlatformModuleModel.code == TenantModuleModel.module_code)
        .filter(TenantModuleModel.tenant_id == tenant_id)
        .order_by(TenantModuleModel.module_code.asc())
        .all()
    )
    enabled_modules: list[str] = []
    lending_entity_name: Optional[str] = None
    detailed: list[dict] = []
    for tm, mod_active in rows:
        is_eff_enabled = bool(tm.is_enabled) and bool(mod_active)
        if is_eff_enabled:
            enabled_modules.append(tm.module_code)
            if tm.module_code == LENDING_MODULE_CODE:
                lending_entity_name = _extract_lending_entity_name_from_config(tm.config_json)
        detailed.append(
            {
                "module_code": tm.module_code,
                "is_enabled": bool(tm.is_enabled),
                "config_json": dict(tm.config_json) if isinstance(tm.config_json, dict) else None,
                "enabled_at": tm.enabled_at.isoformat() if tm.enabled_at else None,
                "disabled_at": tm.disabled_at.isoformat() if tm.disabled_at else None,
                "updated_at": tm.updated_at.isoformat() if tm.updated_at else None,
            }
        )
    return {
        "enabled_modules": enabled_modules,
        "lending_entity_name": lending_entity_name,
        "modules": detailed,
    }
