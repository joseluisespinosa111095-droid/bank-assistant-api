import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def is_production() -> bool:
    return (
        os.getenv("RENDER", "false").strip().lower() == "true"
        or os.getenv("ENVIRONMENT", "development").strip().lower() == "production"
    )


def is_sqlite_url(url: str) -> bool:
    if not isinstance(url, str):
        return False
    lowered = url.strip().lower()
    return lowered.startswith("sqlite://") or lowered.startswith("sqlite+pysqlite://")


def normalize_database_url(raw_url: str, *, allow_sqlite: bool = True) -> str:
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("Database URL must be a non-empty string.")

    url = raw_url.strip()

    if "#" in url:
        raise ValueError(
            "Database URL contains an unencoded '#' character; percent-encode it as '%23'."
        )

    lowered = url.lower()

    if lowered.startswith("postgresql+psycopg2://") or lowered.startswith("postgres+psycopg2://"):
        raise ValueError(
            "Driver 'psycopg2' is not supported; use 'postgresql+psycopg://' (psycopg v3)."
        )

    if lowered.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg://" + url[len("postgresql+psycopg://"):]

    if lowered.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]

    if lowered.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]

    if is_sqlite_url(url):
        if not allow_sqlite:
            raise RuntimeError("SQLite database URLs are not allowed in production.")
        return url

    raise ValueError("Unsupported database URL scheme.")


def get_migration_database_url() -> str:
    raw_url = (os.getenv("MIGRATION_DATABASE_URL") or "").strip()
    if not raw_url:
        raw_url = (os.getenv("DATABASE_URL") or "").strip()
    if not raw_url:
        raise RuntimeError(
            "CRITICAL: MIGRATION_DATABASE_URL or DATABASE_URL must be set for migrations."
        )
    return normalize_database_url(raw_url, allow_sqlite=not is_production())


def get_runtime_database_url() -> str:
    raw_url = (os.getenv("DATABASE_URL") or "").strip()
    if not raw_url:
        raise RuntimeError("CRITICAL: DATABASE_URL must be set.")
    return normalize_database_url(raw_url, allow_sqlite=not is_production())


def get_engine_connect_args(url: str) -> dict:
    return {"check_same_thread": False} if is_sqlite_url(url) else {}


_RUNTIME_DB_ATTRS = frozenset({"DATABASE_URL", "connect_args", "engine", "SessionLocal"})


def _init_runtime_db() -> None:
    """Lazily initializes the singleton runtime SQLAlchemy engine & SessionLocal once.

    Using PEP 562 module __getattr__ keeps `db_config` as the single source of truth
    for runtime `engine` and `SessionLocal` without breaking tools that import URL
    helpers when only `MIGRATION_DATABASE_URL` is set (e.g. `alembic/env.py`).
    """
    if "engine" in globals() and "SessionLocal" in globals():
        return
    database_url = get_runtime_database_url()
    connect_args = get_engine_connect_args(database_url)
    engine = create_engine(database_url, connect_args=connect_args)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    globals()["DATABASE_URL"] = database_url
    globals()["connect_args"] = connect_args
    globals()["engine"] = engine
    globals()["SessionLocal"] = session_local


def __getattr__(name: str):
    if name in _RUNTIME_DB_ATTRS:
        _init_runtime_db()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
