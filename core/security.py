from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import os
import secrets
import time
from typing import Optional

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import IS_PRODUCTION
from core.schema_guard import is_auth_security_schema_ready
from models import PasswordResetTokenModel, RateLimitModel

_RATE_BUCKETS = defaultdict(list)
_DEV_FALLBACK_HMAC_SECRET = secrets.token_hex(32)
_LAST_AUTH_CLEANUP_TS = 0.0


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_valid_ip(raw_ip: Optional[str]) -> Optional[str]:
    if not raw_ip:
        return None
    candidate = raw_ip.strip()
    if not candidate or len(candidate) > 45:
        return None
    try:
        ip_obj = ipaddress.ip_address(candidate)
        return str(ip_obj)[:45]
    except ValueError:
        return None


def _is_trusted_proxy_peer(peer_host: Optional[str]) -> bool:
    parsed = _parse_valid_ip(peer_host)
    if not parsed:
        return False
    trusted_env = {
        ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
    }
    if parsed in trusted_env:
        return True
    if not IS_PRODUCTION:
        return False
    ip_obj = ipaddress.ip_address(parsed)
    return bool(ip_obj.is_private or ip_obj.is_loopback)


def get_client_ip(request: Request) -> str:
    peer_host = request.client.host if (request.client and request.client.host) else None
    if _is_trusted_proxy_peer(peer_host):
        # 1. Edge header overwritten by Render/Cloudflare (not spoofable by client list injection)
        trusted_header = os.getenv("TRUSTED_CLIENT_IP_HEADER", "cf-connecting-ip").strip().lower()
        if trusted_header:
            edge_ip = _parse_valid_ip(request.headers.get(trusted_header))
            if edge_ip:
                return edge_ip

        # 2. Controlled rightmost non-private hop from X-Forwarded-For
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            hops = [
                _parse_valid_ip(part)
                for part in forwarded.split(",")
                if _parse_valid_ip(part) is not None
            ]
            if hops:
                try:
                    proxy_hops = max(1, int(os.getenv("RENDER_PROXY_HOPS", "2")))
                except ValueError:
                    proxy_hops = 2
                idx = max(0, len(hops) - proxy_hops)
                return hops[idx]

    direct_ip = _parse_valid_ip(peer_host)
    if direct_ip:
        return direct_ip
    return "UNKNOWN_CLIENT_IP"


def _get_hmac_secrets() -> tuple[bytes, str, Optional[bytes], Optional[str]]:
    primary_raw = os.getenv("RATE_LIMIT_HMAC_SECRET", "").strip()
    if IS_PRODUCTION:
        if len(primary_raw) < 32:
            raise RuntimeError("RATE_LIMIT_HMAC_SECRET must be set (>= 32 chars) in production.")
    else:
        if len(primary_raw) < 16:
            primary_raw = _DEV_FALLBACK_HMAC_SECRET

    primary_bytes = primary_raw.encode("utf-8")
    primary_kid = hashlib.sha256(primary_bytes).hexdigest()[:8]

    prev_raw = os.getenv("RATE_LIMIT_HMAC_SECRET_PREVIOUS", "").strip()
    if prev_raw and len(prev_raw) >= 16 and prev_raw != primary_raw:
        prev_bytes = prev_raw.encode("utf-8")
        prev_kid = hashlib.sha256(prev_bytes).hexdigest()[:8]
        return primary_bytes, primary_kid, prev_bytes, prev_kid

    return primary_bytes, primary_kid, None, None


