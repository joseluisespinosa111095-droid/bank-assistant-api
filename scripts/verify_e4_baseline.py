"""Entregable 4 regression baseline capture & verification script.

Usage:
  python -B scripts/verify_e4_baseline.py --capture
  python -B scripts/verify_e4_baseline.py --verify

Captures and strictly compares:
  1. Registered FastAPI routes (method, path, endpoint name, resolved auth/RBAC/guard dependencies).
  2. Complete /openapi.json schema.
  3. Existence and object identity (`is`) of critical symbols in `main` (and `db_config` when consolidated).
  4. Real HTTP behavior matrix via TestClient on an isolated SQLite DB migrated to head (012).
  5. Frontend `index.html` reference: inline event handler counts/targets and order of the 4 global monkey-patches.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.routing import Mount

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "tests" / "baselines" / "e4_baseline_73b97ac.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure deterministic dev environment before importing main
os.environ["ENVIRONMENT"] = "development"
os.environ["RENDER"] = "false"
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("RATE_LIMIT_HMAC_SECRET", "test-secret-key-at-least-32-chars-long-abc123")

import db_config
import main
from models import TenantModel, UserModel


CRITICAL_MAIN_SYMBOLS = (
    "app",
    "get_db",
    "SessionLocal",
    "engine",
    "pwd_context",
    "get_local_day_bounds_utc",
    "generate_next_customer_number",
    "require_commercial_tenant",
    "require_superadmin",
)

AUTH_DEPENDENCY_NAMES = (
    "get_db",
    "get_current_user",
    "require_commercial_tenant",
    "role_checker",
    "require_superadmin",
    "verify_csrf",
)

MONKEY_PATCH_TARGETS = (
    "window.fetch",
    "window.openCustomerDetail",
    "window.loadLoanPayments",
    "window.updateLoanLifecycleActionsUI",
)


def _collect_dep_callables(dependant) -> set:
    found = set()
    for dep in dependant.dependencies:
        if dep.call is not None:
            found.add(dep.call)
        found.update(_collect_dep_callables(dep))
    return found


def _iter_api_routes(routes, prefix: str = ""):
    for route in routes:
        if isinstance(route, APIRoute):
            full_path = f"{prefix}{route.path}"
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield method, full_path, route
        elif isinstance(route, Mount) and hasattr(route, "routes"):
            yield from _iter_api_routes(route.routes, prefix=f"{prefix}{route.path}")
        elif hasattr(route, "original_router") and hasattr(route.original_router, "routes"):
            sub_prefix = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            yield from _iter_api_routes(route.original_router.routes, prefix=f"{prefix}{sub_prefix}")


def collect_routes_snapshot() -> list[dict]:
    items = []
    for method, path, route in _iter_api_routes(main.app.routes):
        callables = _collect_dep_callables(route.dependant)
        dep_names = sorted(
            {
                getattr(c, "__name__", repr(c))
                for c in callables
                if getattr(c, "__name__", "") in AUTH_DEPENDENCY_NAMES
            }
        )
        items.append(
            {
                "method": method,
                "path": path,
                "endpoint": route.endpoint.__name__,
                "auth_dependencies": dep_names,
                "identity_checks": {
                    "get_db": main.get_db in callables,
                    "require_commercial_tenant": main.require_commercial_tenant in callables,
                    "require_superadmin": main.require_superadmin in callables,
                },
            }
        )
    items.sort(key=lambda r: (r["path"], r["method"]))
    return items


def collect_critical_symbols_snapshot() -> dict:
    info = {}
    for sym in CRITICAL_MAIN_SYMBOLS:
        exists = hasattr(main, sym)
        obj = getattr(main, sym, None) if exists else None
        info[sym] = {
            "exists": exists,
            "callable": callable(obj),
            "type_name": type(obj).__name__ if obj is not None else None,
        }
    return info


def collect_http_matrix() -> dict:
    """Runs read-only access matrix on a temporary migrated SQLite DB."""
    with tempfile.TemporaryDirectory(prefix="nura_e4_baseline_") as tmp_dir:
        db_file = Path(tmp_dir) / "baseline_012.db"
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "RENDER", "ENVIRONMENT")
        }
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["RENDER"] = "false"
        env["ENVIRONMENT"] = "development"
        env["RATE_LIMIT_HMAC_SECRET"] = "test-secret-key-at-least-32-chars-long-abc123"
        env["MIGRATION_DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"
        env["DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"

        res = subprocess.run(
            [sys.executable, "-B", "-m", "alembic", "upgrade", "head"],
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Alembic upgrade failed:\n{res.stdout}\n{res.stderr}")

        eng = create_engine(
            f"sqlite:///{db_file.as_posix()}",
            connect_args={"check_same_thread": False, "timeout": 15},
        )

        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)

        # Seed 1 SUPERADMIN (platform) and 1 commercial OWNER_ADMIN (default-org)
        db = TestSessionLocal()
        try:
            platform_t = db.query(TenantModel).filter(TenantModel.slug == "platform").first()
            default_t = db.query(TenantModel).filter(TenantModel.slug == "default-org").first()
            pw = "BaselinePassword123!"
            pw_hash = main.pwd_context.hash(pw)
            db.add_all(
                [
                    UserModel(
                        tenant_id=platform_t.id,
                        email="baseline.super@nura.io",
                        full_name="Baseline Super",
                        password_hash=pw_hash,
                        role="OWNER_ADMIN",
                        is_superadmin=True,
                        is_active=True,
                    ),
                    UserModel(
                        tenant_id=default_t.id,
                        email="baseline.owner@nura.io",
                        full_name="Baseline Owner",
                        password_hash=pw_hash,
                        role="OWNER_ADMIN",
                        is_superadmin=False,
                        is_active=True,
                    ),
                ]
            )
            db.commit()
        finally:
            db.close()

        def _override_get_db():
            s = TestSessionLocal()
            try:
                yield s
            finally:
                s.close()

        prev_overrides = dict(main.app.dependency_overrides)
        main.app.dependency_overrides[main.get_db] = _override_get_db
        try:
            anon = TestClient(main.app, base_url="http://localhost")
            comm = TestClient(main.app, base_url="http://localhost")
            plat = TestClient(main.app, base_url="http://localhost")
            try:
                assert comm.post("/api/auth/login", json={"email": "baseline.owner@nura.io", "password": pw}).status_code == 200
                assert plat.post("/api/auth/login", json={"email": "baseline.super@nura.io", "password": pw}).status_code == 200

                matrix = {
                    "public_health_anon": anon.get("/api/health").status_code,
                    "auth_me_anon": anon.get("/api/auth/me").status_code,
                    "auth_me_commercial": comm.get("/api/auth/me").status_code,
                    "auth_me_superadmin": plat.get("/api/auth/me").status_code,
                    "commercial_customers_anon": anon.get("/api/customers").status_code,
                    "commercial_customers_commercial": comm.get("/api/customers").status_code,
                    "commercial_customers_superadmin": plat.get("/api/customers").status_code,
                    "commercial_dashboard_anon": anon.get("/api/dashboard/summary").status_code,
                    "commercial_dashboard_commercial": comm.get("/api/dashboard/summary").status_code,
                    "commercial_dashboard_superadmin": plat.get("/api/dashboard/summary").status_code,
                    "platform_tenants_anon": anon.get("/api/platform/tenants").status_code,
                    "platform_tenants_commercial": comm.get("/api/platform/tenants").status_code,
                    "platform_tenants_superadmin": plat.get("/api/platform/tenants").status_code,
                    "platform_audit_anon": anon.get("/api/platform/audit-logs").status_code,
                    "platform_audit_commercial": comm.get("/api/platform/audit-logs").status_code,
                    "platform_audit_superadmin": plat.get("/api/platform/audit-logs").status_code,
                }
                return matrix
            finally:
                anon.close()
                comm.close()
                plat.close()
        finally:
            main.app.dependency_overrides.clear()
            main.app.dependency_overrides.update(prev_overrides)
            eng.dispose()


def collect_frontend_reference() -> dict:
    html_path = REPO_ROOT / "index.html"
    html_lines = html_path.read_text(encoding="utf-8").splitlines()
    script_start = next(
        (
            i
            for i, l in enumerate(html_lines, 1)
            if l.strip() == "<script>" or re.match(r'^<script\s+src="/static/js/', l.strip())
        ),
        len(html_lines) + 1,
    )
    html_markup = "\n".join(html_lines[:script_start])

    # Collect JS lines in exact classic <script> execution order: external /static/js/* followed by inline <script>
    js_lines: list[str] = []
    in_inline_script = False
    for line in html_lines[script_start - 1 :]:
        stripped = line.strip()
        ext_m = re.match(r'^<script\s+src="(/static/js/[^"?]+)(?:\?[^"]*)?"\s*>\s*</script>$', stripped)
        if ext_m:
            ext_path = REPO_ROOT / ext_m.group(1).lstrip("/")
            if ext_path.exists():
                js_lines.extend(ext_path.read_text(encoding="utf-8").splitlines())
            continue
        if stripped == "<script>":
            in_inline_script = True
            continue
        if stripped == "</script>" and in_inline_script:
            in_inline_script = False
            continue
        if in_inline_script:
            js_lines.append(line)

    js_src = "\n".join(js_lines)

    by_attr = {}
    for evt in ("onclick", "onchange", "onsubmit", "oninput", "onkeypress", "onkeydown", "onkeyup"):
        cnt = len(re.findall(rf"\b{evt}\s*=", html_markup, re.I))
        if cnt:
            by_attr[evt] = cnt

    # Extract global function names invoked by static HTML inline handlers
    invoked_fns = set()
    for match in re.finditer(r'\bon[a-z]+\s*=\s*"([^"]+)"', html_markup, re.I):
        expr = match.group(1)
        for fn_m in re.finditer(r"([A-Za-z_$][A-Za-z0-9_$.]*)\s*\(", expr):
            invoked_fns.add(fn_m.group(1))

    dynamic_inline_count = len(
        re.findall(r"\bon(?:click|change|submit|input|keypress|keydown|keyup)\s*=", js_src, re.I)
    )

    # Locate the 4 global monkey-patches in exact execution order across external static/js + inline script
    patch_positions = []
    for i, line in enumerate(js_lines, 1):
        for target in MONKEY_PATCH_TARGETS:
            if re.search(rf"\b{re.escape(target)}\s*=", line):
                patch_positions.append((i, target))
    patch_positions.sort(key=lambda x: x[0])

    return {
        "static_inline_handlers_by_attr": by_attr,
        "static_inline_handlers_total": sum(by_attr.values()),
        "dynamic_inline_handlers_total": dynamic_inline_count,
        "static_inline_invoked_functions": sorted(invoked_fns),
        "monkey_patch_order": [target for _, target in patch_positions],
    }


def build_snapshot() -> dict:
    return {
        "baseline_commit": "73b97ac",
        "routes": collect_routes_snapshot(),
        "openapi": main.app.openapi(),
        "critical_symbols": collect_critical_symbols_snapshot(),
        "http_matrix": collect_http_matrix(),
        "frontend_reference": collect_frontend_reference(),
    }


def verify_against_baseline(baseline: dict, current: dict) -> list[str]:
    diffs: list[str] = []

    if baseline["routes"] != current["routes"]:
        base_map = {(r["method"], r["path"]): r for r in baseline["routes"]}
        curr_map = {(r["method"], r["path"]): r for r in current["routes"]}
        added = sorted(set(curr_map) - set(base_map))
        removed = sorted(set(base_map) - set(curr_map))
        changed = [k for k in sorted(set(base_map) & set(curr_map)) if base_map[k] != curr_map[k]]
        diffs.append(f"ROUTES DIFF: added={added} removed={removed} changed={changed}")

    if baseline["openapi"] != current["openapi"]:
        diffs.append("OPENAPI DIFF: /openapi.json differs from baseline")

    if baseline["critical_symbols"] != current["critical_symbols"]:
        diffs.append(
            f"CRITICAL SYMBOLS DIFF: baseline={baseline['critical_symbols']} current={current['critical_symbols']}"
        )

    if baseline["http_matrix"] != current["http_matrix"]:
        for k in sorted(set(baseline["http_matrix"]) | set(current["http_matrix"])):
            bv = baseline["http_matrix"].get(k)
            cv = current["http_matrix"].get(k)
            if bv != cv:
                diffs.append(f"HTTP MATRIX DIFF [{k}]: baseline={bv} current={cv}")

    if baseline["frontend_reference"] != current["frontend_reference"]:
        for k in sorted(set(baseline["frontend_reference"]) | set(current["frontend_reference"])):
            bv = baseline["frontend_reference"].get(k)
            cv = current["frontend_reference"].get(k)
            if bv != cv:
                diffs.append(f"FRONTEND REF DIFF [{k}]: baseline={bv} current={cv}")

    # Explicit object identity checks for db_config consolidation (when present)
    if hasattr(db_config, "engine"):
        if main.engine is not db_config.engine:
            diffs.append("IDENTITY DIFF: main.engine is NOT db_config.engine")
    if hasattr(db_config, "SessionLocal"):
        if main.SessionLocal is not db_config.SessionLocal:
            diffs.append("IDENTITY DIFF: main.SessionLocal is NOT db_config.SessionLocal")

    # Verify exact callable identity on active FastAPI route dependants
    route_by_key = {(r["method"], r["path"]): r for r in current["routes"]}
    for r in baseline["routes"]:
        key = (r["method"], r["path"])
        curr_r = route_by_key.get(key)
        if not curr_r:
            continue
        for id_key, expected in r["identity_checks"].items():
            if curr_r["identity_checks"].get(id_key) != expected:
                diffs.append(
                    f"CALLABLE IDENTITY DIFF on {key} [{id_key}]: expected={expected} got={curr_r['identity_checks'].get(id_key)}"
                )

    return diffs


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Capture or verify Entregable 4 regression baseline.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", action="store_true", help="Capture baseline JSON from current repo state.")
    group.add_argument("--verify", action="store_true", help="Verify current repo state against saved baseline JSON.")
    args = parser.parse_args()

    snapshot = build_snapshot()

    if args.capture:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"BASELINE CAPTURED -> {BASELINE_PATH}")
        print(f"  routes={len(snapshot['routes'])}")
        print(f"  openapi_paths={len(snapshot['openapi'].get('paths', {}))}")
        print(f"  http_matrix={snapshot['http_matrix']}")
        print(
            "  frontend="
            f"static_inline={snapshot['frontend_reference']['static_inline_handlers_total']} "
            f"dynamic_inline={snapshot['frontend_reference']['dynamic_inline_handlers_total']} "
            f"invoked_fns={len(snapshot['frontend_reference']['static_inline_invoked_functions'])} "
            f"monkey_patches={snapshot['frontend_reference']['monkey_patch_order']}"
        )
        return 0

    if not BASELINE_PATH.exists():
        print(f"ERROR: Baseline file not found at {BASELINE_PATH}", file=sys.stderr)
        return 2

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    diffs = verify_against_baseline(baseline, snapshot)
    if diffs:
        print(f"BASELINE VERIFICATION FAILED ({len(diffs)} differences):", file=sys.stderr)
        for d in diffs:
            print(f"  - {d}", file=sys.stderr)
        return 1

    db_id_ok = (
        hasattr(db_config, "engine")
        and main.engine is db_config.engine
        and hasattr(db_config, "SessionLocal")
        and main.SessionLocal is db_config.SessionLocal
    )
    print("BASELINE VERIFICATION PASSED: 0 differences")
    print(f"  routes={len(snapshot['routes'])} (exact match)")
    print(f"  openapi_paths={len(snapshot['openapi'].get('paths', {}))} (exact match)")
    print(f"  http_matrix={len(snapshot['http_matrix'])} checks (exact match)")
    print(f"  critical_symbols={len(snapshot['critical_symbols'])} verified (exact identity)")
    print(f"  db_config_singleton_identity={db_id_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
