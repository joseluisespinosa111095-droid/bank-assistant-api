"""Automated migration & schema tests for Entregable 5A — Bloque 1 (Migration 013).

Covers:
1. Clean DB upgrade (`alembic upgrade head` -> revision 013):
   - Catalog seed of all 5 modules (CORE, LENDING assignable; BROKER, COMMERCE, FINANCING not assignable).
   - Dependency graph seed (5 directed edges).
   - Backfill: `default-org` gets CORE + LENDING enabled; `platform` gets 0 rows.
2. Upgrade from revision 012 with multiple seeded commercial tenants + platform:
   - Deterministic backfill assigns CORE + LENDING to all commercial tenants (`slug <> 'platform'`).
   - `platform` receives 0 rows in `tenant_modules`.
   - Existing tenant/user data preserved untouched.
3. Clean `alembic downgrade 012` and idempotent re-upgrade `012 -> 013`.
4. Database constraint enforcement:
   - Composite PK on `platform_module_dependencies` (duplicate edge rejected).
   - CHECK `module_code <> required_module_code` (self-dependency rejected).
   - FK on `platform_module_dependencies` (unknown module rejected).
   - UNIQUE `(tenant_id, module_code)` on `tenant_modules`.
   - FKs on `tenant_modules` (`tenant_id`, `module_code`, `updated_by_user_id`).
5. ORM round-trip for PlatformModuleModel, PlatformModuleDependencyModel, and TenantModuleModel
   (including `config_json` storage and cross-tenant `updated_by_user_id` from SUPERADMIN).
6. Offline PostgreSQL SQL compilation (`alembic upgrade 012:013 --sql`) emits JSONB and ON CONFLICT.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from models import (
    PlatformModuleDependencyModel,
    PlatformModuleModel,
    TenantModel,
    TenantModuleModel,
    UserModel,
)


def _clean_env(db_url: str) -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "RENDER", "ENVIRONMENT")
    }
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["RENDER"] = "false"
    env["ENVIRONMENT"] = "development"
    env["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"
    env["MIGRATION_DATABASE_URL"] = db_url
    env["DATABASE_URL"] = db_url
    return env


def _run_alembic(args: list[str], db_url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=_clean_env(db_url),
        capture_output=True,
        text=True,
    )


def _connect_sqlite(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def test_01_clean_upgrade_head_seeds_catalog_dependencies_and_backfill(tmp_path: Path):
    db_file = tmp_path / "clean_013.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    res = _run_alembic(["upgrade", "head"], db_url)
    assert res.returncode == 0, f"alembic upgrade head failed:\n{res.stdout}\n{res.stderr}"

    conn = _connect_sqlite(db_file)
    try:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert rev == ("013",)

        # 1. Verify 5 catalog modules and assignability flags
        modules = {
            row[0]: {
                "name": row[1],
                "is_core_mandatory": bool(row[2]),
                "is_assignable": bool(row[3]),
                "is_active": bool(row[4]),
            }
            for row in conn.execute(
                "SELECT code, name, is_core_mandatory, is_assignable, is_active FROM platform_modules"
            ).fetchall()
        }
        assert set(modules.keys()) == {"CORE", "LENDING", "BROKER", "COMMERCE", "FINANCING"}
        assert modules["CORE"]["is_core_mandatory"] is True
        assert modules["CORE"]["is_assignable"] is True
        assert modules["LENDING"]["is_core_mandatory"] is False
        assert modules["LENDING"]["is_assignable"] is True
        for unavailable in ("BROKER", "COMMERCE", "FINANCING"):
            assert modules[unavailable]["is_core_mandatory"] is False
            assert modules[unavailable]["is_assignable"] is False
            assert modules[unavailable]["is_active"] is True

        # 2. Verify 5 dependency edges
        deps = set(
            conn.execute(
                "SELECT module_code, required_module_code FROM platform_module_dependencies"
            ).fetchall()
        )
        assert deps == {
            ("LENDING", "CORE"),
            ("BROKER", "CORE"),
            ("COMMERCE", "CORE"),
            ("FINANCING", "CORE"),
            ("FINANCING", "COMMERCE"),
        }

        # 3. Verify default-org gets CORE + LENDING enabled; platform gets 0 rows
        default_id = conn.execute("SELECT id FROM tenants WHERE slug = 'default-org'").fetchone()[0]
        platform_id = conn.execute("SELECT id FROM tenants WHERE slug = 'platform'").fetchone()[0]

        default_mods = {
            row[0]: (bool(row[1]), row[2], row[3], row[4])
            for row in conn.execute(
                "SELECT module_code, is_enabled, config_json, enabled_at, disabled_at "
                "FROM tenant_modules WHERE tenant_id = ?",
                (default_id,),
            ).fetchall()
        }
        assert set(default_mods.keys()) == {"CORE", "LENDING"}
        for code in ("CORE", "LENDING"):
            is_enabled, cfg, enabled_at, disabled_at = default_mods[code]
            assert is_enabled is True
            assert cfg is None
            assert enabled_at is not None
            assert disabled_at is None

        platform_count = conn.execute(
            "SELECT COUNT(*) FROM tenant_modules WHERE tenant_id = ?",
            (platform_id,),
        ).fetchone()[0]
        assert platform_count == 0
    finally:
        conn.close()


def test_02_upgrade_from_012_backfills_all_commercial_tenants_and_downgrade_reupgrade(tmp_path: Path):
    db_file = tmp_path / "from_012.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    r012 = _run_alembic(["upgrade", "012"], db_url)
    assert r012.returncode == 0, f"alembic upgrade 012 failed:\n{r012.stdout}\n{r012.stderr}"

    # Seed an additional commercial tenant and user at revision 012
    conn = _connect_sqlite(db_file)
    try:
        conn.execute(
            "INSERT INTO tenants (name, slug, legal_name, is_active, created_at) "
            "VALUES ('Alpha Credit', 'alpha-credit', 'Alpha Credit SAPI', 1, CURRENT_TIMESTAMP)"
        )
        alpha_id = conn.execute("SELECT id FROM tenants WHERE slug = 'alpha-credit'").fetchone()[0]
        default_id = conn.execute("SELECT id FROM tenants WHERE slug = 'default-org'").fetchone()[0]
        platform_id = conn.execute("SELECT id FROM tenants WHERE slug = 'platform'").fetchone()[0]
        conn.execute(
            "INSERT INTO users (tenant_id, email, full_name, password_hash, role, is_superadmin, preferred_language, is_active, created_at) "
            "VALUES (?, 'owner@alpha.io', 'Alpha Owner', 'hash', 'OWNER_ADMIN', 0, 'en', 1, CURRENT_TIMESTAMP)",
            (alpha_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # Upgrade 012 -> 013
    r013 = _run_alembic(["upgrade", "head"], db_url)
    assert r013.returncode == 0, f"alembic upgrade 012->013 failed:\n{r013.stdout}\n{r013.stderr}"

    conn = _connect_sqlite(db_file)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("013",)
        for tid in (default_id, alpha_id):
            mods = {
                r[0]
                for r in conn.execute(
                    "SELECT module_code FROM tenant_modules WHERE tenant_id = ? AND is_enabled = 1",
                    (tid,),
                ).fetchall()
            }
            assert mods == {"CORE", "LENDING"}

        assert (
            conn.execute(
                "SELECT COUNT(*) FROM tenant_modules WHERE tenant_id = ?",
                (platform_id,),
            ).fetchone()[0]
            == 0
        )
        # Existing user preserved
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM users WHERE email = 'owner@alpha.io' AND tenant_id = ?",
                (alpha_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()

    # Clean downgrade 013 -> 012
    r_down = _run_alembic(["downgrade", "012"], db_url)
    assert r_down.returncode == 0, f"alembic downgrade 013->012 failed:\n{r_down.stdout}\n{r_down.stderr}"

    conn = _connect_sqlite(db_file)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("012",)
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "platform_modules" not in tables
        assert "platform_module_dependencies" not in tables
        assert "tenant_modules" not in tables
        # Commercial data still intact
        assert conn.execute("SELECT COUNT(*) FROM users WHERE email = 'owner@alpha.io'").fetchone()[0] == 1
    finally:
        conn.close()

    # Re-upgrade 012 -> 013 succeeds cleanly
    r_reup = _run_alembic(["upgrade", "head"], db_url)
    assert r_reup.returncode == 0, f"re-upgrade 012->013 failed:\n{r_reup.stdout}\n{r_reup.stderr}"


def test_03_constraints_and_orm_roundtrip(tmp_path: Path):
    db_file = tmp_path / "constraints_013.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    res = _run_alembic(["upgrade", "head"], db_url)
    assert res.returncode == 0, res.stderr

    conn = _connect_sqlite(db_file)
    try:
        default_id = conn.execute("SELECT id FROM tenants WHERE slug = 'default-org'").fetchone()[0]

        # 1. Duplicate composite PK in platform_module_dependencies rejected
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO platform_module_dependencies (module_code, required_module_code) "
                "VALUES ('LENDING', 'CORE')"
            )

        # 2. CHECK (module_code <> required_module_code) rejects self-dependency
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO platform_module_dependencies (module_code, required_module_code) "
                "VALUES ('CORE', 'CORE')"
            )

        # 3. FK in platform_module_dependencies rejects unknown module
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO platform_module_dependencies (module_code, required_module_code) "
                "VALUES ('UNKNOWN_MOD', 'CORE')"
            )

        # 4. UNIQUE (tenant_id, module_code) in tenant_modules rejects duplicate
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tenant_modules (tenant_id, module_code, is_enabled) VALUES (?, 'CORE', 1)",
                (default_id,),
            )

        # 5. FK in tenant_modules rejects unknown module_code, unknown tenant_id, unknown user_id
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tenant_modules (tenant_id, module_code, is_enabled) VALUES (?, 'NO_SUCH_MOD', 1)",
                (default_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tenant_modules (tenant_id, module_code, is_enabled) VALUES (999999, 'CORE', 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO tenant_modules (tenant_id, module_code, is_enabled, updated_by_user_id) "
                "VALUES (?, 'BROKER', 0, 999999)",
                (default_id,),
            )
    finally:
        conn.close()

    # 6. SQLAlchemy ORM round-trip (including JSON config_json and SUPERADMIN updated_by_user_id)
    eng = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)
    db = SessionLocal()
    try:
        platform_t = db.query(TenantModel).filter(TenantModel.slug == "platform").one()
        default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").one()

        superadmin = UserModel(
            tenant_id=platform_t.id,
            email="super@platform.io",
            full_name="Platform Super",
            password_hash="bcrypt-hash",
            role="OWNER_ADMIN",
            is_superadmin=True,
            is_active=True,
        )
        db.add(superadmin)
        db.flush()

        all_mods = db.query(PlatformModuleModel).order_by(PlatformModuleModel.code).all()
        assert [m.code for m in all_mods] == ["BROKER", "COMMERCE", "CORE", "FINANCING", "LENDING"]

        all_deps = db.query(PlatformModuleDependencyModel).all()
        assert len(all_deps) == 5

        lending_tm = (
            db.query(TenantModuleModel)
            .filter(
                TenantModuleModel.tenant_id == default_t.id,
                TenantModuleModel.module_code == "LENDING",
            )
            .one()
        )
        lending_tm.config_json = {"lending_entity_name": "Demo Lending SAPI de CV"}
        lending_tm.updated_by_user_id = superadmin.id
        lending_tm.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(lending_tm)

        assert lending_tm.config_json == {"lending_entity_name": "Demo Lending SAPI de CV"}
        assert lending_tm.updated_by_user_id == superadmin.id
        assert lending_tm.module.code == "LENDING"
        assert lending_tm.tenant.slug == "default-org"
    finally:
        db.close()
        eng.dispose()


def test_04_postgresql_offline_sql_generation():
    res = _run_alembic(
        ["upgrade", "012:013", "--sql"],
        "postgresql+psycopg://user:pass@localhost:5432/nura_test",
    )
    assert res.returncode == 0, f"Offline PG SQL failed:\n{res.stdout}\n{res.stderr}"
    sql_upper = res.stdout.upper()
    assert "CREATE TABLE PLATFORM_MODULES" in sql_upper
    assert "CREATE TABLE PLATFORM_MODULE_DEPENDENCIES" in sql_upper
    assert "CREATE TABLE TENANT_MODULES" in sql_upper
    assert "JSONB" in sql_upper
    assert "ON CONFLICT (TENANT_ID, MODULE_CODE) DO NOTHING" in sql_upper
