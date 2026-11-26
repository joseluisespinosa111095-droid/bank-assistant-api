import os


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