def compute_email_hmac_buckets(operation_prefix: str, raw_email: str) -> tuple[str, Optional[str]]:
    normalized = (raw_email or "").strip().lower()
    primary_bytes, primary_kid, prev_bytes, prev_kid = _get_hmac_secrets()
    primary_digest = hmac.new(primary_bytes, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    primary_bucket = f"{operation_prefix}:{primary_kid}:{primary_digest}"

    prev_bucket = None
    if prev_bytes and prev_kid:
        prev_digest = hmac.new(prev_bytes, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
        prev_bucket = f"{operation_prefix}:{prev_kid}:{prev_digest}"

    return primary_bucket, prev_bucket


def cleanup_expired_auth_security_records(db: Session, now_utc: datetime, force: bool = False) -> None:
    global _LAST_AUTH_CLEANUP_TS
    now_ts = time.time()
    if not force and (now_ts - _LAST_AUTH_CLEANUP_TS) < 120.0:
        return
    if not is_auth_security_schema_ready(db):
        return

    _LAST_AUTH_CLEANUP_TS = now_ts
    rl_cutoff = now_utc - timedelta(minutes=5)
    prt_cutoff = now_utc - timedelta(hours=24)

    try:
        expired_rl_ids = [
            row[0] for row in (
                db.query(RateLimitModel.id)
                .filter(RateLimitModel.window_expires_at < rl_cutoff)
                .order_by(RateLimitModel.window_expires_at.asc())
                .limit(100)
                .all()
            )
        ]
        if expired_rl_ids:
            db.query(RateLimitModel).filter(RateLimitModel.id.in_(expired_rl_ids)).delete(synchronize_session=False)

        expired_prt_ids = [
            row[0] for row in (
                db.query(PasswordResetTokenModel.id)
                .filter(PasswordResetTokenModel.expires_at < prt_cutoff)
                .order_by(PasswordResetTokenModel.expires_at.asc())
                .limit(100)
                .all()
            )
        ]
        if expired_prt_ids:
            db.query(PasswordResetTokenModel).filter(PasswordResetTokenModel.id.in_(expired_prt_ids)).delete(synchronize_session=False)

        if expired_rl_ids or expired_prt_ids:
            db.commit()
    except Exception:
        db.rollback()


def check_rate_limit(bucket_key: str, max_attempts: int = 6, window_seconds: int = 300) -> bool:
    now = time.time()
    _RATE_BUCKETS[bucket_key] = [t for t in _RATE_BUCKETS[bucket_key] if now - t < window_seconds]
    if len(_RATE_BUCKETS[bucket_key]) >= max_attempts:
        return False
    _RATE_BUCKETS[bucket_key].append(now)
    return True


def consume_rate_limit_atomic(
    db: Session,
    bucket_key: str,
    max_attempts: int,
    window_seconds: int,
    previous_bucket_key: Optional[str] = None,
    now_utc: Optional[datetime] = None
) -> tuple[bool, int]:
    current_utc = now_utc or datetime.now(timezone.utc)

    # Transitional fallback ONLY if database is still on revision 007
    if not is_auth_security_schema_ready(db):
        allowed = check_rate_limit(bucket_key, max_attempts=max_attempts, window_seconds=window_seconds)
        return allowed, (0 if allowed else window_seconds)

    cleanup_expired_auth_security_records(db, current_utc)
    new_expires_at = current_utc + timedelta(seconds=window_seconds)

    upsert_sql = text("""
        INSERT INTO rate_limits (
            bucket_key, attempt_count, window_started_at, window_expires_at, updated_at
        )
        VALUES (
            :bucket_key, 1, :now_utc, :new_expires_at, :now_utc
        )
        ON CONFLICT (bucket_key) DO UPDATE SET
            attempt_count = CASE
                WHEN rate_limits.window_expires_at <= :now_utc THEN 1
                ELSE rate_limits.attempt_count + 1
            END,
            window_started_at = CASE
                WHEN rate_limits.window_expires_at <= :now_utc THEN :now_utc
                ELSE rate_limits.window_started_at
            END,
            window_expires_at = CASE
                WHEN rate_limits.window_expires_at <= :now_utc THEN :new_expires_at
                ELSE rate_limits.window_expires_at
            END,
            updated_at = :now_utc
        RETURNING attempt_count, window_expires_at
    """)

    row = db.execute(
        upsert_sql,
        {
            "bucket_key": bucket_key[:140],
            "now_utc": current_utc,
            "new_expires_at": new_expires_at
        }
    ).fetchone()

    attempt_count = int(row[0])
    raw_expires = row[1]
    if isinstance(raw_expires, str):
        exp_str = raw_expires[:-1] + "+00:00" if raw_expires.endswith("Z") else raw_expires
        expires_dt = datetime.fromisoformat(exp_str)
    else:
        expires_dt = raw_expires
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)

    # Zero-gap HMAC rotation: carry over active attempts from previous key if present
    if previous_bucket_key:
        prev_row = (
            db.query(RateLimitModel.attempt_count, RateLimitModel.window_expires_at)
            .filter(
                RateLimitModel.bucket_key == previous_bucket_key[:140],
                RateLimitModel.window_expires_at > current_utc
            )
            .first()
        )
        if prev_row:
            attempt_count += int(prev_row[0])

    db.commit()

    retry_after = max(1, int((expires_dt - current_utc).total_seconds()))
    if attempt_count > max_attempts:
        return False, retry_after
    return True, 0


def reset_rate_limit_bucket(db: Session, bucket_key: str, previous_bucket_key: Optional[str] = None) -> None:
    if not is_auth_security_schema_ready(db):
        _RATE_BUCKETS.pop(bucket_key, None)
        if previous_bucket_key:
            _RATE_BUCKETS.pop(previous_bucket_key, None)
        return
    keys = [bucket_key[:140]]
    if previous_bucket_key:
        keys.append(previous_bucket_key[:140])
    db.query(RateLimitModel).filter(RateLimitModel.bucket_key.in_(keys)).delete(synchronize_session=False)
    db.commit()


def mask_email_for_audit(raw_email: str) -> str:
    clean = (raw_email or "").strip().lower()
    if "@" not in clean:
        return "invalid_identifier"
    local, domain = clean.split("@", 1)
    masked_local = (local[:2] + "***") if len(local) > 2 else "***"
    return f"{masked_local}@{domain}"[:60]
