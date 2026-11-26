"""One-shot CLI script to create the initial platform SUPERADMIN user.

Reads credentials strictly from environment variables:
  - DATABASE_URL (required; resolved via db_config.get_runtime_database_url)
  - SUPERADMIN_EMAIL (required)
  - SUPERADMIN_PASSWORD (required, min 8 chars)
  - SUPERADMIN_FULL_NAME (optional, defaults to 'NURA Platform Superadmin')

Aborts with a non-zero exit code without modifying the database if:
  - Any required environment variable is missing or invalid.
  - The 'platform' tenant does not exist.
  - Any user with is_superadmin = True already exists.
  - The target email is already registered.
"""
import json
import os
import sys

# Ensure repository root is importable when executed as `python scripts/bootstrap_superadmin.py`
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from passlib.context import CryptContext
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from db_config import get_engine_connect_args, get_runtime_database_url, is_sqlite_url
from models import AuditLogModel, TenantModel, UserModel


PLATFORM_TENANT_SLUG = "platform"


def main() -> int:
    raw_email = (os.getenv("SUPERADMIN_EMAIL") or "").strip().lower()
    raw_password = os.getenv("SUPERADMIN_PASSWORD") or ""
    full_name = (os.getenv("SUPERADMIN_FULL_NAME") or "NURA Platform Superadmin").strip()

    if not raw_email or "@" not in raw_email or "." not in raw_email.split("@", 1)[-1]:
        print("ERROR: SUPERADMIN_EMAIL environment variable is missing or invalid.", file=sys.stderr)
        return 1

    if len(raw_password.strip()) < 8:
        print("ERROR: SUPERADMIN_PASSWORD environment variable must be at least 8 characters.", file=sys.stderr)
        return 1

    try:
        database_url = get_runtime_database_url()
    except Exception as exc:
        print(f"ERROR: Invalid database configuration: {exc}", file=sys.stderr)
        return 1

    engine = create_engine(database_url, connect_args=get_engine_connect_args(database_url))
    if is_sqlite_url(database_url):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

    db = SessionLocal()
    try:
        platform_tenant = (
            db.query(TenantModel)
            .filter(TenantModel.slug == PLATFORM_TENANT_SLUG)
            .first()
        )
        if not platform_tenant:
            print(
                "ERROR: Platform tenant ('platform') does not exist. Run 'alembic upgrade head' first.",
                file=sys.stderr,
            )
            return 1

        existing_superadmin = (
            db.query(UserModel.id)
            .filter(UserModel.is_superadmin.is_(True))
            .first()
        )
        if existing_superadmin is not None:
            print(
                "ERROR: A SUPERADMIN user already exists. Bootstrap can only be executed once.",
                file=sys.stderr,
            )
            return 1

        existing_email = (
            db.query(UserModel.id)
            .filter(UserModel.email == raw_email)
            .first()
        )
        if existing_email is not None:
            print(
                f"ERROR: Email '{raw_email}' is already registered.",
                file=sys.stderr,
            )
            return 1

        superadmin = UserModel(
            tenant_id=platform_tenant.id,
            email=raw_email,
            full_name=full_name or "NURA Platform Superadmin",
            password_hash=pwd_context.hash(raw_password),
            role="OWNER_ADMIN",
            is_superadmin=True,
            is_active=True,
        )
        db.add(superadmin)
        db.flush()

        audit_entry = AuditLogModel(
            tenant_id=None,
            scope="PLATFORM",
            actor_user_id=superadmin.id,
            actor_role=superadmin.role,
            event_type="PLATFORM_SUPERADMIN_BOOTSTRAPPED",
            resource_type="user",
            resource_id=str(superadmin.id),
            outcome="SUCCESS",
            ip_address=None,
            details_json=json.dumps({"tenant_slug": PLATFORM_TENANT_SLUG}),
        )
        db.add(audit_entry)
        db.commit()
        print(
            f"SUCCESS: Platform SUPERADMIN created (id={superadmin.id}, email={superadmin.email}, tenant={PLATFORM_TENANT_SLUG})."
        )
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERROR: Failed to bootstrap SUPERADMIN: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
