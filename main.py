import os
import re
import time
import hmac
import hashlib
import ipaddress
import json
import logging
import secrets
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, List
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo
from urllib.parse import quote as url_quote

from fastapi import FastAPI, HTTPException, Depends, Response, Cookie, Request, Query, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
import google.generativeai as genai
from sqlalchemy import create_engine, text, or_, inspect, func
from sqlalchemy.orm import sessionmaker, Session, joinedload, selectinload
from sqlalchemy.exc import IntegrityError
from passlib.context import CryptContext

from db_config import is_production, get_runtime_database_url, get_engine_connect_args
from models import (
    Base,
    UserModel,
    SessionModel,
    RateLimitModel,
    PasswordResetTokenModel,
    ConversationModel,
    ChatMessageModel,
    AuditLogModel,
    CustomerModel,
    CustomerDocumentModel,
    LeadModel,
    LeadStageHistoryModel,
    InteractionModel,
    LoanModel,
    LoanInstallmentModel,
    LoanPaymentModel,
    LoanPaymentAllocationModel,
)

# --- SERVER LOGGING ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("nura.crm")

# --- DATABASE & SECURITY CONFIG ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
DUMMY_BCRYPT_HASH = pwd_context.hash("nura-constant-time-mitigation-baseline-secret")

IS_PRODUCTION = is_production()
DATABASE_URL = get_runtime_database_url()
connect_args = get_engine_connect_args(DATABASE_URL)
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)




def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# --- ROLES, PRODUCTS & CRM CATALOGS ---
VALID_ROLES = {
    "OWNER_ADMIN",         # Owners
    "SALES_AGENT",         # Salespersons / Vendedores
    "CREDIT_ADVISOR",      # Credit Analysts
    "OPERATIONS_OFFICER"   # Collections / Operations
}

VALID_LANGUAGES = {"en", "es"}

SUPPORTED_PRODUCTS = ["Personal Loan", "Business Loan"]

VALID_ENTITY_TYPES = {"INDIVIDUAL", "BUSINESS"}
VALID_CUSTOMER_STATUSES = {"ACTIVE", "INACTIVE", "PENDING_VERIFICATION"}
VALID_VERIFICATION_STATUSES = {"NOT_PROVIDED", "PENDING_VERIFICATION", "VERIFIED"}

VALID_LEAD_STAGES = {
    "NEW", "CONTACT_ATTEMPTED", "CONTACTED", "QUALIFIED",
    "APPLICATION_STARTED", "UNDER_REVIEW", "APPROVED",
    "CONVERTED", "NURTURE", "LOST"
}

QUALIFIED_OR_LATER_STAGES = {
    "QUALIFIED", "APPLICATION_STARTED", "UNDER_REVIEW", "APPROVED"
}

VALID_LEAD_SOURCES = {
    "REFERRAL", "INBOUND_CALL", "CAMPAIGN", "DIRECT_OUTREACH",
    "WEBSITE", "REPEAT_BORROWER", "OTHER"
}

VALID_LEAD_PRIORITIES = {"LOW", "MEDIUM", "HIGH"}

VALID_INTERACTION_TYPES = {"CALL", "NOTE", "WHATSAPP", "EMAIL", "MEETING"}
VALID_INTERACTION_DIRECTIONS = {"OUTBOUND", "INBOUND", "INTERNAL"}
VALID_INTERACTION_VISIBILITIES = {"INTERNAL", "CUSTOMER_FACING"}

VALID_CALL_OUTCOMES = {
    "ANSWERED", "NO_ANSWER", "VOICEMAIL", "REQUESTED_CALLBACK",
    "INTERESTED", "NOT_INTERESTED", "FOLLOW_UP_REQUIRED", "WRONG_NUMBER"
}

VALID_LOAN_STATUSES = {"DRAFT", "APPROVED", "ACTIVE", "PAID_OFF", "CANCELLED", "DEFAULTED"}
VALID_DISBURSEMENT_METHODS = {"SPEI_TRANSFER", "BANK_DEPOSIT", "CHECK", "CASH_RECEIPT"}
VALID_PAYMENT_STATUSES = {"POSTED", "REVERSED"}
VALID_PAYMENT_METHODS = {"SPEI_TRANSFER", "BANK_DEPOSIT", "CHECK", "CASH_RECEIPT"}
VALID_DOCUMENT_TYPES = {"INE_ID", "PROOF_OF_ADDRESS", "BANK_STATEMENT", "SIGNED_CONTRACT", "OTHER"}


# --- AUTHORITATIVE FINANCIAL ENGINE (FLAT & DECLINING BALANCE) ---
class LoanFinancialEngine:
    @staticmethod
    def add_days_iso(iso_date_str: str, days_to_add: int) -> str:
        parts = [int(p) for p in iso_date_str.split("-")]
        dt = date(parts[0], parts[1], parts[2]) + timedelta(days=days_to_add)
        return dt.isoformat()

    @staticmethod
    def calculate_flat_schedule(
        principal_pesos: float,
        monthly_rate_percent: float,
        assumed_interest_months: int,
        term_weeks: int,
        first_payment_date_iso: str,
        interest_calculation_method: str = "FLAT_ORIGINAL_PRINCIPAL"
    ) -> dict:
        import math
        principal_cents = max(0, int(round(Decimal(str(principal_pesos)) * Decimal("100"))))
        rate_bps = max(0, int(round(Decimal(str(monthly_rate_percent)) * Decimal("100"))))
        months_count = max(1, int(assumed_interest_months))
        installments_count = max(1, int(term_weeks))
        method = (interest_calculation_method or "FLAT_ORIGINAL_PRINCIPAL").strip().upper()

        monthly_interest_cents = int(
            ((Decimal(principal_cents) * Decimal(rate_bps)) / Decimal("10000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

        schedule = []
        remaining_principal_cents = principal_cents
        accumulated_principal_cents = 0
        accumulated_interest_cents = 0

        if method == "DECLINING_BALANCE":
            if rate_bps == 0:
                weekly_rate = 0.0
                regular_payment_cents = principal_cents // installments_count
            else:
                weekly_rate = (rate_bps * 12.0) / (10000.0 * 52.0)
                factor = (1.0 + weekly_rate) ** installments_count
                raw_pmt = principal_cents * (weekly_rate * factor) / (factor - 1.0)
                regular_payment_cents = int(math.floor(raw_pmt + 0.5))

            for i in range(1, installments_count + 1):
                is_final = (i == installments_count)
                due_date_str = LoanFinancialEngine.add_days_iso(first_payment_date_iso, (i - 1) * 7)
                due_date_obj = date.fromisoformat(due_date_str)
                beginning_principal_cents = remaining_principal_cents

                interest_component_cents = (
                    int(math.floor(beginning_principal_cents * weekly_rate + 0.5))
                    if rate_bps > 0 else 0
                )
                if is_final:
                    principal_component_cents = remaining_principal_cents
                else:
                    principal_component_cents = max(
                        0,
                        min(remaining_principal_cents, regular_payment_cents - interest_component_cents)
                    )

                scheduled_payment_cents = principal_component_cents + interest_component_cents
                ending_principal_cents = beginning_principal_cents - principal_component_cents

                accumulated_principal_cents += principal_component_cents
                accumulated_interest_cents += interest_component_cents
                remaining_principal_cents = ending_principal_cents

                schedule.append({
                    "installment_number": i,
                    "due_date": due_date_obj,
                    "beginning_principal_cents": beginning_principal_cents,
                    "principal_due_cents": principal_component_cents,
                    "interest_due_cents": interest_component_cents,
                    "scheduled_payment_cents": scheduled_payment_cents,
                    "ending_principal_cents": ending_principal_cents
                })

            total_interest_cents = accumulated_interest_cents
            total_repayment_cents = principal_cents + total_interest_cents
            monthly_interest_cents = int(math.floor((total_interest_cents / months_count) + 0.5))
        else:
            total_interest_cents = monthly_interest_cents * months_count
            total_repayment_cents = principal_cents + total_interest_cents
            regular_principal_cents = principal_cents // installments_count
            regular_interest_cents = total_interest_cents // installments_count

            for i in range(1, installments_count + 1):
                is_final = (i == installments_count)
                due_date_str = LoanFinancialEngine.add_days_iso(first_payment_date_iso, (i - 1) * 7)
                due_date_obj = date.fromisoformat(due_date_str)
                beginning_principal_cents = remaining_principal_cents

                principal_component_cents = (
                    (principal_cents - accumulated_principal_cents) if is_final else regular_principal_cents
                )
                interest_component_cents = (
                    (total_interest_cents - accumulated_interest_cents) if is_final else regular_interest_cents
                )
                scheduled_payment_cents = principal_component_cents + interest_component_cents
                ending_principal_cents = beginning_principal_cents - principal_component_cents

                accumulated_principal_cents += principal_component_cents
                accumulated_interest_cents += interest_component_cents
                remaining_principal_cents = ending_principal_cents

                schedule.append({
                    "installment_number": i,
                    "due_date": due_date_obj,
                    "beginning_principal_cents": beginning_principal_cents,
                    "principal_due_cents": principal_component_cents,
                    "interest_due_cents": interest_component_cents,
                    "scheduled_payment_cents": scheduled_payment_cents,
                    "ending_principal_cents": ending_principal_cents
                })

        return {
            "interest_calculation_method": method,
            "principal_cents": principal_cents,
            "monthly_rate_percent": float(monthly_rate_percent),
            "monthly_interest_rate_bps": rate_bps,
            "monthly_interest_amount_cents": monthly_interest_cents,
            "months_count": months_count,
            "installments_count": installments_count,
            "total_interest_cents": total_interest_cents,
            "total_repayment_cents": total_repayment_cents,
            "regular_installment_cents": schedule[0]["scheduled_payment_cents"],
            "final_installment_cents": schedule[-1]["scheduled_payment_cents"],
            "first_payment_date": date.fromisoformat(first_payment_date_iso),
            "maturity_date": schedule[-1]["due_date"],
            "schedule": schedule
        }


# --- STARTUP SCHEMA GUARD ---
_AUTH_SECURITY_SCHEMA_READY = False


def is_auth_security_schema_ready(db: Optional[Session] = None) -> bool:
    global _AUTH_SECURITY_SCHEMA_READY
    if _AUTH_SECURITY_SCHEMA_READY:
        return True
    target_bind = db.get_bind() if db is not None else engine
    try:
        tables = set(inspect(target_bind).get_table_names())
        if {"rate_limits", "password_reset_tokens"}.issubset(tables):
            _AUTH_SECURITY_SCHEMA_READY = True
            return True
    except Exception:
        pass
    return False


EXPECTED_ALEMBIC_REVISION = "011"  # al crear una nueva migracion, actualizar solo esta constante


def verify_database_schema_compatibility(db_engine):
    global _AUTH_SECURITY_SCHEMA_READY
    if not IS_PRODUCTION and "sqlite" in str(db_engine.url):
        is_auth_security_schema_ready()
        return  # SQLite dev DB trusts test fixtures / alembic
    try:
        inspector = inspect(db_engine)
        tables = set(inspector.get_table_names())
        base_required_tables = {
            "users", "sessions", "conversations", "chat_messages", "audit_logs",
            "customers", "customer_documents", "leads", "lead_stage_history",
            "interactions", "loans", "loan_installments",
            "loan_payments", "loan_payment_allocations",
            "rate_limits", "password_reset_tokens"
        }
        if not base_required_tables.issubset(tables):
            missing = base_required_tables - tables
            raise RuntimeError(f"Database schema is missing required tables: {missing}")

        with db_engine.begin() as conn:
            res = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            if not res:
                raise RuntimeError("No alembic_version record found in database.")
            rev = res[0]
            if rev != EXPECTED_ALEMBIC_REVISION:
                raise RuntimeError(
                    f"Database schema is at revision '{rev}', expected '{EXPECTED_ALEMBIC_REVISION}'. Run 'alembic upgrade head'."
                )
            _AUTH_SECURITY_SCHEMA_READY = True

            if db_engine.dialect.name == "postgresql":
                conn.execute(text("""
                    DO $$
                    DECLARE
                        r RECORD;
                    BEGIN
                        FOR r IN
                            SELECT conname, pg_get_constraintdef(oid) AS def
                            FROM pg_constraint
                            WHERE conrelid = 'loans'::regclass AND contype = 'c'
                        LOOP
                            IF r.def ILIKE '%FLAT_ORIGINAL_PRINCIPAL%'
                               OR r.def ILIKE '%ORIGINAL_PRINCIPAL%'
                               OR r.def ILIKE '%FLOOR_REGULAR_EXACT_FINAL%'
                               OR r.def ILIKE '%monthly_interest_amount_cents%'
                               OR r.def ILIKE '%regular_installment_cents%'
                            THEN
                                EXECUTE format('ALTER TABLE loans DROP CONSTRAINT IF EXISTS %I', r.conname);
                            END IF;
                        END LOOP;
                    END $$;
                """))
                conn.execute(text("""
                    CREATE OR REPLACE FUNCTION assert_loan_ledger_integrity(p_loan_id INTEGER)
                    RETURNS VOID LANGUAGE plpgsql AS $fn$
                    DECLARE
                        v_loan RECORD;
                        v_posted_amount BIGINT;
                        v_posted_interest BIGINT;
                        v_posted_principal BIGINT;
                        v_inst_amt_paid BIGINT;
                        v_inst_int_paid BIGINT;
                        v_inst_prin_paid BIGINT;
                        v_inst_rem_total BIGINT;
                    BEGIN
                        SELECT * INTO v_loan FROM loans WHERE id = p_loan_id;
                        IF NOT FOUND OR v_loan.status NOT IN ('ACTIVE', 'PAID_OFF') THEN
                            RETURN;
                        END IF;

                        SELECT
                            COALESCE(SUM(amount_cents), 0),
                            COALESCE(SUM(interest_applied_cents), 0),
                            COALESCE(SUM(principal_applied_cents), 0)
                        INTO v_posted_amount, v_posted_interest, v_posted_principal
                        FROM loan_payments
                        WHERE loan_id = p_loan_id AND status = 'POSTED';

                        SELECT
                            COALESCE(SUM(amount_paid_cents), 0),
                            COALESCE(SUM(interest_paid_cents), 0),
                            COALESCE(SUM(principal_paid_cents), 0),
                            COALESCE(SUM(remaining_amount_cents), 0)
                        INTO v_inst_amt_paid, v_inst_int_paid, v_inst_prin_paid, v_inst_rem_total
                        FROM loan_installments
                        WHERE loan_id = p_loan_id;

                        IF v_loan.amount_paid_cents != v_posted_amount OR v_loan.amount_paid_cents != v_inst_amt_paid THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: amount_paid_cents mismatch', p_loan_id;
                        END IF;

                        IF (v_loan.total_interest_cents - v_loan.remaining_contractual_interest_cents) != v_posted_interest
                           OR (v_loan.total_interest_cents - v_loan.remaining_contractual_interest_cents) != v_inst_int_paid THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: interest paid mismatch', p_loan_id;
                        END IF;

                        IF (v_loan.principal_cents - v_loan.unpaid_principal_cents) != v_posted_principal
                           OR (v_loan.principal_cents - v_loan.unpaid_principal_cents) != v_inst_prin_paid THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: principal paid mismatch', p_loan_id;
                        END IF;

                        IF v_loan.remaining_contract_total_cents != v_inst_rem_total THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: remaining_contract_total_cents mismatch', p_loan_id;
                        END IF;

                        IF (v_loan.unpaid_principal_cents + v_loan.remaining_contractual_interest_cents) != v_loan.remaining_contract_total_cents THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: unpaid principal + remaining interest != remaining total', p_loan_id;
                        END IF;

                        IF (v_loan.amount_paid_cents + v_loan.remaining_contract_total_cents) != v_loan.total_repayment_cents THEN
                            RAISE EXCEPTION 'Ledger integrity violation on loan %: amount_paid + remaining_total != total_repayment', p_loan_id;
                        END IF;
                    END;
                    $fn$;
                """))
    except Exception as e:
        logger.error(f"Schema compatibility guard failed: {e}")
        raise

# --- RATE LIMITING, PHONE NORMALIZATION & ID HELPERS ---
_RATE_BUCKETS = defaultdict(list)


def get_crm_timezone():
    tz_name = os.getenv("CRM_TIMEZONE", "America/Los_Angeles")
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone(timedelta(hours=-7))


def get_local_day_bounds_utc() -> tuple[datetime, datetime, datetime, date]:
    crm_tz = get_crm_timezone()
    now_local = datetime.now(crm_tz)
    today_local = now_local.date()
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    upcoming_end_local = end_local + timedelta(days=7)
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        upcoming_end_local.astimezone(timezone.utc),
        today_local
    )


def parse_optional_datetime_utc(raw_value: Optional[str]) -> Optional[datetime]:
    if not raw_value or not str(raw_value).strip():
        return None
    s = str(raw_value).strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=get_crm_timezone())
        return dt.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid datetime format: {raw_value}")


def parse_pesos_to_cents(pesos_val: Optional[float], cents_val: Optional[int]) -> int:
    if cents_val is not None:
        if cents_val < 0:
            raise HTTPException(status_code=422, detail="Requested amount cannot be negative.")
        return int(cents_val)
    if pesos_val is not None:
        try:
            d = Decimal(str(pesos_val))
            if d < 0:
                raise HTTPException(status_code=422, detail="Requested amount cannot be negative.")
            return int((d * Decimal("100")).quantize(Decimal("1")))
        except InvalidOperation:
            raise HTTPException(status_code=422, detail="Invalid monetary amount.")
    return 0


_DEV_FALLBACK_HMAC_SECRET = secrets.token_hex(32)
_LAST_AUTH_CLEANUP_TS = 0.0


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


def check_rate_limit(bucket_key: str, max_attempts: int = 6, window_seconds: int = 300) -> bool:
    now = time.time()
    _RATE_BUCKETS[bucket_key] = [t for t in _RATE_BUCKETS[bucket_key] if now - t < window_seconds]
    if len(_RATE_BUCKETS[bucket_key]) >= max_attempts:
        return False
    _RATE_BUCKETS[bucket_key].append(now)
    return True


def mask_email_for_audit(raw_email: str) -> str:
    clean = (raw_email or "").strip().lower()
    if "@" not in clean:
        return "invalid_identifier"
    local, domain = clean.split("@", 1)
    masked_local = (local[:2] + "***") if len(local) > 2 else "***"
    return f"{masked_local}@{domain}"[:60]


def normalize_phone_number(raw_phone: Optional[str], required: bool = True) -> Optional[str]:
    if not raw_phone or not raw_phone.strip():
        if required:
            raise HTTPException(status_code=422, detail="Primary phone number is required.")
        return None
    digits = re.sub(r"\D", "", raw_phone)
    if len(digits) == 12 and digits.startswith("52"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("521"):
        digits = digits[3:]
    if len(digits) < 10 or len(digits) > 15:
        raise HTTPException(
            status_code=422,
            detail="Phone number must contain between 10 and 15 digits."
        )
    return digits


def generate_next_customer_number(db: Session) -> str:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(9002001)"))
    last_customer = db.query(CustomerModel.id).order_by(CustomerModel.id.desc()).first()
    next_seq = (last_customer[0] + 1) if last_customer else 1
    while True:
        candidate = f"CUST-{next_seq:05d}"
        exists = db.query(CustomerModel.id).filter(CustomerModel.customer_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1


def generate_next_lead_number(db: Session) -> str:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(9003001)"))
    last_lead = db.query(LeadModel.id).order_by(LeadModel.id.desc()).first()
    next_seq = (last_lead[0] + 1) if last_lead else 1
    while True:
        candidate = f"LD-{next_seq:05d}"
        exists = db.query(LeadModel.id).filter(LeadModel.lead_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1


def generate_next_loan_number(db: Session) -> str:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(9004001)"))
    last_loan = db.query(LoanModel.id).order_by(LoanModel.id.desc()).first()
    next_seq = (last_loan[0] + 1) if last_loan else 1
    while True:
        candidate = f"LN-{next_seq:05d}"
        exists = db.query(LoanModel.id).filter(LoanModel.loan_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1


def generate_next_payment_number(db: Session) -> str:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(9005001)"))
    last_payment = db.query(LoanPaymentModel.id).order_by(LoanPaymentModel.id.desc()).first()
    next_seq = (last_payment[0] + 1) if last_payment else 1
    while True:
        candidate = f"PAY-{next_seq:06d}"
        exists = db.query(LoanPaymentModel.id).filter(LoanPaymentModel.payment_number == candidate).first()
        if not exists:
            return candidate
        next_seq += 1

# --- REBATE DETAIL (PURE HELPERS) BEGIN ---
# El detalle del rebate por pronto pago vive en loan_payments.rebate_detail_json (se escribe
# en la misma transaccion que el pago). NO se lee de la bitacora de auditoria: alla cada valor
# se convierte a texto y se trunca a 255 caracteres.
def serialize_rebate_detail(rebated_map: dict) -> Optional[str]:
    if not rebated_map:
        return None
    clean = {}
    for inst_id, cents in rebated_map.items():
        inst_id_int = int(inst_id)
        cents_int = int(cents)
        if inst_id_int <= 0 or cents_int <= 0:
            raise ValueError("Invalid rebate detail entry.")
        clean[str(inst_id_int)] = cents_int
    return json.dumps({"v": 1, "rebated_installments": clean}, sort_keys=True, separators=(",", ":"))


def parse_rebate_detail(raw: Optional[str]) -> dict:
    # Devuelve {installment_id: centavos}. Si el dato esta corrupto lanza ValueError a proposito:
    # es preferible abortar la reversa que dejar el prestamo a medio restaurar.
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ValueError("Unsupported rebate detail format.")
    entries = data.get("rebated_installments")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("Invalid rebate detail payload.")
    out = {}
    for inst_id, cents in entries.items():
        inst_id_int = int(inst_id)
        cents_int = int(cents)
        if inst_id_int <= 0 or cents_int <= 0:
            raise ValueError("Invalid rebate detail entry.")
        out[inst_id_int] = cents_int
    return out
# --- REBATE DETAIL (PURE HELPERS) END ---


class LoanPaymentEngine:
    @staticmethod
    def _verify_ledger_integrity_python(loan: LoanModel, installments: List[LoanInstallmentModel], db: Session):
        posted_payments = db.query(LoanPaymentModel).filter(
            LoanPaymentModel.loan_id == loan.id, LoanPaymentModel.status == 'POSTED'
        ).all()
        
        posted_amt = sum(p.amount_cents for p in posted_payments)
        inst_amt = sum(i.amount_paid_cents for i in installments)
        if loan.amount_paid_cents != posted_amt or loan.amount_paid_cents != inst_amt:
            raise RuntimeError(f"Python Ledger Violation: amount_paid_cents mismatch. Loan={loan.amount_paid_cents}, Posted={posted_amt}, Inst={inst_amt}")
        
        posted_int = sum(p.interest_applied_cents for p in posted_payments)
        inst_int = sum(i.interest_paid_cents for i in installments)
        if (loan.total_interest_cents - loan.remaining_contractual_interest_cents) != posted_int or posted_int != inst_int:
            raise RuntimeError("Python Ledger Violation: interest_paid mismatch.")
            
        posted_prin = sum(p.principal_applied_cents for p in posted_payments)
        inst_prin = sum(i.principal_paid_cents for i in installments)
        if (loan.principal_cents - loan.unpaid_principal_cents) != posted_prin or posted_prin != inst_prin:
            raise RuntimeError("Python Ledger Violation: principal_paid mismatch.")
            
        rem_total = sum(i.remaining_amount_cents for i in installments)
        if loan.remaining_contract_total_cents != rem_total:
            raise RuntimeError("Python Ledger Violation: remaining_contract_total_cents mismatch.")

    @staticmethod
    def execute_waterfall_allocation(db: Session, loan: LoanModel, installments: List[LoanInstallmentModel], amount_cents: int, payment_method: str, payment_reference: str, paid_at_dt: datetime, notes: str, user_id: int, idempotency_key: str, payload_hash: str, rebate_detail_json: Optional[str] = None) -> dict:
        if amount_cents <= 0 or amount_cents > loan.remaining_contract_total_cents:
            raise ValueError("Payment amount must be > 0 and cannot exceed the remaining contract total.")

        payment_number = generate_next_payment_number(db)
        max_seq = db.query(func.max(LoanPaymentModel.loan_payment_seq)).filter(LoanPaymentModel.loan_id == loan.id).scalar()
        next_seq = (max_seq or 0) + 1

        prev_loan_status = loan.status
        prev_unpaid_principal = int(loan.unpaid_principal_cents)
        prev_remaining_interest = int(loan.remaining_contractual_interest_cents)
        prev_remaining_total = int(loan.remaining_contract_total_cents)
        prev_amount_paid = int(loan.amount_paid_cents)

        remaining_to_allocate = int(amount_cents)
        interest_applied = 0
        principal_applied = 0
        pending_allocations = []

        for inst in installments:
            if remaining_to_allocate <= 0:
                break
            if inst.remaining_amount_cents <= 0:
                continue

            prev_prin_paid = int(inst.principal_paid_cents)
            prev_int_paid = int(inst.interest_paid_cents)
            prev_amt_paid = int(inst.amount_paid_cents)
            prev_rem_amt = int(inst.remaining_amount_cents)
            prev_paid_at = inst.paid_at

            int_pending = max(0, int(inst.interest_due_cents) - prev_int_paid)
            int_alloc = min(remaining_to_allocate, int_pending)
            if int_alloc > 0:
                inst.interest_paid_cents = prev_int_paid + int_alloc
                remaining_to_allocate -= int_alloc
                interest_applied += int_alloc

            prin_pending = max(0, int(inst.principal_due_cents) - prev_prin_paid)
            prin_alloc = min(remaining_to_allocate, prin_pending)
            if prin_alloc > 0:
                inst.principal_paid_cents = prev_prin_paid + prin_alloc
                remaining_to_allocate -= prin_alloc
                principal_applied += prin_alloc

            total_alloc = int_alloc + prin_alloc
            if total_alloc > 0:
                inst.amount_paid_cents = prev_amt_paid + total_alloc
                inst.remaining_amount_cents = int(inst.scheduled_payment_cents) - int(inst.amount_paid_cents)
                if inst.remaining_amount_cents == 0:
                    inst.paid_at = paid_at_dt

                pending_allocations.append({
                    "installment_id": inst.id,
                    "installment_number": inst.installment_number,
                    "interest_allocated_cents": int_alloc,
                    "principal_allocated_cents": prin_alloc,
                    "total_allocated_cents": total_alloc,
                    "prev_principal_paid_cents": prev_prin_paid,
                    "prev_interest_paid_cents": prev_int_paid,
                    "prev_amount_paid_cents": prev_amt_paid,
                    "prev_remaining_amount_cents": prev_rem_amt,
                    "prev_paid_at": prev_paid_at
                })

        if remaining_to_allocate != 0 or (interest_applied + principal_applied) != amount_cents:
            raise RuntimeError("Waterfall allocation mismatch: unable to allocate exact payment amount across installments.")

        payment = LoanPaymentModel(
            payment_number=payment_number,
            loan_id=loan.id,
            customer_id=loan.customer_id,
            loan_payment_seq=next_seq,
            idempotency_key=idempotency_key,
            request_payload_hash=payload_hash,
            status="POSTED",
            currency="MXN",
            amount_cents=int(amount_cents),
            interest_applied_cents=int(interest_applied),
            principal_applied_cents=int(principal_applied),
            prev_loan_status=prev_loan_status,
            prev_unpaid_principal_cents=prev_unpaid_principal,
            prev_remaining_interest_cents=prev_remaining_interest,
            prev_remaining_total_cents=prev_remaining_total,
            prev_amount_paid_cents=prev_amount_paid,
            payment_method=payment_method,
            payment_reference=payment_reference,
            paid_at=paid_at_dt,
            notes=notes,
            rebate_detail_json=rebate_detail_json,
            recorded_by_user_id=user_id
        )
        db.add(payment)
        db.flush()

        for alloc_data in pending_allocations:
            db.add(LoanPaymentAllocationModel(
                payment_id=payment.id,
                loan_id=loan.id,
                **alloc_data
            ))

        loan.unpaid_principal_cents = prev_unpaid_principal - principal_applied
        loan.remaining_contractual_interest_cents = prev_remaining_interest - interest_applied
        loan.remaining_contract_total_cents = prev_remaining_total - int(amount_cents)
        loan.amount_paid_cents = prev_amount_paid + int(amount_cents)

        if loan.remaining_contract_total_cents == 0:
            loan.status = "PAID_OFF"
        loan.updated_at = datetime.now(timezone.utc)

        db.flush()
        if db.bind and db.bind.dialect.name == "sqlite":
            LoanPaymentEngine._verify_ledger_integrity_python(loan, installments, db)

        return {"payment": payment, "allocations_count": len(pending_allocations)}

    @staticmethod
    def execute_reversal_restoration(db: Session, loan: LoanModel, payment: LoanPaymentModel, reason: str, user_id: int):
        now_utc = datetime.now(timezone.utc)
        
        for alloc in payment.allocations:
            inst = db.query(LoanInstallmentModel).filter(LoanInstallmentModel.id == alloc.installment_id).with_for_update().first()
            if not inst:
                raise ValueError(f"Data corruption: Installment {alloc.installment_id} not found.")
                
            inst.principal_paid_cents = alloc.prev_principal_paid_cents
            inst.interest_paid_cents = alloc.prev_interest_paid_cents
            inst.amount_paid_cents = alloc.prev_amount_paid_cents
            inst.remaining_amount_cents = alloc.prev_remaining_amount_cents
            inst.paid_at = alloc.prev_paid_at

        payment.status = "REVERSED"
        payment.reversed_by_user_id = user_id
        payment.reversed_at = now_utc
        payment.reversal_reason = reason

        loan.status = payment.prev_loan_status
        loan.unpaid_principal_cents = payment.prev_unpaid_principal_cents
        loan.remaining_contractual_interest_cents = payment.prev_remaining_interest_cents
        loan.remaining_contract_total_cents = payment.prev_remaining_total_cents
        loan.amount_paid_cents = payment.prev_amount_paid_cents

        db.flush()
        
        installments = db.query(LoanInstallmentModel).filter(LoanInstallmentModel.loan_id == loan.id).all()
        if db.bind and db.bind.dialect.name == "sqlite":
            LoanPaymentEngine._verify_ledger_integrity_python(loan, installments, db)


# --- AUDIT SERVICE ---
SENSITIVE_AUDIT_KEYS = {
    "password", "new_password", "password_hash", "token", "reset_token",
    "token_hash", "session_token", "csrf_token", "secret", "bootstrap_secret",
    "rate_limit_hmac_secret", "api_key", "email", "raw_email",
    "phone", "phone_primary", "phone_secondary", "address_full",
    "tax_id_rfc", "date_of_birth", "full_legal_name", "full_name",
    "first_name", "middle_name", "last_name",
    "content", "summary_notes", "reason_or_note", "approval_notes", "disbursement_notes"
}


def record_audit_event(
    db: Session,
    event_type: str,
    resource_type: str,
    outcome: str,
    actor: Optional[UserModel] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    details: Optional[dict] = None,
    commit: bool = True
):
    sanitized_details = {}
    if details:
        for k, v in details.items():
            if str(k).lower() in SENSITIVE_AUDIT_KEYS:
                continue
            sanitized_details[str(k)] = str(v)[:255]

    entry = AuditLogModel(
        actor_user_id=actor.id if actor else None,
        actor_role=actor.role if actor else None,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=str(resource_id)[:60] if resource_id is not None else None,
        outcome=outcome,
        ip_address=ip_address,
        details_json=json.dumps(sanitized_details) if sanitized_details else None
    )

    if not commit:
        db.add(entry)
        db.flush()
        return

    try:
        db.add(entry)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Audit log standalone write failed: {type(e).__name__}")


# --- LIFESPAN & FASTAPI INIT ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    verify_database_schema_compatibility(engine)
    yield

app = FastAPI(title="NURA CRM | Credit & Pipeline System", version="7.4.0", lifespan=lifespan)

if os.getenv("GEMINI_API_KEY"):
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- PYDANTIC SCHEMAS ---
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: str = Field(default="SALES_AGENT")  # minimo privilegio por defecto


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=128)


class UserUpdateByAdmin(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserLanguageUpdateRequest(BaseModel):
    preferred_language: str = Field(..., min_length=2, max_length=10)


class UserChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class BootstrapAdminRequest(BaseModel):
    secret: str = Field(..., min_length=12)


class ChatRequest(BaseModel):
    mensaje: str = Field(..., min_length=1, max_length=1500)
    conversation_id: Optional[int] = None


class CustomerCreateRequest(BaseModel):
    entity_type: str = Field(default="INDIVIDUAL")
    status: str = Field(default="ACTIVE")
    verification_status: str = Field(default="PENDING_VERIFICATION")
    # INDIVIDUAL: first_name y last_name obligatorios, middle_name opcional.  BUSINESS: full_legal_name (razon social) obligatorio.
    first_name: Optional[str] = Field(default=None, max_length=50)
    middle_name: Optional[str] = Field(default=None, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=75)
    full_legal_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    date_of_birth: Optional[date] = None
    phone_primary: str = Field(..., min_length=10, max_length=25)
    phone_secondary: Optional[str] = Field(default=None, max_length=25)
    email: Optional[EmailStr] = None
    address_full: Optional[str] = Field(default=None, max_length=1000)
    employment_status: Optional[str] = Field(default=None, max_length=60)
    employer_or_business_name: Optional[str] = Field(default=None, max_length=160)
    occupation_or_industry: Optional[str] = Field(default=None, max_length=120)
    employment_address: Optional[str] = Field(default=None, max_length=1000)
    owner_user_id: Optional[int] = None


class CustomerUpdateRequest(BaseModel):
    entity_type: Optional[str] = None
    status: Optional[str] = None
    verification_status: Optional[str] = None
    first_name: Optional[str] = Field(default=None, max_length=50)
    middle_name: Optional[str] = Field(default=None, max_length=50)
    last_name: Optional[str] = Field(default=None, max_length=75)
    full_legal_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    date_of_birth: Optional[date] = None
    phone_primary: Optional[str] = Field(default=None, min_length=10, max_length=25)
    phone_secondary: Optional[str] = Field(default=None, max_length=25)
    email: Optional[EmailStr] = None
    address_full: Optional[str] = Field(default=None, max_length=1000)
    employment_status: Optional[str] = Field(default=None, max_length=60)
    employer_or_business_name: Optional[str] = Field(default=None, max_length=160)
    occupation_or_industry: Optional[str] = Field(default=None, max_length=120)
    employment_address: Optional[str] = Field(default=None, max_length=1000)
    owner_user_id: Optional[int] = None


class LeadCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=3, max_length=180)
    entity_type: str = Field(default="INDIVIDUAL")
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    phone_primary: str = Field(..., min_length=10, max_length=25)
    email: Optional[EmailStr] = None
    source: str = Field(default="DIRECT_OUTREACH")
    product_interest: str = Field(default="Personal Loan")
    requested_amount_pesos: Optional[float] = Field(default=None, ge=0)
    requested_amount_cents: Optional[int] = Field(default=None, ge=0)
    stage: str = Field(default="NEW")
    priority: str = Field(default="MEDIUM")
    next_follow_up_at: Optional[str] = None
    summary_notes: Optional[str] = Field(default=None, max_length=2000)
    owner_user_id: Optional[int] = None


class LeadUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=3, max_length=180)
    entity_type: Optional[str] = None
    trade_name: Optional[str] = Field(default=None, max_length=160)
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    phone_primary: Optional[str] = Field(default=None, min_length=10, max_length=25)
    email: Optional[EmailStr] = None
    source: Optional[str] = None
    product_interest: Optional[str] = None
    requested_amount_pesos: Optional[float] = Field(default=None, ge=0)
    requested_amount_cents: Optional[int] = Field(default=None, ge=0)
    stage: Optional[str] = None
    priority: Optional[str] = None
    lost_reason: Optional[str] = Field(default=None, max_length=160)
    next_follow_up_at: Optional[str] = None
    clear_next_follow_up: Optional[bool] = False
    summary_notes: Optional[str] = Field(default=None, max_length=2000)
    stage_change_note: Optional[str] = Field(default=None, max_length=500)
    owner_user_id: Optional[int] = None


class InteractionCreateRequest(BaseModel):
    interaction_type: str = Field(default="CALL")
    direction: str = Field(default="OUTBOUND")
    visibility: str = Field(default="INTERNAL")
    outcome: Optional[str] = None
    content: str = Field(..., min_length=2, max_length=2500)
    next_follow_up_at: Optional[str] = None
    clear_next_follow_up: Optional[bool] = False


class LeadConvertRequest(BaseModel):
    entity_type: Optional[str] = None
    legal_representative_name: Optional[str] = Field(default=None, max_length=160)
    trade_name: Optional[str] = Field(default=None, max_length=160)
    tax_id_rfc: Optional[str] = Field(default=None, max_length=20)
    address_full: Optional[str] = Field(default=None, max_length=1000)
    notes: Optional[str] = Field(default=None, max_length=500)


class LoanCreateRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=10, max_length=64)
    customer_id: int
    source_lead_id: Optional[int] = None
    product_name: str = Field(default="Personal Loan")
    principal_pesos: Optional[float] = Field(default=None, gt=0, le=10000000)
    principal_cents: Optional[int] = Field(default=None, gt=0, le=1000000000)
    monthly_interest_rate_percent: float = Field(default=10.0, ge=0, le=100.0)
    assumed_interest_months: int = Field(default=4, ge=1, le=36)
    term_installments_count: int = Field(default=16, ge=1, le=104)
    start_date: str  # YYYY-MM-DD
    first_payment_date: str  # YYYY-MM-DD
    notes: Optional[str] = Field(default=None, max_length=1000)
    interest_calculation_method: str = Field(default="FLAT_ORIGINAL_PRINCIPAL")


class LoanApproveRequest(BaseModel):
    approval_notes: Optional[str] = Field(default=None, max_length=1000)


class LoanActivateRequest(BaseModel):
    confirm_disbursement: bool = Field(...)
    disbursement_method: str = Field(...)
    disbursement_reference: str = Field(..., min_length=3, max_length=120)
    disbursed_at: Optional[str] = None  # ISO datetime
    disbursement_notes: Optional[str] = Field(default=None, max_length=1000)


class LoanCancelRequest(BaseModel):
    cancellation_reason: str = Field(..., min_length=3, max_length=255)


class LoanPaymentCreateRequest(BaseModel):
    idempotency_key: str = Field(..., min_length=10, max_length=64)
    amount_pesos: Optional[float] = Field(default=None, gt=0)
    amount_cents: Optional[int] = Field(default=None, gt=0)
    payment_method: str = Field(...)
    payment_reference: Optional[str] = Field(default=None, max_length=120)
    paid_at: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=1000)
    apply_early_payoff_rebate: bool = Field(default=False)

class LoanPaymentReverseRequest(BaseModel):
    reversal_reason: str = Field(..., min_length=3, max_length=255)


class LoanDefaultRequest(BaseModel):
    confirm_default: bool = Field(...)
    default_reason: str = Field(..., min_length=5, max_length=500)


class LoanCollectionLogRequest(BaseModel):
    interaction_type: str = Field(default="CALL")
    outcome: str = Field(default="FOLLOW_UP_REQUIRED")
    promised_amount_pesos: Optional[float] = Field(default=None, ge=0)
    next_follow_up_at: Optional[str] = None
    notes: str = Field(..., min_length=3, max_length=2000)


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
            from urllib.parse import urlparse
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

    return user


def require_roles(*allowed_roles: str):
    def role_checker(
        request: Request,
        user: UserModel = Depends(get_current_user),
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


# --- HEALTH CHECK & SCHEMA STATUS ENDPOINTS ---
@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected", "version": "7.4.0"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database unhealthy: {type(e).__name__}")


@app.get("/api/admin/schema-status")
def schema_status(
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    rev = db.execute(text("SELECT version_num FROM alembic_version")).fetchone()
    inspector = inspect(db.bind)
    tables = inspector.get_table_names()
    return {
        "alembic_version": rev[0] if rev else None,
        "tables_verified": sorted(tables),
        "status": "OK"
    }


# --- DECOUPLED AI SERVICE (NURA ASSISTANT) ---
class AIService:
    @staticmethod
    def generate_reply(user_message: str, prior_messages: List[ChatMessageModel], user_role: str) -> str:
        if not os.getenv("GEMINI_API_KEY"):
            return "⚠️ Nura Assistant is temporarily unavailable (API key not configured)."

        model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        system_rules = (
            "You are NURA, the internal intelligent assistant for a credit & pipeline CRM team. "
            f"Current authenticated team member role: {user_role}. "
            "Supported credit products: Personal Loan, Business Loan.\n"
            "STRICT SECURITY RULES:\n"
            "1. You operate strictly in advisory and read-only mode. You cannot approve loans, "
            "disburse money, modify balances, or execute financial transactions.\n"
            "2. Never invent customer balances, payments, or approval confirmations.\n"
            "3. Respond clearly and professionally in the same language used by the user."
        )

        try:
            structured_contents = []
            for m in prior_messages[-10:]:
                mapped_role = "user" if m.role == "user" else "model"
                structured_contents.append({"role": mapped_role, "parts": [m.content[:2000]]})

            structured_contents.append({"role": "user", "parts": [user_message.strip()]})

            try:
                model = genai.GenerativeModel(model_name, system_instruction=system_rules)
                res = model.generate_content(structured_contents)
            except TypeError:
                model = genai.GenerativeModel(model_name)
                fallback_prompt = f"[SYSTEM INSTRUCTIONS]\n{system_rules}\n\n[CONVERSATION HISTORY]\n"
                for m in prior_messages[-10:]:
                    fallback_prompt += f"{m.role}: {m.content[:1500]}\n"
                fallback_prompt += f"user: {user_message.strip()}\n"
                res = model.generate_content(fallback_prompt)

            text_out = res.text.strip() if res and getattr(res, "text", None) else "No response received."
            return text_out
        except Exception as e:
            logger.error(f"Gemini provider error: {type(e).__name__}")
            return "⚠️ Nura Assistant is busy right now. Your message has been saved."


# --- TRANSACTIONAL EMAIL DELIVERY INTERFACE (PROVIDER-AGNOSTIC & PROD-SAFE) ---
GENERIC_PASSWORD_RESET_MESSAGE = (
    "If an active account is associated with that email address, "
    "password reset instructions have been sent."
)


class EmailDeliveryService:
    """
    Provider-agnostic email delivery interface.
    STRICT SECURITY POLICY:
    - In production (IS_PRODUCTION == True), raw tokens, reset links, or secrets
      are NEVER written to logs under any circumstance.
    - If no real transactional email provider is configured in production,
      delivery fails safely (returns False) without faking success.
    """
    @staticmethod
    def send_password_reset_email(recipient_email: str, raw_token: str, request: Request) -> bool:
        provider = os.getenv("EMAIL_PROVIDER", "NONE").strip().upper()
        base_url = os.getenv("PASSWORD_RESET_BASE_URL", "").strip().rstrip("/")
        if not base_url:
            base_url = f"{request.url.scheme}://{request.url.netloc}"

        reset_link = f"{base_url}/?reset_token={raw_token}"

        if IS_PRODUCTION:
            # Mock/console logging is strictly forbidden in production
            if provider in ("", "NONE", "DEV_CONSOLE", "MOCK"):
                logger.error("Password reset email delivery aborted: EMAIL_PROVIDER is not configured in production.")
                return False
            # Hook for authorized external transactional provider (e.g., Resend / SendGrid / SES)
            logger.error(f"Password reset email delivery aborted: provider '{provider}' adapter not yet active.")
            return False

        # Local / development environment only (IS_PRODUCTION == False)
        if provider in ("", "NONE", "DEV_CONSOLE", "MOCK"):
            logger.info(
                "[DEV ONLY - LOCAL PASSWORD RESET] Account=%s | Reset Link: %s",
                mask_email_for_audit(recipient_email),
                reset_link
            )
            return True

        return False


# --- AUTHENTICATION & ATOMIC BOOTSTRAP ENDPOINTS ---
@app.post("/api/auth/login")
def login(user_data: UserLogin, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    email_clean = user_data.email.strip().lower()
    masked_email = mask_email_for_audit(email_clean)
    now_utc = datetime.now(timezone.utc)

    try:
        email_bucket, prev_email_bucket = compute_email_hmac_buckets("LOGIN_EMAIL", email_clean)
    except RuntimeError:
        record_audit_event(
            db, "AUTH_CONFIG_MISSING_HMAC_SECRET", "security", "ERROR",
            resource_id="LOGIN", ip_address=ip, commit=True
        )
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    ip_bucket = f"LOGIN_IP:{ip}"

    ip_allowed, ip_retry = consume_rate_limit_atomic(
        db, bucket_key=ip_bucket, max_attempts=5, window_seconds=60, now_utc=now_utc
    )
    email_allowed, email_retry = consume_rate_limit_atomic(
        db, bucket_key=email_bucket, max_attempts=5, window_seconds=300,
        previous_bucket_key=prev_email_bucket, now_utc=now_utc
    )

    if not ip_allowed or not email_allowed:
        retry_after = max(ip_retry, email_retry, 1)
        limit_scope = "IP_AND_EMAIL" if (not ip_allowed and not email_allowed) else ("IP" if not ip_allowed else "EMAIL")
        record_audit_event(
            db, "LOGIN_RATE_LIMITED", "auth", "DENIED",
            resource_id=masked_email, ip_address=ip,
            details={"scope": limit_scope, "retry_after_sec": retry_after}, commit=True
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Please wait {retry_after} seconds before trying again.",
            headers={"Retry-After": str(retry_after)}
        )

    user = db.query(UserModel).filter(UserModel.email == email_clean).first()

    # Constant-time bcrypt execution regardless of whether the email exists
    target_hash = user.password_hash if user else DUMMY_BCRYPT_HASH
    password_ok = pwd_context.verify(user_data.password, target_hash)

    if not user or not password_ok:
        record_audit_event(
            db, "LOGIN_FAILED", "auth", "ERROR",
            resource_id=masked_email, ip_address=ip, commit=True
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        record_audit_event(
            db, "LOGIN_BLOCKED_INACTIVE", "auth", "DENIED",
            actor=user, resource_id=str(user.id), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Clear email failure counter upon successful authentication
    reset_rate_limit_bucket(db, email_bucket, prev_email_bucket)

    token = secrets.token_hex(32)
    csrf_token = secrets.token_urlsafe(32)
    expires = now_utc + timedelta(days=7)

    db.add(SessionModel(token=hash_token(token), user_id=user.id, expires_at=expires))
    db.commit()

    cookie_secure = os.getenv("COOKIE_SECURE", str(IS_PRODUCTION)).lower() == "true"
    response.set_cookie(
        key="session_token", value=token, httponly=True,
        secure=cookie_secure, samesite="lax", max_age=604800, path="/"
    )
    response.set_cookie(
        key="csrf_token", value=csrf_token, httponly=False,
        secure=cookie_secure, samesite="lax", max_age=604800, path="/"
    )

    record_audit_event(
        db, "LOGIN_SUCCESS", "auth", "SUCCESS",
        actor=user, resource_id=str(user.id), ip_address=ip, commit=True
    )
    return {
        "message": "Login successful",
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "preferred_language": user.preferred_language if user.preferred_language in VALID_LANGUAGES else "en"
    }


@app.post("/api/auth/password-reset/request")
def request_password_reset(
    payload: PasswordResetRequest,
    request: Request,
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    email_clean = payload.email.strip().lower()
    masked_email = mask_email_for_audit(email_clean)
    now_utc = datetime.now(timezone.utc)

    try:
        email_bucket, prev_email_bucket = compute_email_hmac_buckets("PASSWORD_RESET_EMAIL", email_clean)
    except RuntimeError:
        record_audit_event(
            db, "AUTH_CONFIG_MISSING_HMAC_SECRET", "security", "ERROR",
            resource_id="PASSWORD_RESET_REQUEST", ip_address=ip, commit=True
        )
        return {"message": GENERIC_PASSWORD_RESET_MESSAGE}

    ip_bucket = f"PASSWORD_RESET_IP:{ip}"

    ip_allowed, ip_retry = consume_rate_limit_atomic(
        db, bucket_key=ip_bucket, max_attempts=5, window_seconds=300, now_utc=now_utc
    )
    email_allowed, email_retry = consume_rate_limit_atomic(
        db, bucket_key=email_bucket, max_attempts=3, window_seconds=900,
        previous_bucket_key=prev_email_bucket, now_utc=now_utc
    )

    if not ip_allowed or not email_allowed:
        retry_after = max(ip_retry, email_retry, 1)
        limit_scope = "IP_AND_EMAIL" if (not ip_allowed and not email_allowed) else ("IP" if not ip_allowed else "EMAIL")
        record_audit_event(
            db, "PASSWORD_RESET_RATE_LIMITED", "auth", "DENIED",
            resource_id=masked_email, ip_address=ip,
            details={"scope": limit_scope, "retry_after_sec": retry_after}, commit=True
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many password recovery requests. Please wait {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )

    if not is_auth_security_schema_ready(db):
        record_audit_event(
            db, "PASSWORD_RESET_SCHEMA_NOT_READY", "auth", "ERROR",
            resource_id=masked_email, ip_address=ip, commit=True
        )
        return {"message": GENERIC_PASSWORD_RESET_MESSAGE}

    try:
        # Serialize token issuance per user by locking the UserModel row first
        user = (
            db.query(UserModel)
            .filter(UserModel.email == email_clean)
            .with_for_update()
            .first()
        )

        if not user or not user.is_active:
            # Perform dummy token hash calculation to keep response timing consistent
            _ = hash_token(secrets.token_urlsafe(32))
            record_audit_event(
                db, "PASSWORD_RESET_REQUEST_IGNORED", "auth", "SUCCESS",
                resource_id=masked_email, ip_address=ip, commit=True
            )
            return {"message": GENERIC_PASSWORD_RESET_MESSAGE}

        # Invalidate ALL prior unconsumed tokens for this user (including expired ones)
        # so uq_password_reset_tokens_active_user (WHERE used_at IS NULL) is always freed.
        db.execute(
            text("""
                UPDATE password_reset_tokens
                SET used_at = :now_utc
                WHERE user_id = :user_id
                  AND used_at IS NULL
            """),
            {"now_utc": now_utc, "user_id": user.id}
        )

        raw_token = secrets.token_urlsafe(32)
        token_hash_val = hash_token(raw_token)
        expires_at = now_utc + timedelta(minutes=30)

        reset_record = PasswordResetTokenModel(
            user_id=user.id,
            token_hash=token_hash_val,
            created_at=now_utc,
            expires_at=expires_at,
            used_at=None,
            request_ip=ip
        )
        db.add(reset_record)
        db.flush()

        delivered = EmailDeliveryService.send_password_reset_email(
            recipient_email=user.email,
            raw_token=raw_token,
            request=request
        )
        if not delivered:
            db.rollback()
            record_audit_event(
                db, "PASSWORD_RESET_DELIVERY_FAILED", "auth", "ERROR",
                actor=user, resource_id=str(user.id), ip_address=ip, commit=True
            )
            return {"message": GENERIC_PASSWORD_RESET_MESSAGE}

        record_audit_event(
            db, "PASSWORD_RESET_REQUESTED", "auth", "SUCCESS",
            actor=user, resource_id=str(user.id), ip_address=ip,
            details={"expires_in_minutes": 30}, commit=False
        )
        db.commit()
        return {"message": GENERIC_PASSWORD_RESET_MESSAGE}
    except Exception as e:
        db.rollback()
        logger.error(f"Password reset request failed: {type(e).__name__}")
        return {"message": GENERIC_PASSWORD_RESET_MESSAGE}


@app.post("/api/auth/password-reset/confirm")
def confirm_password_reset(
    payload: PasswordResetConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    now_utc = datetime.now(timezone.utc)

    confirm_ip_bucket = f"PASSWORD_RESET_CONFIRM_IP:{ip}"
    ip_allowed, ip_retry = consume_rate_limit_atomic(
        db, bucket_key=confirm_ip_bucket, max_attempts=5, window_seconds=300, now_utc=now_utc
    )
    if not ip_allowed:
        record_audit_event(
            db, "PASSWORD_RESET_CONFIRM_RATE_LIMITED", "auth", "DENIED",
            ip_address=ip, details={"retry_after_sec": ip_retry}, commit=True
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many password reset attempts. Please wait {ip_retry} seconds.",
            headers={"Retry-After": str(ip_retry)}
        )

    if not is_auth_security_schema_ready(db):
        raise HTTPException(status_code=503, detail="Password recovery service is not active yet.")

    new_pw = payload.new_password
    if len(new_pw.strip()) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters long.")

    raw_token = payload.token.strip()
    token_hash_val = hash_token(raw_token)

    try:
        # Atomic single-use token consumption (works identically in PostgreSQL & SQLite)
        consume_res = db.execute(
            text("""
                UPDATE password_reset_tokens
                SET used_at = :now_utc
                WHERE token_hash = :token_hash
                  AND used_at IS NULL
                  AND expires_at > :now_utc
            """),
            {"now_utc": now_utc, "token_hash": token_hash_val}
        )

        if consume_res.rowcount != 1:
            db.rollback()
            record_audit_event(
                db, "PASSWORD_RESET_CONFIRM_INVALID", "auth", "DENIED",
                ip_address=ip, commit=True
            )
            raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")

        token_record = (
            db.query(PasswordResetTokenModel)
            .filter(PasswordResetTokenModel.token_hash == token_hash_val)
            .first()
        )
        user = (
            db.query(UserModel)
            .filter(UserModel.id == token_record.user_id)
            .with_for_update()
            .first()
            if token_record else None
        )

        if not user or not user.is_active:
            db.rollback()
            record_audit_event(
                db, "PASSWORD_RESET_CONFIRM_INACTIVE_USER", "auth", "DENIED",
                ip_address=ip, commit=True
            )
            raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")

        # Update password hash using NURA's authoritative bcrypt context
        user.password_hash = pwd_context.hash(new_pw)

        # Revoke all prior active sessions for this user in PostgreSQL
        revoked_sessions = (
            db.query(SessionModel)
            .filter(SessionModel.user_id == user.id)
            .delete(synchronize_session=False)
        )

        record_audit_event(
            db, "PASSWORD_RESET_COMPLETED", "auth", "SUCCESS",
            actor=user, resource_id=str(user.id), ip_address=ip,
            details={"sessions_revoked": revoked_sessions}, commit=False
        )
        db.commit()

        # Clear any active login rate limit bucket for this user's email
        try:
            login_email_bucket, prev_login_bucket = compute_email_hmac_buckets("LOGIN_EMAIL", user.email)
            reset_rate_limit_bucket(db, login_email_bucket, prev_login_bucket)
        except Exception:
            pass

        response.delete_cookie(key="session_token", path="/")
        response.delete_cookie(key="csrf_token", path="/")

        return {"message": "Password has been updated successfully. Please sign in with your new password."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Password reset confirmation error: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not complete password reset.")


@app.post("/api/auth/bootstrap-admin", dependencies=[Depends(verify_csrf)])
def bootstrap_admin(
    payload: BootstrapAdminRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)

    if not check_rate_limit(f"bootstrap:{ip}", max_attempts=5, window_seconds=600):
        record_audit_event(
            db, "BOOTSTRAP_RATE_LIMITED", "admin", "DENIED",
            actor=current_user, ip_address=ip, commit=True
        )
        raise HTTPException(status_code=429, detail="Too many bootstrap attempts.")

    expected_email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    expected_secret = os.getenv("BOOTSTRAP_ADMIN_SECRET", "").strip()

    if not expected_email or not expected_secret:
        record_audit_event(
            db, "BOOTSTRAP_NOT_CONFIGURED", "admin", "DENIED",
            actor=current_user, ip_address=ip, commit=True
        )
        raise HTTPException(status_code=403, detail="Bootstrap is disabled in environment.")

    if (
        current_user.email.strip().lower() != expected_email
        or not secrets.compare_digest(payload.secret, expected_secret)
    ):
        record_audit_event(
            db, "BOOTSTRAP_INVALID_CREDENTIALS", "admin", "DENIED",
            actor=current_user, ip_address=ip, commit=True
        )
        raise HTTPException(status_code=403, detail="Invalid bootstrap credentials.")

    try:
        if db.bind and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(9001001)"))

        active_owners = (
            db.query(UserModel.id)
            .filter(UserModel.role == "OWNER_ADMIN", UserModel.is_active.is_(True))
            .first()
        )

        already_bootstrapped = (
            db.query(AuditLogModel.id)
            .filter(AuditLogModel.event_type == "BOOTSTRAP_ADMIN_SUCCESS")
            .first()
        )

        if active_owners is not None or already_bootstrapped is not None:
            db.rollback()
            record_audit_event(
                db, "BOOTSTRAP_ALREADY_USED", "admin", "DENIED",
                actor=current_user, ip_address=ip, commit=True
            )
            raise HTTPException(
                status_code=403,
                detail="Initial Owner Admin has already been initialized."
            )

        current_user.role = "OWNER_ADMIN"
        current_user.is_active = True

        record_audit_event(
            db, "BOOTSTRAP_ADMIN_SUCCESS", "admin", "SUCCESS",
            actor=current_user, resource_id=str(current_user.id),
            ip_address=ip, details={"promoted_role": "OWNER_ADMIN"}, commit=False
        )
        db.commit()
        return {
            "message": "Account promoted to OWNER_ADMIN",
            "email": current_user.email,
            "role": current_user.role
        }
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        logger.exception("Bootstrap failed with an unexpected error")
        raise HTTPException(status_code=500, detail="Could not complete bootstrap.")


@app.post("/api/auth/logout", dependencies=[Depends(verify_csrf)])
def logout(
    request: Request,
    response: Response,
    session_token: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    if session_token:
        token_h = hash_token(session_token)
        s = db.query(SessionModel).filter(SessionModel.token == token_h).first()
        if s:
            u = db.query(UserModel).filter(UserModel.id == s.user_id).first()
            record_audit_event(
                db, "LOGOUT", "auth", "SUCCESS",
                actor=u, ip_address=get_client_ip(request), commit=True
            )
            db.delete(s)
            db.commit()

    response.delete_cookie(key="session_token", path="/")
    response.delete_cookie(key="csrf_token", path="/")
    return {"message": "Logged out"}


@app.get("/api/auth/me")
def get_me(current_user: UserModel = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "preferred_language": current_user.preferred_language if current_user.preferred_language in VALID_LANGUAGES else "en",
        "is_active": current_user.is_active,
        "supported_products": SUPPORTED_PRODUCTS
    }


@app.patch("/api/auth/me/language", dependencies=[Depends(verify_csrf)])
def update_my_language(
    payload: UserLanguageUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    lang = (payload.preferred_language or "").strip().lower()
    if lang not in VALID_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail="Invalid preferred_language. Allowed values: en, es."
        )

    try:
        target_user = (
            db.query(UserModel)
            .filter(UserModel.id == current_user.id)
            .with_for_update()
            .first()
        )
        if not target_user or not target_user.is_active:
            raise HTTPException(status_code=403, detail="Account has been deactivated.")

        previous_lang = target_user.preferred_language
        target_user.preferred_language = lang

        record_audit_event(
            db, "USER_LANGUAGE_UPDATED", "user", "SUCCESS",
            actor=target_user, resource_id=str(target_user.id), ip_address=ip,
            details={"from_language": previous_lang, "to_language": lang},
            commit=False
        )
        db.commit()
        db.refresh(target_user)
        return {
            "message": "Language preference updated",
            "preferred_language": target_user.preferred_language
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Language preference update failed: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not update language preference.")


@app.post("/api/auth/me/password", dependencies=[Depends(verify_csrf)])
def change_my_password(
    payload: UserChangePasswordRequest,
    request: Request,
    session_token: Optional[str] = Cookie(None, alias="session_token"),
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    now_utc = datetime.now(timezone.utc)

    allowed, retry_after = consume_rate_limit_atomic(
        db,
        bucket_key=f"CHANGE_PW_USER:{current_user.id}",
        max_attempts=5,
        window_seconds=300,
        now_utc=now_utc
    )
    if not allowed:
        record_audit_event(
            db, "PASSWORD_CHANGE_RATE_LIMITED", "auth", "DENIED",
            actor=current_user, resource_id=str(current_user.id), ip_address=ip,
            details={"retry_after_sec": retry_after}, commit=True
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many password change attempts. Please wait {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )

    new_pw = payload.new_password  # no mutar: el login verifica la contrasena exactamente como se escribio
    if len(new_pw.strip()) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters long.")

    try:
        target_user = (
            db.query(UserModel)
            .filter(UserModel.id == current_user.id)
            .with_for_update()
            .first()
        )
        if not target_user or not target_user.is_active:
            raise HTTPException(status_code=403, detail="Account has been deactivated.")

        if not pwd_context.verify(payload.current_password, target_user.password_hash):
            record_audit_event(
                db, "PASSWORD_CHANGE_FAILED_CURRENT_MISMATCH", "auth", "DENIED",
                actor=target_user, resource_id=str(target_user.id), ip_address=ip, commit=True
            )
            raise HTTPException(status_code=400, detail="Current password is incorrect.")

        if pwd_context.verify(new_pw, target_user.password_hash):
            raise HTTPException(
                status_code=422,
                detail="New password must be different from your current password."
            )

        target_user.password_hash = pwd_context.hash(new_pw)

        # Keep current session active while revoking sessions on other devices
        current_token_hash = hash_token(session_token) if session_token else None
        other_sessions_query = db.query(SessionModel).filter(SessionModel.user_id == target_user.id)
        if current_token_hash:
            other_sessions_query = other_sessions_query.filter(SessionModel.token != current_token_hash)
        revoked_other_sessions = other_sessions_query.delete(synchronize_session=False)

        record_audit_event(
            db, "PASSWORD_CHANGED_SELF", "auth", "SUCCESS",
            actor=target_user, resource_id=str(target_user.id), ip_address=ip,
            details={"other_sessions_revoked": revoked_other_sessions}, commit=False
        )
        db.commit()
        reset_rate_limit_bucket(db, f"CHANGE_PW_USER:{target_user.id}")

        return {"message": "Password updated successfully."}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Self password change failed: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not update password.")


# --- TEAM MANAGEMENT ENDPOINTS ---
@app.post("/api/auth/register", dependencies=[Depends(verify_csrf)])
def register(
    user_data: UserRegister,
    request: Request,
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    email_clean = user_data.email.strip().lower()

    if user_data.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Invalid role. Allowed: {', '.join(sorted(VALID_ROLES))}")

    if db.query(UserModel).filter(UserModel.email == email_clean).first():
        record_audit_event(
            db, "USER_CREATE_DUPLICATE", "user", "ERROR",
            actor=admin, resource_id=mask_email_for_audit(email_clean), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=400, detail="Email is already registered")

    try:
        new_user = UserModel(
            email=email_clean,
            full_name=user_data.full_name.strip() if user_data.full_name else None,
            password_hash=pwd_context.hash(user_data.password),
            role=user_data.role,
            is_active=True
        )
        db.add(new_user)
        db.flush()

        record_audit_event(
            db, "USER_CREATED", "user", "SUCCESS",
            actor=admin, resource_id=str(new_user.id), ip_address=ip,
            details={"assigned_role": new_user.role}, commit=False
        )
        db.commit()
        db.refresh(new_user)
        return {
            "message": "Team member created successfully",
            "id": new_user.id,
            "email": new_user.email,
            "full_name": new_user.full_name,
            "role": new_user.role
        }
    except Exception as e:
        db.rollback()
        logger.error(f"User creation rollback: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Failed to create user with audit trail.")


@app.get("/api/admin/users")
def list_team_users(
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    users = db.query(UserModel).order_by(UserModel.id.asc()).all()
    return [{
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at
    } for u in users]


@app.patch("/api/admin/users/{target_user_id}", dependencies=[Depends(verify_csrf)])
def update_team_user(
    target_user_id: int,
    payload: UserUpdateByAdmin,
    request: Request,
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        target = db.query(UserModel).filter(UserModel.id == target_user_id).with_for_update().first()
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        new_role = payload.role if payload.role is not None else target.role
        new_active = payload.is_active if payload.is_active is not None else target.is_active

        if new_role not in VALID_ROLES:
            raise HTTPException(status_code=400, detail="Invalid role specified")

        if target.id == admin.id and (new_role != "OWNER_ADMIN" or not new_active):
            raise HTTPException(
                status_code=400,
                detail="You cannot demote or deactivate your own OWNER_ADMIN account."
            )

        if target.role == "OWNER_ADMIN" and (new_role != "OWNER_ADMIN" or not new_active):
            active_owner_rows = (
                db.query(UserModel.id)
                .filter(UserModel.role == "OWNER_ADMIN", UserModel.is_active.is_(True))
                .with_for_update()
                .all()
            )
            if len(active_owner_rows) <= 1:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote or deactivate the last active OWNER_ADMIN."
                )

        if payload.full_name is not None:
            target.full_name = payload.full_name.strip() or None
        target.role = new_role
        target.is_active = new_active

        if not new_active:
            db.query(SessionModel).filter(SessionModel.user_id == target.id).delete()

        record_audit_event(
            db, "USER_UPDATED", "user", "SUCCESS",
            actor=admin, resource_id=str(target.id), ip_address=ip,
            details={"role": target.role, "is_active": target.is_active}, commit=False
        )
        db.commit()
        return {
            "message": "Team member updated",
            "id": target.id,
            "role": target.role,
            "is_active": target.is_active
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"User update failed: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Could not update user.")


@app.get("/api/admin/audit-logs")
def get_audit_logs(
    limit: int = 40,
    admin: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    safe_limit = min(max(1, limit), 100)
    logs = db.query(AuditLogModel).order_by(AuditLogModel.id.desc()).limit(safe_limit).all()
    return [{
        "id": l.id,
        "created_at": l.created_at,
        "actor_user_id": l.actor_user_id,
        "actor_role": l.actor_role,
        "event_type": l.event_type,
        "resource_type": l.resource_type,
        "resource_id": l.resource_id,
        "outcome": l.outcome,
        "ip_address": l.ip_address,
        "details": json.loads(l.details_json) if l.details_json else None
    } for l in logs]


# --- STAGE 1 & 2: CUSTOMERS ENDPOINTS ---
# --- CUSTOMER NAME (PURE HELPERS) BEGIN ---
# Limites: 50 + 1 + 50 + 1 + 75 = 177 caracteres, siempre cabe en full_legal_name (180).
def normalize_name_part(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def compose_full_legal_name(first: Optional[str], middle: Optional[str], last: Optional[str]) -> str:
    return " ".join(
        p for p in (normalize_name_part(first), normalize_name_part(middle), normalize_name_part(last)) if p
    )


def resolve_customer_name_for_create(entity_type, first_name, middle_name, last_name, full_legal_name) -> dict:
    # Devuelve los 4 campos de nombre a guardar o lanza ValueError con el mensaje para el usuario.
    if entity_type == "BUSINESS":
        legal_name = normalize_name_part(full_legal_name)
        if len(legal_name) < 3:
            raise ValueError("Legal entity name is required for Business (Persona Moral) customers.")
        return {"first_name": None, "middle_name": None, "last_name": None, "full_legal_name": legal_name}

    first = normalize_name_part(first_name)
    middle = normalize_name_part(middle_name)
    last = normalize_name_part(last_name)
    if not first or not last:
        raise ValueError("First name and last name are required for individual customers.")
    return {
        "first_name": first,
        "middle_name": middle or None,
        "last_name": last,
        "full_legal_name": compose_full_legal_name(first, middle, last),
    }


def resolve_customer_name_for_update(final_entity_type, type_changed, current_first, current_middle, current_last,
                                     first_name, middle_name, last_name, full_legal_name) -> Optional[dict]:
    # Devuelve {campo: valor} con lo que hay que cambiar, None si el nombre no cambia, o lanza ValueError.
    if final_entity_type == "BUSINESS":
        out = {}
        if full_legal_name is not None:
            legal_name = normalize_name_part(full_legal_name)
            if len(legal_name) < 3:
                raise ValueError("Legal entity name must have at least 3 characters.")
            out["full_legal_name"] = legal_name
        elif type_changed:
            raise ValueError("Legal entity name is required for Business (Persona Moral) customers.")
        if type_changed or current_first or current_middle or current_last:
            out.update({"first_name": None, "middle_name": None, "last_name": None})
        return out or None

    # INDIVIDUAL
    parts_sent = any(p is not None for p in (first_name, middle_name, last_name))
    if parts_sent or type_changed:
        first = normalize_name_part(first_name) if first_name is not None else normalize_name_part(current_first)
        middle = normalize_name_part(middle_name) if middle_name is not None else normalize_name_part(current_middle)
        last = normalize_name_part(last_name) if last_name is not None else normalize_name_part(current_last)
        if not first or not last:
            raise ValueError("First name and last name are required for individual customers.")
        return {
            "first_name": first,
            "middle_name": middle or None,
            "last_name": last,
            "full_legal_name": compose_full_legal_name(first, middle, last),
        }

    if full_legal_name is not None:
        # Clientes anteriores al cambio (sin partes) pueden seguir editando solo el nombre completo.
        if normalize_name_part(current_first) or normalize_name_part(current_last):
            raise ValueError("To change an individual's name, send first_name, middle_name and last_name.")
        legal_name = normalize_name_part(full_legal_name)
        if len(legal_name) < 3:
            raise ValueError("Name must have at least 3 characters.")
        return {"full_legal_name": legal_name}
    return None
# --- CUSTOMER NAME (PURE HELPERS) END ---


def serialize_customer(
    c: CustomerModel,
    active_loans_count: int = 0,
    unpaid_principal_cents: int = 0,
    last_payment_date: Optional[str] = None
) -> dict:
    return {
        "id": c.id,
        "customer_number": c.customer_number,
        "entity_type": c.entity_type,
        "status": c.status,
        "verification_status": c.verification_status,
        "full_legal_name": c.full_legal_name,
        "first_name": c.first_name,
        "middle_name": c.middle_name,
        "last_name": c.last_name,
        "trade_name": c.trade_name,
        "legal_representative_name": c.legal_representative_name,
        "tax_id_rfc": c.tax_id_rfc,
        "date_of_birth": c.date_of_birth.isoformat() if c.date_of_birth else None,
        "phone_primary": c.phone_primary,
        "phone_secondary": c.phone_secondary,
        "email": c.email,
        "address_full": c.address_full,
        "employment_status": c.employment_status,
        "employer_or_business_name": c.employer_or_business_name,
        "occupation_or_industry": c.occupation_or_industry,
        "employment_address": c.employment_address,
        "owner_user_id": c.owner_user_id,
        "created_by_user_id": c.created_by_user_id,
        "customer_since": c.customer_since.isoformat() if c.customer_since else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "current_outstanding_balance_cents": unpaid_principal_cents,
        "current_outstanding_principal_cents": unpaid_principal_cents,
        "active_loans_count": active_loans_count,
        "last_payment_date": last_payment_date
    }


def serialize_interaction(i: InteractionModel, user_map: dict) -> dict:
    author = user_map.get(i.created_by_user_id)
    return {
        "id": i.id,
        "lead_id": i.lead_id,
        "customer_id": i.customer_id,
        "interaction_type": i.interaction_type,
        "direction": i.direction,
        "visibility": i.visibility,
        "outcome": i.outcome,
        "content": i.content,
        "next_follow_up_at": i.next_follow_up_at.isoformat() if i.next_follow_up_at else None,
        "created_by_user_id": i.created_by_user_id,
        "created_by_name": (author.full_name or author.email) if author else f"User #{i.created_by_user_id}",
        "created_by_role": author.role if author else "TEAM",
        "created_at": i.created_at.isoformat() if i.created_at else None
    }


@app.get("/api/customers")
def list_customers(
    search: Optional[str] = Query(default=None, max_length=120),
    status: Optional[str] = Query(default=None),
    entity_type: Optional[str] = Query(default=None),
    sort: str = Query(default="CREATED_DESC"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    query = db.query(CustomerModel)

    if status and status != "ALL":
        query = query.filter(CustomerModel.status == status)

    if entity_type and entity_type != "ALL":
        query = query.filter(CustomerModel.entity_type == entity_type)

    if search and search.strip():
        term = f"%{search.strip()}%"
        digits_only = re.sub(r"\D", "", search)
        filters = [
            CustomerModel.customer_number.ilike(term),
            CustomerModel.full_legal_name.ilike(term),
            CustomerModel.trade_name.ilike(term),
            CustomerModel.email.ilike(term)
        ]
        if digits_only:
            filters.append(CustomerModel.phone_primary_normalized.ilike(f"%{digits_only}%"))
        query = query.filter(or_(*filters))

    total = query.count()

    if sort == "NAME_ASC":
        query = query.order_by(CustomerModel.full_legal_name.asc())
    elif sort == "NUMBER_ASC":
        query = query.order_by(CustomerModel.customer_number.asc())
    else:
        query = query.order_by(CustomerModel.id.desc())

    offset = (page - 1) * page_size
    items = query.offset(offset).limit(page_size).all()

    # Pre-fetch active loan balances and latest posted payment date for customers on this page
    cust_ids = [c.id for c in items]
    loan_stats = {}
    last_payments_map = {}
    if cust_ids:
        active_loans = (
            db.query(LoanModel)
            .filter(LoanModel.customer_id.in_(cust_ids), LoanModel.status.in_(["ACTIVE", "DEFAULTED"]))
            .all()
        )
        for l in active_loans:
            st = loan_stats.setdefault(l.customer_id, {"count": 0, "principal": 0})
            st["count"] += 1
            st["principal"] += l.unpaid_principal_cents

        payment_rows = (
            db.query(
                LoanPaymentModel.customer_id,
                func.max(LoanPaymentModel.paid_at)
            )
            .filter(
                LoanPaymentModel.customer_id.in_(cust_ids),
                LoanPaymentModel.status == "POSTED"
            )
            .group_by(LoanPaymentModel.customer_id)
            .all()
        )
        crm_tz = get_crm_timezone()
        for cid, max_paid_at in payment_rows:
            if max_paid_at:
                if isinstance(max_paid_at, str):
                    last_payments_map[cid] = max_paid_at[:10]
                else:
                    dt_utc = max_paid_at if max_paid_at.tzinfo else max_paid_at.replace(tzinfo=timezone.utc)
                    last_payments_map[cid] = dt_utc.astimezone(crm_tz).strftime("%Y-%m-%d")

    serialized_items = []
    for c in items:
        st = loan_stats.get(c.id, {"count": 0, "principal": 0})
        serialized_items.append(
            serialize_customer(
                c,
                active_loans_count=st["count"],
                unpaid_principal_cents=st["principal"],
                last_payment_date=last_payments_map.get(c.id)
            )
        )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": serialized_items
    }


@app.post("/api/customers", dependencies=[Depends(verify_csrf)])
def create_customer(
    payload: CustomerCreateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    entity_type = payload.entity_type.strip().upper()
    status = payload.status.strip().upper()
    verification_status = payload.verification_status.strip().upper()

    if entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail="Invalid entity_type. Use INDIVIDUAL or BUSINESS.")
    if status not in VALID_CUSTOMER_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid customer status.")
    if verification_status not in VALID_VERIFICATION_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid verification_status.")

    legal_rep = payload.legal_representative_name.strip() if payload.legal_representative_name else None
    try:
        name_fields = resolve_customer_name_for_create(
            entity_type, payload.first_name, payload.middle_name, payload.last_name, payload.full_legal_name
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    full_legal_name = name_fields["full_legal_name"]

    if entity_type == "BUSINESS" and not legal_rep:
        raise HTTPException(
            status_code=422,
            detail="Legal Representative Name is required for Business (Persona Moral) customers."
        )

    phone_norm = normalize_phone_number(payload.phone_primary, required=True)
    phone_sec_norm = normalize_phone_number(payload.phone_secondary, required=False)
    email_clean = payload.email.strip().lower() if payload.email else None
    rfc_clean = payload.tax_id_rfc.strip().upper() if payload.tax_id_rfc and payload.tax_id_rfc.strip() else None

    dup_phone = db.query(CustomerModel).filter(CustomerModel.phone_primary_normalized == phone_norm).first()
    if dup_phone:
        raise HTTPException(
            status_code=409,
            detail=f"Primary phone number is already registered under customer {dup_phone.customer_number}."
        )

    if email_clean:
        dup_email = db.query(CustomerModel).filter(CustomerModel.email == email_clean).first()
        if dup_email:
            raise HTTPException(
                status_code=409,
                detail=f"Email address is already registered under customer {dup_email.customer_number}."
            )

    if rfc_clean:
        dup_rfc = db.query(CustomerModel).filter(CustomerModel.tax_id_rfc == rfc_clean).first()
        if dup_rfc:
            raise HTTPException(
                status_code=409,
                detail=f"RFC / Tax ID is already registered under customer {dup_rfc.customer_number}."
            )

    try:
        cust_number = generate_next_customer_number(db)
        owner_id = payload.owner_user_id if payload.owner_user_id else current_user.id

        new_customer = CustomerModel(
            customer_number=cust_number,
            entity_type=entity_type,
            status=status,
            verification_status=verification_status,
            full_legal_name=full_legal_name,
            first_name=name_fields["first_name"],
            middle_name=name_fields["middle_name"],
            last_name=name_fields["last_name"],
            trade_name=payload.trade_name.strip() if payload.trade_name and payload.trade_name.strip() else None,
            legal_representative_name=legal_rep,
            tax_id_rfc=rfc_clean,
            date_of_birth=payload.date_of_birth,
            phone_primary=payload.phone_primary.strip(),
            phone_primary_normalized=phone_norm,
            phone_secondary=payload.phone_secondary.strip() if payload.phone_secondary and payload.phone_secondary.strip() else None,
            phone_secondary_normalized=phone_sec_norm,
            email=email_clean,
            address_full=payload.address_full.strip() if payload.address_full and payload.address_full.strip() else None,
            employment_status=payload.employment_status.strip() if payload.employment_status and payload.employment_status.strip() else None,
            employer_or_business_name=payload.employer_or_business_name.strip() if payload.employer_or_business_name and payload.employer_or_business_name.strip() else None,
            occupation_or_industry=payload.occupation_or_industry.strip() if payload.occupation_or_industry and payload.occupation_or_industry.strip() else None,
            employment_address=payload.employment_address.strip() if payload.employment_address and payload.employment_address.strip() else None,
            owner_user_id=owner_id,
            created_by_user_id=current_user.id
        )
        db.add(new_customer)
        db.flush()

        record_audit_event(
            db, "CUSTOMER_CREATED", "customer", "SUCCESS",
            actor=current_user, resource_id=new_customer.customer_number, ip_address=ip,
            details={
                "customer_number": new_customer.customer_number,
                "entity_type": new_customer.entity_type,
                "status": new_customer.status
            },
            commit=False
        )
        db.commit()
        db.refresh(new_customer)
        return serialize_customer(new_customer, active_loans_count=0, unpaid_principal_cents=0)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to create customer")
        raise HTTPException(status_code=500, detail="Could not create customer record.")


@app.get("/api/customers/{customer_id}")
def get_customer_detail(
    customer_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    active_loans = (
        db.query(LoanModel)
        .filter(LoanModel.customer_id == customer.id, LoanModel.status.in_(["ACTIVE", "DEFAULTED"]))
        .all()
    )
    all_loans = (
        db.query(LoanModel)
        .filter(LoanModel.customer_id == customer.id)
        .order_by(LoanModel.id.desc())
        .all()
    )

    total_outstanding_principal = sum(l.unpaid_principal_cents for l in active_loans)
    total_amount_due = sum(l.remaining_contract_total_cents for l in active_loans)

    _, _, _, today_local = get_local_day_bounds_utc()
    overdue_amt = 0
    next_due_date = None
    remaining_inst_count = 0

    if active_loans:
        loan_ids = [l.id for l in active_loans]
        unpaid_insts = (
            db.query(LoanInstallmentModel)
            .filter(LoanInstallmentModel.loan_id.in_(loan_ids), LoanInstallmentModel.remaining_amount_cents > 0)
            .order_by(LoanInstallmentModel.due_date.asc())
            .all()
        )
        remaining_inst_count = len(unpaid_insts)
        if unpaid_insts:
            next_due_date = unpaid_insts[0].due_date.isoformat()
            for inst in unpaid_insts:
                if inst.due_date < today_local:
                    overdue_amt += inst.remaining_amount_cents

    data = serialize_customer(customer, active_loans_count=len(active_loans), unpaid_principal_cents=total_outstanding_principal)
    data["credit_summary"] = {
        "current_outstanding_principal_cents": total_outstanding_principal,
        "total_amount_due_cents": total_amount_due,
        "overdue_amount_cents": overdue_amt,
        "active_loans_count": len(active_loans),
        "total_loans_count": len(all_loans),
        "last_payment_date": None,
        "next_payment_due_date": next_due_date,
        "remaining_installments": remaining_inst_count
    }
    data["loans"] = [serialize_loan_summary(l, today_local) for l in all_loans]

    last_payment = db.query(func.max(LoanPaymentModel.paid_at)).filter(
        LoanPaymentModel.customer_id == customer.id, LoanPaymentModel.status == 'POSTED'
    ).scalar()
    if last_payment:
        if isinstance(last_payment, str):
            formatted_last_pay = last_payment[:10]
        else:
            dt_utc = last_payment if last_payment.tzinfo else last_payment.replace(tzinfo=timezone.utc)
            formatted_last_pay = dt_utc.astimezone(get_crm_timezone()).strftime("%Y-%m-%d")
        data["last_payment_date"] = formatted_last_pay
        data["credit_summary"]["last_payment_date"] = formatted_last_pay

    return data


@app.patch("/api/customers/{customer_id}", dependencies=[Depends(verify_csrf)])
def update_customer(
    customer_id: int,
    payload: CustomerUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).with_for_update().first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found.")

        updated_fields = []
        previous_entity_type = customer.entity_type

        if payload.entity_type is not None:
            et = payload.entity_type.strip().upper()
            if et not in VALID_ENTITY_TYPES:
                raise HTTPException(status_code=422, detail="Invalid entity_type.")
            customer.entity_type = et
            updated_fields.append("entity_type")

        if payload.status is not None:
            st = payload.status.strip().upper()
            if st not in VALID_CUSTOMER_STATUSES:
                raise HTTPException(status_code=422, detail="Invalid status.")
            customer.status = st
            updated_fields.append("status")

        if payload.verification_status is not None:
            vs = payload.verification_status.strip().upper()
            if vs not in VALID_VERIFICATION_STATUSES:
                raise HTTPException(status_code=422, detail="Invalid verification_status.")
            customer.verification_status = vs
            updated_fields.append("verification_status")

        try:
            name_updates = resolve_customer_name_for_update(
                customer.entity_type, customer.entity_type != previous_entity_type,
                customer.first_name, customer.middle_name, customer.last_name,
                payload.first_name, payload.middle_name, payload.last_name, payload.full_legal_name
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if name_updates:
            for field_name, field_value in name_updates.items():
                setattr(customer, field_name, field_value)
                updated_fields.append(field_name)

        if payload.trade_name is not None:
            customer.trade_name = payload.trade_name.strip() or None
            updated_fields.append("trade_name")

        if payload.legal_representative_name is not None:
            customer.legal_representative_name = payload.legal_representative_name.strip() or None
            updated_fields.append("legal_representative_name")

        if customer.entity_type == "BUSINESS" and not customer.legal_representative_name:
            raise HTTPException(
                status_code=422,
                detail="Legal Representative Name is required for Business (Persona Moral) customers."
            )

        if payload.phone_primary is not None:
            phone_norm = normalize_phone_number(payload.phone_primary, required=True)
            dup_phone = (
                db.query(CustomerModel)
                .filter(CustomerModel.phone_primary_normalized == phone_norm, CustomerModel.id != customer.id)
                .first()
            )
            if dup_phone:
                raise HTTPException(
                    status_code=409,
                    detail=f"Primary phone number is already registered under customer {dup_phone.customer_number}."
                )
            customer.phone_primary = payload.phone_primary.strip()
            customer.phone_primary_normalized = phone_norm
            updated_fields.append("phone_primary")

        if payload.phone_secondary is not None:
            customer.phone_secondary = payload.phone_secondary.strip() or None
            customer.phone_secondary_normalized = normalize_phone_number(customer.phone_secondary, required=False)
            updated_fields.append("phone_secondary")

        if payload.email is not None:
            email_clean = payload.email.strip().lower() if payload.email else None
            if email_clean:
                dup_email = (
                    db.query(CustomerModel)
                    .filter(CustomerModel.email == email_clean, CustomerModel.id != customer.id)
                    .first()
                )
                if dup_email:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Email address is already registered under customer {dup_email.customer_number}."
                    )
            customer.email = email_clean
            updated_fields.append("email")

        if payload.tax_id_rfc is not None:
            rfc_clean = payload.tax_id_rfc.strip().upper() or None
            if rfc_clean:
                dup_rfc = (
                    db.query(CustomerModel)
                    .filter(CustomerModel.tax_id_rfc == rfc_clean, CustomerModel.id != customer.id)
                    .first()
                )
                if dup_rfc:
                    raise HTTPException(
                        status_code=409,
                        detail=f"RFC / Tax ID is already registered under customer {dup_rfc.customer_number}."
                    )
            customer.tax_id_rfc = rfc_clean
            updated_fields.append("tax_id_rfc")

        if payload.date_of_birth is not None:
            customer.date_of_birth = payload.date_of_birth
            updated_fields.append("date_of_birth")

        if payload.address_full is not None:
            customer.address_full = payload.address_full.strip() or None
            updated_fields.append("address_full")

        if payload.employment_status is not None:
            customer.employment_status = payload.employment_status.strip() or None
            updated_fields.append("employment_status")

        if payload.employer_or_business_name is not None:
            customer.employer_or_business_name = payload.employer_or_business_name.strip() or None
            updated_fields.append("employer_or_business_name")

        if payload.occupation_or_industry is not None:
            customer.occupation_or_industry = payload.occupation_or_industry.strip() or None
            updated_fields.append("occupation_or_industry")

        if payload.employment_address is not None:
            customer.employment_address = payload.employment_address.strip() or None
            updated_fields.append("employment_address")

        if payload.owner_user_id is not None:
            customer.owner_user_id = payload.owner_user_id
            updated_fields.append("owner_user_id")

        customer.updated_at = datetime.now(timezone.utc)

        record_audit_event(
            db, "CUSTOMER_UPDATED", "customer", "SUCCESS",
            actor=current_user, resource_id=customer.customer_number, ip_address=ip,
            details={
                "customer_number": customer.customer_number,
                "updated_fields": ",".join(updated_fields)
            },
            commit=False
        )
        db.commit()
        db.refresh(customer)
        active_loans_count = db.query(LoanModel).filter(LoanModel.customer_id == customer.id, LoanModel.status == "ACTIVE").count()
        unpaid_principal = sum(l.unpaid_principal_cents for l in customer.loans if l.status == "ACTIVE")
        return serialize_customer(customer, active_loans_count=active_loans_count, unpaid_principal_cents=unpaid_principal)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to update customer")
        raise HTTPException(status_code=500, detail="Could not update customer record.")


@app.get("/api/customers/{customer_id}/interactions")
def list_customer_interactions(
    customer_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    customer = db.query(CustomerModel.id).filter(CustomerModel.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    interactions = (
        db.query(InteractionModel)
        .filter(InteractionModel.customer_id == customer_id)
        .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
        .all()
    )
    user_ids = {i.created_by_user_id for i in interactions}
    users = db.query(UserModel).filter(UserModel.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: u for u in users}

    return [serialize_interaction(i, user_map) for i in interactions]


@app.post("/api/customers/{customer_id}/interactions", dependencies=[Depends(verify_csrf)])
def create_customer_interaction(
    customer_id: int,
    payload: InteractionCreateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    itype = payload.interaction_type.strip().upper()
    direction = payload.direction.strip().upper()
    visibility = payload.visibility.strip().upper()
    outcome = payload.outcome.strip().upper() if payload.outcome and payload.outcome.strip() else None

    if itype not in VALID_INTERACTION_TYPES:
        raise HTTPException(status_code=422, detail="Invalid interaction_type.")
    if direction not in VALID_INTERACTION_DIRECTIONS:
        raise HTTPException(status_code=422, detail="Invalid direction.")
    if visibility not in VALID_INTERACTION_VISIBILITIES:
        raise HTTPException(status_code=422, detail="Invalid visibility.")
    if outcome and outcome not in VALID_CALL_OUTCOMES:
        raise HTTPException(status_code=422, detail="Invalid call outcome.")

    follow_up_utc = parse_optional_datetime_utc(payload.next_follow_up_at)

    try:
        interaction = InteractionModel(
            lead_id=None,
            customer_id=customer.id,
            interaction_type=itype,
            direction=direction,
            visibility=visibility,
            outcome=outcome,
            content=payload.content.strip(),
            next_follow_up_at=follow_up_utc,
            created_by_user_id=current_user.id
        )
        db.add(interaction)
        db.flush()

        record_audit_event(
            db, "CUSTOMER_INTERACTION_LOGGED", "customer", "SUCCESS",
            actor=current_user, resource_id=customer.customer_number, ip_address=ip,
            details={
                "customer_number": customer.customer_number,
                "interaction_type": itype,
                "visibility": visibility,
                "outcome": outcome or "NONE"
            },
            commit=False
        )
        db.commit()
        db.refresh(interaction)
        return serialize_interaction(interaction, {current_user.id: current_user})
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to log customer interaction")
        raise HTTPException(status_code=500, detail="Could not save customer interaction.")


# --- DOCUMENT UPLOAD SECURITY (PURE HELPERS) BEGIN ---
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_MIME_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 512 * 1024  # holgura para boundaries y campos del formulario


class UploadTooLarge(Exception):
    pass


def read_upload_limited(fileobj, max_bytes: int, chunk_size: int = UPLOAD_READ_CHUNK_BYTES) -> bytes:
    # Lee en bloques y aborta apenas se supera max_bytes: nunca se carga mas de max_bytes + 1 en RAM.
    chunks = []
    total = 0
    while True:
        chunk = fileobj.read(min(chunk_size, max_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def detect_document_mime(data: bytes) -> Optional[str]:
    # Tipo REAL por "magic bytes". No se confia en el Content-Type que declara el cliente.
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sanitize_upload_filename(raw_name: Optional[str]) -> str:
    name = (raw_name or "").replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"<>|:*?')
    name = name.strip().strip(".")
    return name[:255] if name else "document"


def build_content_disposition(filename: Optional[str], disposition: str = "inline") -> str:
    safe_name = sanitize_upload_filename(filename)
    ascii_fallback = unicodedata.normalize("NFKD", safe_name).encode("ascii", "ignore").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "").replace('"', "").strip() or "document"
    return f'{disposition}; filename="{ascii_fallback}"; filename*=UTF-8\'\'{url_quote(safe_name, safe="")}'


_DOCUMENT_UPLOAD_PATH_RE = re.compile(r"^/api/customers/\d+/documents/?$")


class DocumentUploadSizeGuard:
    # Middleware ASGI puro: rechaza con 413 las subidas cuyo Content-Length ya excede el limite,
    # antes de que el servidor reciba y guarde en disco todo el cuerpo.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and _DOCUMENT_UPLOAD_PATH_RE.match(scope["path"])
        ):
            for header_name, header_value in scope.get("headers", []):
                if header_name == b"content-length":
                    try:
                        declared_length = int(header_value)
                    except ValueError:
                        declared_length = 0
                    if declared_length > MAX_FILE_SIZE_BYTES + MULTIPART_OVERHEAD_BYTES:
                        body = b'{"detail":"File must be between 1 byte and 10 MB."}'
                        await send({
                            "type": "http.response.start", "status": 413,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii")),
                            ],
                        })
                        await send({"type": "http.response.body", "body": body})
                        return
                    break
        await self.app(scope, receive, send)
# --- DOCUMENT UPLOAD SECURITY (PURE HELPERS) END ---


app.add_middleware(DocumentUploadSizeGuard)


@app.post("/api/customers/{customer_id}/documents", dependencies=[Depends(verify_csrf)])
def upload_customer_document(
    customer_id: int,
    request: Request,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    customer = db.query(CustomerModel).filter(CustomerModel.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    if document_type not in VALID_DOCUMENT_TYPES:
        raise HTTPException(status_code=422, detail="Invalid document type.")

    declared_mime = (file.content_type or "").split(";")[0].strip().lower()
    if declared_mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=422, detail="Invalid file type. Only PDF, JPG, PNG, WEBP are allowed.")

    try:
        file_data = read_upload_limited(file.file, MAX_FILE_SIZE_BYTES)
    except UploadTooLarge:
        raise HTTPException(status_code=422, detail="File must be between 1 byte and 10 MB.")

    file_size = len(file_data)
    if file_size == 0:
        raise HTTPException(status_code=422, detail="File must be between 1 byte and 10 MB.")

    # El tipo que se guarda es el detectado por contenido, no el declarado por el cliente.
    mime_type = detect_document_mime(file_data)
    if mime_type is None:
        raise HTTPException(status_code=422, detail="File content is not a valid PDF, JPG, PNG or WEBP.")

    file_hash = hashlib.sha256(file_data).hexdigest()

    try:
        new_doc = CustomerDocumentModel(
            customer_id=customer.id,
            document_type=document_type,
            original_filename=sanitize_upload_filename(file.filename),
            mime_type=mime_type,
            file_size_bytes=file_size,
            file_hash=file_hash,
            file_data=file_data,
            uploaded_by_user_id=current_user.id
        )
        db.add(new_doc)
        db.flush()

        record_audit_event(
            db, "CUSTOMER_DOCUMENT_UPLOADED", "document", "SUCCESS", actor=current_user,
            resource_id=str(new_doc.id), ip_address=ip,
            details={"customer_number": customer.customer_number, "type": document_type, "hash": file_hash}, commit=False
        )
        db.commit()
        return {
            "id": new_doc.id, "document_type": new_doc.document_type,
            "original_filename": new_doc.original_filename, "file_size_bytes": new_doc.file_size_bytes,
            "created_at": new_doc.created_at.isoformat()
        }
    except Exception as e:
        db.rollback()
        logger.exception("Document upload failed")
        raise HTTPException(status_code=500, detail="Could not upload document.")

@app.get("/api/customers/{customer_id}/documents")
def list_customer_documents(
    customer_id: int,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    docs = db.query(CustomerDocumentModel).filter(CustomerDocumentModel.customer_id == customer_id, CustomerDocumentModel.status == "ACTIVE").order_by(CustomerDocumentModel.id.desc()).all()
    user_ids = {d.uploaded_by_user_id for d in docs}
    users = {u.id: u.full_name or u.email for u in db.query(UserModel).filter(UserModel.id.in_(user_ids)).all()} if user_ids else {}

    return [{
        "id": d.id, "document_type": d.document_type, "original_filename": d.original_filename,
        "mime_type": d.mime_type, "file_size_bytes": d.file_size_bytes, "file_hash": d.file_hash,
        "uploaded_by_name": users.get(d.uploaded_by_user_id, "System"),
        "created_at": d.created_at.isoformat()
    } for d in docs]

@app.get("/api/customers/{customer_id}/documents/{doc_id}/download")
def download_customer_document(
    customer_id: int,
    doc_id: int,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    doc = db.query(CustomerDocumentModel).filter(CustomerDocumentModel.id == doc_id, CustomerDocumentModel.customer_id == customer_id, CustomerDocumentModel.status == "ACTIVE").first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found or unauthorized.")

    record_audit_event(
        db, "CUSTOMER_DOCUMENT_DOWNLOADED", "document", "SUCCESS", actor=current_user,
        resource_id=str(doc.id), ip_address=get_client_ip(request), commit=True
    )
    return Response(
        content=doc.file_data,
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": build_content_disposition(doc.original_filename, "inline"),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        }
    )

@app.delete("/api/customers/{customer_id}/documents/{doc_id}", dependencies=[Depends(verify_csrf)])
def delete_customer_document(
    customer_id: int,
    doc_id: int,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db)
):
    doc = db.query(CustomerDocumentModel).filter(CustomerDocumentModel.id == doc_id, CustomerDocumentModel.customer_id == customer_id, CustomerDocumentModel.status == "ACTIVE").first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    try:
        doc.status = "DELETED"
        # commit=False: el cambio y su auditoria se confirman juntos. Con commit=True, si el insert
        # de auditoria fallaba, el rollback interno deshacia el borrado y aun asi se respondia "exito".
        record_audit_event(
            db, "CUSTOMER_DOCUMENT_DELETED", "document", "SUCCESS", actor=current_user,
            resource_id=str(doc.id), ip_address=get_client_ip(request), commit=False
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Document delete failed")
        raise HTTPException(status_code=500, detail="Could not delete document.")
    return {"message": "Document deleted successfully"}


# --- STAGE 2: LEADS, CALL LIST & CONVERSION ---
def compute_call_bucket(lead: LeadModel, today_start_utc: datetime, today_end_utc: datetime) -> Optional[str]:
    if lead.stage in ("CONVERTED", "LOST"):
        return None
    if lead.next_follow_up_at:
        nfa = lead.next_follow_up_at
        if nfa.tzinfo is None:
            nfa = nfa.replace(tzinfo=timezone.utc)
        if nfa < today_start_utc:
            return "OVERDUE"
        if nfa <= today_end_utc:
            return "TODAY"
        return "UPCOMING"
    if lead.stage == "NEW":
        return "TODAY"
    return None


def serialize_lead(l: LeadModel, today_start_utc: datetime, today_end_utc: datetime) -> dict:
    return {
        "id": l.id,
        "lead_number": l.lead_number,
        "full_name": l.full_name,
        "entity_type": l.entity_type,
        "trade_name": l.trade_name,
        "legal_representative_name": l.legal_representative_name,
        "tax_id_rfc": l.tax_id_rfc,
        "phone_primary": l.phone_primary,
        "email": l.email,
        "source": l.source,
        "product_interest": l.product_interest,
        "requested_amount_cents": int(l.requested_amount_cents or 0),
        "currency": l.currency,
        "stage": l.stage,
        "priority": l.priority,
        "lost_reason": l.lost_reason,
        "last_contacted_at": l.last_contacted_at.isoformat() if l.last_contacted_at else None,
        "last_interaction_outcome": l.last_interaction_outcome,
        "next_follow_up_at": l.next_follow_up_at.isoformat() if l.next_follow_up_at else None,
        "call_bucket": compute_call_bucket(l, today_start_utc, today_end_utc),
        "summary_notes": l.summary_notes,
        "owner_user_id": l.owner_user_id,
        "created_by_user_id": l.created_by_user_id,
        "converted_customer_id": l.converted_customer_id,
        "converted_at": l.converted_at.isoformat() if l.converted_at else None,
        "created_at": l.created_at.isoformat() if l.created_at else None,
        "updated_at": l.updated_at.isoformat() if l.updated_at else None
    }


@app.get("/api/leads")
def list_leads(
    search: Optional[str] = Query(default=None, max_length=120),
    stage: Optional[str] = Query(default=None),
    product_interest: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    call_bucket: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    today_start_utc, today_end_utc, upcoming_end_utc, _ = get_local_day_bounds_utc()

    open_leads_all = (
        db.query(LeadModel)
        .filter(LeadModel.stage.notin_(["CONVERTED", "LOST"]))
        .all()
    )
    overdue_count = 0
    today_count = 0
    upcoming_count = 0
    for ol in open_leads_all:
        bucket = compute_call_bucket(ol, today_start_utc, today_end_utc)
        if bucket == "OVERDUE":
            overdue_count += 1
        elif bucket == "TODAY":
            today_count += 1
        elif bucket == "UPCOMING" and ol.next_follow_up_at:
            nfa = ol.next_follow_up_at if ol.next_follow_up_at.tzinfo else ol.next_follow_up_at.replace(tzinfo=timezone.utc)
            if nfa <= upcoming_end_utc:
                upcoming_count += 1

    query = db.query(LeadModel)

    if stage and stage != "ALL":
        query = query.filter(LeadModel.stage == stage)

    if product_interest and product_interest != "ALL":
        query = query.filter(LeadModel.product_interest == product_interest)

    if priority and priority != "ALL":
        query = query.filter(LeadModel.priority == priority)

    if search and search.strip():
        term = f"%{search.strip()}%"
        digits_only = re.sub(r"\D", "", search)
        filters = [
            LeadModel.lead_number.ilike(term),
            LeadModel.full_name.ilike(term),
            LeadModel.trade_name.ilike(term),
            LeadModel.email.ilike(term)
        ]
        if digits_only:
            filters.append(LeadModel.phone_primary_normalized.ilike(f"%{digits_only}%"))
        query = query.filter(or_(*filters))

    if call_bucket and call_bucket != "ALL":
        query = query.filter(LeadModel.stage.notin_(["CONVERTED", "LOST"]))
        if call_bucket == "OVERDUE":
            query = query.filter(
                LeadModel.next_follow_up_at.isnot(None),
                LeadModel.next_follow_up_at < today_start_utc
            )
        elif call_bucket == "TODAY":
            query = query.filter(
                or_(
                    (LeadModel.next_follow_up_at >= today_start_utc) & (LeadModel.next_follow_up_at <= today_end_utc),
                    (LeadModel.next_follow_up_at.is_(None)) & (LeadModel.stage == "NEW")
                )
            )
        elif call_bucket == "UPCOMING":
            query = query.filter(
                LeadModel.next_follow_up_at.isnot(None),
                LeadModel.next_follow_up_at > today_end_utc
            )

    total = query.count()
    offset = (page - 1) * page_size
    items = (
        query.order_by(LeadModel.next_follow_up_at.asc().nullslast(), LeadModel.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "call_list_counts": {
            "open_total": len(open_leads_all),
            "overdue": overdue_count,
            "today": today_count,
            "upcoming": upcoming_count
        },
        "items": [serialize_lead(l, today_start_utc, today_end_utc) for l in items]
    }


@app.post("/api/leads", dependencies=[Depends(verify_csrf)])
def create_lead(
    payload: LeadCreateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    entity_type = payload.entity_type.strip().upper()
    source = payload.source.strip().upper()
    stage = payload.stage.strip().upper()
    priority = payload.priority.strip().upper()
    product_interest = payload.product_interest.strip()

    if entity_type not in VALID_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail="Invalid entity_type.")
    if source not in VALID_LEAD_SOURCES:
        raise HTTPException(status_code=422, detail="Invalid lead source.")
    if stage not in VALID_LEAD_STAGES or stage == "CONVERTED":
        raise HTTPException(status_code=422, detail="Invalid initial lead stage.")
    if priority not in VALID_LEAD_PRIORITIES:
        raise HTTPException(status_code=422, detail="Invalid priority.")
    if product_interest not in SUPPORTED_PRODUCTS:
        raise HTTPException(status_code=422, detail=f"Supported products: {', '.join(SUPPORTED_PRODUCTS)}")

    amount_cents = parse_pesos_to_cents(payload.requested_amount_pesos, payload.requested_amount_cents)
    if stage in QUALIFIED_OR_LATER_STAGES and amount_cents <= 0:
        raise HTTPException(
            status_code=422,
            detail=f"Stage {stage} requires a requested loan amount greater than $0.00 MXN."
        )

    phone_norm = normalize_phone_number(payload.phone_primary, required=True)
    email_clean = payload.email.strip().lower() if payload.email else None
    rfc_clean = payload.tax_id_rfc.strip().upper() if payload.tax_id_rfc and payload.tax_id_rfc.strip() else None
    follow_up_utc = parse_optional_datetime_utc(payload.next_follow_up_at)

    dup_open_phone = (
        db.query(LeadModel)
        .filter(
            LeadModel.phone_primary_normalized == phone_norm,
            LeadModel.stage.notin_(["CONVERTED", "LOST"])
        )
        .first()
    )
    if dup_open_phone:
        raise HTTPException(
            status_code=409,
            detail=f"An active lead with this phone number already exists ({dup_open_phone.lead_number} in stage {dup_open_phone.stage})."
        )

    if email_clean:
        dup_open_email = (
            db.query(LeadModel)
            .filter(
                LeadModel.email == email_clean,
                LeadModel.stage.notin_(["CONVERTED", "LOST"])
            )
            .first()
        )
        if dup_open_email:
            raise HTTPException(
                status_code=409,
                detail=f"An active lead with this email already exists ({dup_open_email.lead_number})."
            )

    try:
        lead_num = generate_next_lead_number(db)
        owner_id = payload.owner_user_id if payload.owner_user_id else current_user.id

        new_lead = LeadModel(
            lead_number=lead_num,
            full_name=payload.full_name.strip(),
            entity_type=entity_type,
            trade_name=payload.trade_name.strip() if payload.trade_name and payload.trade_name.strip() else None,
            legal_representative_name=payload.legal_representative_name.strip() if payload.legal_representative_name and payload.legal_representative_name.strip() else None,
            tax_id_rfc=rfc_clean,
            phone_primary=payload.phone_primary.strip(),
            phone_primary_normalized=phone_norm,
            email=email_clean,
            source=source,
            product_interest=product_interest,
            requested_amount_cents=amount_cents,
            currency="MXN",
            stage=stage,
            priority=priority,
            next_follow_up_at=follow_up_utc,
            summary_notes=payload.summary_notes.strip() if payload.summary_notes and payload.summary_notes.strip() else None,
            owner_user_id=owner_id,
            created_by_user_id=current_user.id
        )
        db.add(new_lead)
        db.flush()

        db.add(LeadStageHistoryModel(
            lead_id=new_lead.id,
            from_stage=None,
            to_stage=new_lead.stage,
            changed_by_user_id=current_user.id,
            reason_or_note="Initial lead registration"
        ))

        if new_lead.summary_notes:
            db.add(InteractionModel(
                lead_id=new_lead.id,
                customer_id=None,
                interaction_type="NOTE",
                direction="INTERNAL",
                visibility="INTERNAL",
                outcome=None,
                content=new_lead.summary_notes,
                next_follow_up_at=follow_up_utc,
                created_by_user_id=current_user.id
            ))

        record_audit_event(
            db, "LEAD_CREATED", "lead", "SUCCESS",
            actor=current_user, resource_id=new_lead.lead_number, ip_address=ip,
            details={
                "lead_number": new_lead.lead_number,
                "stage": new_lead.stage,
                "product": new_lead.product_interest,
                "amount_cents": new_lead.requested_amount_cents
            },
            commit=False
        )
        db.commit()
        db.refresh(new_lead)
        today_start_utc, today_end_utc, _, _ = get_local_day_bounds_utc()
        return serialize_lead(new_lead, today_start_utc, today_end_utc)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to create lead")
        raise HTTPException(status_code=500, detail="Could not create lead.")


@app.get("/api/leads/{lead_id}")
def get_lead_detail(
    lead_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    lead = db.query(LeadModel).filter(LeadModel.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    today_start_utc, today_end_utc, _, _ = get_local_day_bounds_utc()
    data = serialize_lead(lead, today_start_utc, today_end_utc)

    history_rows = (
        db.query(LeadStageHistoryModel)
        .filter(LeadStageHistoryModel.lead_id == lead.id)
        .order_by(LeadStageHistoryModel.changed_at.desc(), LeadStageHistoryModel.id.desc())
        .all()
    )
    interaction_rows = (
        db.query(InteractionModel)
        .filter(InteractionModel.lead_id == lead.id)
        .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
        .all()
    )

    user_ids = {h.changed_by_user_id for h in history_rows} | {i.created_by_user_id for i in interaction_rows}
    users = db.query(UserModel).filter(UserModel.id.in_(user_ids)).all() if user_ids else []
    user_map = {u.id: u for u in users}

    data["stage_history"] = [{
        "id": h.id,
        "from_stage": h.from_stage,
        "to_stage": h.to_stage,
        "changed_by_name": (user_map[h.changed_by_user_id].full_name or user_map[h.changed_by_user_id].email)
        if h.changed_by_user_id in user_map else f"User #{h.changed_by_user_id}",
        "reason_or_note": h.reason_or_note,
        "changed_at": h.changed_at.isoformat() if h.changed_at else None
    } for h in history_rows]

    data["interactions"] = [serialize_interaction(i, user_map) for i in interaction_rows]
    return data


@app.patch("/api/leads/{lead_id}", dependencies=[Depends(verify_csrf)])
def update_lead(
    lead_id: int,
    payload: LeadUpdateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        lead = db.query(LeadModel).filter(LeadModel.id == lead_id).with_for_update().first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found.")

        if lead.stage == "CONVERTED":
            raise HTTPException(
                status_code=400,
                detail="Converted leads are locked. Manage this borrower in the Customers module."
            )

        updated_fields = []
        old_stage = lead.stage

        if payload.full_name is not None:
            lead.full_name = payload.full_name.strip()
            updated_fields.append("full_name")

        if payload.entity_type is not None:
            et = payload.entity_type.strip().upper()
            if et not in VALID_ENTITY_TYPES:
                raise HTTPException(status_code=422, detail="Invalid entity_type.")
            lead.entity_type = et
            updated_fields.append("entity_type")

        if payload.trade_name is not None:
            lead.trade_name = payload.trade_name.strip() or None
            updated_fields.append("trade_name")

        if payload.legal_representative_name is not None:
            lead.legal_representative_name = payload.legal_representative_name.strip() or None
            updated_fields.append("legal_representative_name")

        if payload.tax_id_rfc is not None:
            lead.tax_id_rfc = payload.tax_id_rfc.strip().upper() or None
            updated_fields.append("tax_id_rfc")

        if payload.phone_primary is not None:
            phone_norm = normalize_phone_number(payload.phone_primary, required=True)
            dup_open = (
                db.query(LeadModel)
                .filter(
                    LeadModel.phone_primary_normalized == phone_norm,
                    LeadModel.id != lead.id,
                    LeadModel.stage.notin_(["CONVERTED", "LOST"])
                )
                .first()
            )
            if dup_open:
                raise HTTPException(
                    status_code=409,
                    detail=f"Phone number already belongs to active lead {dup_open.lead_number}."
                )
            lead.phone_primary = payload.phone_primary.strip()
            lead.phone_primary_normalized = phone_norm
            updated_fields.append("phone_primary")

        if payload.email is not None:
            email_clean = payload.email.strip().lower() if payload.email else None
            if email_clean:
                dup_email = (
                    db.query(LeadModel)
                    .filter(
                        LeadModel.email == email_clean,
                        LeadModel.id != lead.id,
                        LeadModel.stage.notin_(["CONVERTED", "LOST"])
                    )
                    .first()
                )
                if dup_email:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Email already belongs to active lead {dup_email.lead_number}."
                    )
            lead.email = email_clean
            updated_fields.append("email")

        if payload.source is not None:
            src = payload.source.strip().upper()
            if src not in VALID_LEAD_SOURCES:
                raise HTTPException(status_code=422, detail="Invalid lead source.")
            lead.source = src
            updated_fields.append("source")

        if payload.product_interest is not None:
            prod = payload.product_interest.strip()
            if prod not in SUPPORTED_PRODUCTS:
                raise HTTPException(status_code=422, detail="Invalid product_interest.")
            lead.product_interest = prod
            updated_fields.append("product_interest")

        if payload.requested_amount_pesos is not None or payload.requested_amount_cents is not None:
            lead.requested_amount_cents = parse_pesos_to_cents(
                payload.requested_amount_pesos, payload.requested_amount_cents
            )
            updated_fields.append("requested_amount_cents")

        if payload.priority is not None:
            prio = payload.priority.strip().upper()
            if prio not in VALID_LEAD_PRIORITIES:
                raise HTTPException(status_code=422, detail="Invalid priority.")
            lead.priority = prio
            updated_fields.append("priority")

        if payload.clear_next_follow_up:
            lead.next_follow_up_at = None
            updated_fields.append("next_follow_up_at")
        elif payload.next_follow_up_at is not None:
            lead.next_follow_up_at = parse_optional_datetime_utc(payload.next_follow_up_at)
            updated_fields.append("next_follow_up_at")

        if payload.summary_notes is not None:
            lead.summary_notes = payload.summary_notes.strip() or None
            updated_fields.append("summary_notes")

        if payload.lost_reason is not None:
            lead.lost_reason = payload.lost_reason.strip() or None
            updated_fields.append("lost_reason")

        if payload.stage is not None:
            new_stage = payload.stage.strip().upper()
            if new_stage not in VALID_LEAD_STAGES:
                raise HTTPException(status_code=422, detail="Invalid stage.")
            if new_stage == "CONVERTED":
                raise HTTPException(
                    status_code=400,
                    detail="Use POST /api/leads/{id}/convert to convert a lead into a customer."
                )
            if new_stage == "LOST" and not lead.lost_reason:
                raise HTTPException(
                    status_code=422,
                    detail="A lost_reason is required when moving a lead to LOST."
                )
            if new_stage == "NURTURE" and not lead.next_follow_up_at:
                raise HTTPException(
                    status_code=422,
                    detail="A next_follow_up_at date is required when moving a lead to NURTURE."
                )
            if new_stage in QUALIFIED_OR_LATER_STAGES and (lead.requested_amount_cents or 0) <= 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"Stage {new_stage} requires a requested amount greater than $0.00 MXN."
                )

            if new_stage != old_stage:
                lead.stage = new_stage
                if new_stage == "LOST":
                    lead.next_follow_up_at = None
                updated_fields.append("stage")
                db.add(LeadStageHistoryModel(
                    lead_id=lead.id,
                    from_stage=old_stage,
                    to_stage=new_stage,
                    changed_by_user_id=current_user.id,
                    reason_or_note=(payload.stage_change_note or lead.lost_reason or f"Stage updated to {new_stage}")[:500]
                ))
                record_audit_event(
                    db, "LEAD_STAGE_CHANGED", "lead", "SUCCESS",
                    actor=current_user, resource_id=lead.lead_number, ip_address=ip,
                    details={"from_stage": old_stage, "to_stage": new_stage},
                    commit=False
                )

        lead.updated_at = datetime.now(timezone.utc)

        record_audit_event(
            db, "LEAD_UPDATED", "lead", "SUCCESS",
            actor=current_user, resource_id=lead.lead_number, ip_address=ip,
            details={"lead_number": lead.lead_number, "updated_fields": ",".join(updated_fields)},
            commit=False
        )
        db.commit()
        db.refresh(lead)
        today_start_utc, today_end_utc, _, _ = get_local_day_bounds_utc()
        return serialize_lead(lead, today_start_utc, today_end_utc)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to update lead")
        raise HTTPException(status_code=500, detail="Could not update lead.")


@app.post("/api/leads/{lead_id}/interactions", dependencies=[Depends(verify_csrf)])
def create_lead_interaction(
    lead_id: int,
    payload: InteractionCreateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        lead = db.query(LeadModel).filter(LeadModel.id == lead_id).with_for_update().first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found.")

        itype = payload.interaction_type.strip().upper()
        direction = payload.direction.strip().upper()
        visibility = payload.visibility.strip().upper()
        outcome = payload.outcome.strip().upper() if payload.outcome and payload.outcome.strip() else None

        if itype not in VALID_INTERACTION_TYPES:
            raise HTTPException(status_code=422, detail="Invalid interaction_type.")
        if direction not in VALID_INTERACTION_DIRECTIONS:
            raise HTTPException(status_code=422, detail="Invalid direction.")
        if visibility not in VALID_INTERACTION_VISIBILITIES:
            raise HTTPException(status_code=422, detail="Invalid visibility.")
        if outcome and outcome not in VALID_CALL_OUTCOMES:
            raise HTTPException(status_code=422, detail="Invalid call outcome.")

        follow_up_utc = parse_optional_datetime_utc(payload.next_follow_up_at)
        now_utc = datetime.now(timezone.utc)

        interaction = InteractionModel(
            lead_id=lead.id,
            customer_id=lead.converted_customer_id,
            interaction_type=itype,
            direction=direction,
            visibility=visibility,
            outcome=outcome,
            content=payload.content.strip(),
            next_follow_up_at=follow_up_utc,
            created_by_user_id=current_user.id
        )
        db.add(interaction)

        if itype in ("CALL", "WHATSAPP", "MEETING", "EMAIL"):
            lead.last_contacted_at = now_utc
            if outcome:
                lead.last_interaction_outcome = outcome

        if payload.clear_next_follow_up:
            lead.next_follow_up_at = None
        elif follow_up_utc is not None:
            lead.next_follow_up_at = follow_up_utc

        if lead.stage == "NEW" and outcome:
            old_st = lead.stage
            if outcome in ("ANSWERED", "INTERESTED", "REQUESTED_CALLBACK", "FOLLOW_UP_REQUIRED"):
                lead.stage = "CONTACTED"
            elif outcome in ("NO_ANSWER", "VOICEMAIL", "WRONG_NUMBER"):
                lead.stage = "CONTACT_ATTEMPTED"
            if lead.stage != old_st:
                db.add(LeadStageHistoryModel(
                    lead_id=lead.id,
                    from_stage=old_st,
                    to_stage=lead.stage,
                    changed_by_user_id=current_user.id,
                    reason_or_note=f"Auto-advanced from {itype} outcome: {outcome}"
                ))

        lead.updated_at = now_utc
        db.flush()

        record_audit_event(
            db, "LEAD_INTERACTION_LOGGED", "lead", "SUCCESS",
            actor=current_user, resource_id=lead.lead_number, ip_address=ip,
            details={
                "lead_number": lead.lead_number,
                "interaction_type": itype,
                "outcome": outcome or "NONE",
                "stage": lead.stage
            },
            commit=False
        )
        db.commit()
        db.refresh(interaction)
        return serialize_interaction(interaction, {current_user.id: current_user})
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to log lead interaction")
        raise HTTPException(status_code=500, detail="Could not save lead interaction.")


@app.post("/api/leads/{lead_id}/convert", dependencies=[Depends(verify_csrf)])
def convert_lead_to_customer(
    lead_id: int,
    payload: LeadConvertRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        lead = db.query(LeadModel).filter(LeadModel.id == lead_id).with_for_update().first()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found.")

        if lead.stage == "CONVERTED" and lead.converted_customer_id:
            raise HTTPException(
                status_code=400,
                detail="This lead has already been converted to a customer."
            )
        if lead.stage == "LOST":
            raise HTTPException(
                status_code=400,
                detail="Cannot convert a lead marked as LOST. Reopen the lead stage first."
            )

        entity_type = (payload.entity_type or lead.entity_type or "INDIVIDUAL").strip().upper()
        if entity_type not in VALID_ENTITY_TYPES:
            raise HTTPException(status_code=422, detail="Invalid entity_type.")

        legal_rep = (
            payload.legal_representative_name.strip()
            if payload.legal_representative_name and payload.legal_representative_name.strip()
            else lead.legal_representative_name
        )
        trade_name = (
            payload.trade_name.strip()
            if payload.trade_name and payload.trade_name.strip()
            else lead.trade_name
        )
        rfc_clean = (
            payload.tax_id_rfc.strip().upper()
            if payload.tax_id_rfc and payload.tax_id_rfc.strip()
            else lead.tax_id_rfc
        )

        if entity_type == "BUSINESS" and not legal_rep:
            raise HTTPException(
                status_code=422,
                detail="Legal Representative Name is required to convert a Business (Persona Moral) lead."
            )

        match_phone = (
            db.query(CustomerModel)
            .filter(CustomerModel.phone_primary_normalized == lead.phone_primary_normalized)
            .first()
        )
        match_email = (
            db.query(CustomerModel)
            .filter(CustomerModel.email == lead.email)
            .first()
            if lead.email else None
        )
        match_rfc = (
            db.query(CustomerModel)
            .filter(CustomerModel.tax_id_rfc == rfc_clean)
            .first()
            if rfc_clean else None
        )

        matched_customers = {c.id: c for c in [match_phone, match_email, match_rfc] if c is not None}

        if len(matched_customers) > 1:
            conflict_nums = ", ".join(sorted(c.customer_number for c in matched_customers.values()))
            raise HTTPException(
                status_code=409,
                detail=f"Identity conflict: Phone/Email/RFC match different existing customers ({conflict_nums}). Resolve duplicate data before converting."
            )

        now_utc = datetime.now(timezone.utc)
        linked_existing = False

        if len(matched_customers) == 1:
            existing_cust = list(matched_customers.values())[0]
            lead_name_norm = re.sub(r"\s+", " ", (lead.full_name or "").strip()).upper()
            cust_name_norm = re.sub(r"\s+", " ", (existing_cust.full_legal_name or "").strip()).upper()

            if existing_cust.entity_type != entity_type or lead_name_norm != cust_name_norm:
                conflict_fields = []
                if match_phone and match_phone.id == existing_cust.id:
                    conflict_fields.append("primary phone")
                if match_email and match_email.id == existing_cust.id:
                    conflict_fields.append("email")
                if match_rfc and match_rfc.id == existing_cust.id:
                    conflict_fields.append("RFC")
                fields_str = ", ".join(conflict_fields) or "contact data"

                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot link lead '{lead.full_name}' ({entity_type}) to existing customer "
                        f"{existing_cust.customer_number} ('{existing_cust.full_legal_name}', {existing_cust.entity_type}) "
                        f"because their name or entity type does not match (shared {fields_str}). "
                        f"Please update the duplicate {fields_str} on the lead before converting."
                    )
                )

            target_customer = existing_cust
            linked_existing = True
        else:
            cust_number = generate_next_customer_number(db)
            target_customer = CustomerModel(
                customer_number=cust_number,
                entity_type=entity_type,
                status="ACTIVE",
                verification_status="PENDING_VERIFICATION",
                full_legal_name=lead.full_name,
                trade_name=trade_name,
                legal_representative_name=legal_rep,
                tax_id_rfc=rfc_clean,
                phone_primary=lead.phone_primary,
                phone_primary_normalized=lead.phone_primary_normalized,
                email=lead.email,
                address_full=payload.address_full.strip() if payload.address_full and payload.address_full.strip() else None,
                owner_user_id=lead.owner_user_id,
                created_by_user_id=current_user.id
            )
            db.add(target_customer)
            db.flush()

        db.query(InteractionModel).filter(
            InteractionModel.lead_id == lead.id,
            InteractionModel.customer_id.is_(None)
        ).update({"customer_id": target_customer.id}, synchronize_session=False)

        old_stage = lead.stage
        lead.stage = "CONVERTED"
        lead.converted_customer_id = target_customer.id
        lead.converted_at = now_utc
        lead.converted_by_user_id = current_user.id
        lead.next_follow_up_at = None
        lead.updated_at = now_utc

        conversion_note = (
            f"Lead {lead.lead_number} converted and linked to {'existing ' if linked_existing else 'new '}customer {target_customer.customer_number}."
        )
        if payload.notes and payload.notes.strip():
            conversion_note += f" Note: {payload.notes.strip()}"

        db.add(LeadStageHistoryModel(
            lead_id=lead.id,
            from_stage=old_stage,
            to_stage="CONVERTED",
            changed_by_user_id=current_user.id,
            reason_or_note=conversion_note[:500]
        ))

        db.add(InteractionModel(
            lead_id=lead.id,
            customer_id=target_customer.id,
            interaction_type="NOTE",
            direction="INTERNAL",
            visibility="INTERNAL",
            outcome=None,
            content=conversion_note,
            created_by_user_id=current_user.id
        ))

        record_audit_event(
            db, "LEAD_CONVERTED", "lead", "SUCCESS",
            actor=current_user, resource_id=lead.lead_number, ip_address=ip,
            details={
                "lead_number": lead.lead_number,
                "customer_number": target_customer.customer_number,
                "linked_existing": linked_existing
            },
            commit=False
        )
        db.commit()
        db.refresh(target_customer)
        active_loans_count = db.query(LoanModel).filter(LoanModel.customer_id == target_customer.id, LoanModel.status == "ACTIVE").count()
        unpaid_principal = sum(l.unpaid_principal_cents for l in target_customer.loans if l.status == "ACTIVE")
        return {
            "message": "Lead converted successfully",
            "linked_existing": linked_existing,
            "lead_id": lead.id,
            "lead_number": lead.lead_number,
            "customer": serialize_customer(target_customer, active_loans_count=active_loans_count, unpaid_principal_cents=unpaid_principal)
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Lead conversion failed")
        raise HTTPException(status_code=500, detail="Could not complete lead conversion.")


# --- STAGE 3 & BLOCK J: LOANS, AMORTIZATION SCHEDULES & DELINQUENCY METRICS ---
def serialize_loan_summary(l: LoanModel, today_local: date) -> dict:
    is_live_obligation = l.status in ("ACTIVE", "DEFAULTED")
    overdue_amt = 0
    due_today_amt = 0
    accrued_int = 0
    overdue_inst_count = 0
    oldest_overdue_date: Optional[date] = None

    if (is_live_obligation or l.status == "PAID_OFF") and l.installments:
        for inst in l.installments:
            if inst.remaining_amount_cents > 0:
                if inst.due_date < today_local:
                    overdue_amt += inst.remaining_amount_cents
                    overdue_inst_count += 1
                    if oldest_overdue_date is None or inst.due_date < oldest_overdue_date:
                        oldest_overdue_date = inst.due_date
                elif inst.due_date == today_local:
                    due_today_amt += inst.remaining_amount_cents

            if inst.due_date <= today_local:
                accrued_int += max(0, inst.interest_due_cents - inst.interest_paid_cents)

    days_past_due = (today_local - oldest_overdue_date).days if oldest_overdue_date else 0
    cust = l.customer

    return {
        "id": l.id,
        "loan_number": l.loan_number,
        "customer_id": l.customer_id,
        "customer_number": cust.customer_number if cust else f"CUST-{l.customer_id:05d}",
        "customer_name": cust.full_legal_name if cust else f"Customer #{l.customer_id}",
        "customer_phone": cust.phone_primary if cust else None,
        "status": l.status,
        "product_name": l.product_name,
        "currency": l.currency,
        "principal_cents": l.principal_cents,
        "monthly_interest_rate_percent": l.monthly_interest_rate_bps / 100.0,
        "term_installments_count": l.term_installments_count,
        "unpaid_principal_cents": l.unpaid_principal_cents,
        "remaining_contractual_interest_cents": l.remaining_contractual_interest_cents,
        "remaining_contract_total_cents": l.remaining_contract_total_cents,
        "accrued_interest_cents": accrued_int,
        "due_today_cents": due_today_amt,
        "overdue_amount_cents": overdue_amt,
        "overdue_installments_count": overdue_inst_count,
        "days_past_due": days_past_due,
        "current_payable_amount_cents": due_today_amt + overdue_amt,
        "start_date": l.start_date.isoformat(),
        "first_payment_date": l.first_payment_date.isoformat(),
        "maturity_date": l.maturity_date.isoformat(),
        "created_at": l.created_at.isoformat() if l.created_at else None
    }


def compute_early_payoff_breakdown(
    l: LoanModel,
    today_local: date,
    db: Optional[Session] = None,
    base_summary: Optional[dict] = None
) -> dict:
    rem_contract_total = int(
        (base_summary or {}).get("remaining_contract_total_cents")
        or getattr(l, "remaining_contract_total_cents", 0)
        or 0
    )
    if l.status != "ACTIVE":
        return {
            "unpaid_accrued_interest_cents": 0,
            "early_payoff_rebate_cents": 0,
            "early_payoff_amount_cents": rem_contract_total
        }

    installments = list(getattr(l, "installments", None) or [])
    if not installments and db is not None:
        installments = (
            db.query(LoanInstallmentModel)
            .filter(LoanInstallmentModel.loan_id == l.id)
            .order_by(LoanInstallmentModel.installment_number.asc())
            .all()
        )

    if not installments:
        return {
            "unpaid_accrued_interest_cents": 0,
            "early_payoff_rebate_cents": 0,
            "early_payoff_amount_cents": rem_contract_total
        }

    unpaid_principal = 0
    unpaid_accrued_interest = 0
    unearned_future_interest = 0

    for inst in installments:
        rem_p = max(0, int(inst.principal_due_cents or 0) - int(inst.principal_paid_cents or 0))
        rem_i = max(0, int(inst.interest_due_cents or 0) - int(inst.interest_paid_cents or 0))
        unpaid_principal += rem_p
        if inst.due_date and inst.due_date <= today_local:
            unpaid_accrued_interest += rem_i
        else:
            unearned_future_interest += rem_i

    return {
        "unpaid_accrued_interest_cents": unpaid_accrued_interest,
        "early_payoff_rebate_cents": unearned_future_interest,
        "early_payoff_amount_cents": unpaid_principal + unpaid_accrued_interest
    }


def serialize_loan_detail(l: LoanModel, today_local: date, db: Optional[Session] = None) -> dict:
    base = serialize_loan_summary(l, today_local)
    base.update(compute_early_payoff_breakdown(l, today_local, db=db, base_summary=base))

    defaulted_at_iso = None
    defaulted_by_user_id = None
    default_reason_str = None

    if l.status == "DEFAULTED" and db is not None:
        audit_row = (
            db.query(AuditLogModel)
            .filter(
                AuditLogModel.event_type == "LOAN_MARKED_DEFAULTED",
                AuditLogModel.resource_id == l.loan_number
            )
            .order_by(AuditLogModel.id.desc())
            .first()
        )
        if audit_row:
            defaulted_at_iso = audit_row.created_at.isoformat() if audit_row.created_at else None
            defaulted_by_user_id = audit_row.actor_user_id

        default_int = (
            db.query(InteractionModel)
            .filter(
                InteractionModel.customer_id == l.customer_id,
                InteractionModel.content.like(f"%LOAN MARKED AS DEFAULTED ({l.loan_number})%")
            )
            .order_by(InteractionModel.id.desc())
            .first()
        )
        if default_int and "Reason: " in default_int.content:
            default_reason_str = default_int.content.split("Reason: ", 1)[1].strip()

    base.update({
        "source_lead_id": l.source_lead_id,
        "product_version": l.product_version,
        "interest_calculation_method": l.interest_calculation_method,
        "interest_basis": l.interest_basis,
        "interest_accrual_convention": l.interest_accrual_convention,
        "assumed_interest_months": l.assumed_interest_months,
        "payment_frequency": l.payment_frequency,
        "rounding_policy": l.rounding_policy,
        "contract_rules": json.loads(l.contract_rules_json) if l.contract_rules_json else None,
        "monthly_interest_amount_cents": l.monthly_interest_amount_cents,
        "total_interest_cents": l.total_interest_cents,
        "total_repayment_cents": l.total_repayment_cents,
        "regular_installment_cents": l.regular_installment_cents,
        "final_installment_cents": l.final_installment_cents,
        "amount_paid_cents": l.amount_paid_cents,
        "owner_user_id": l.owner_user_id,
        "created_by_user_id": l.created_by_user_id,
        "approved_by_user_id": l.approved_by_user_id,
        "approved_at": l.approved_at.isoformat() if l.approved_at else None,
        "approval_notes": l.approval_notes,
        "activated_by_user_id": l.activated_by_user_id,
        "activated_at": l.activated_at.isoformat() if l.activated_at else None,
        "disbursed_at": l.disbursed_at.isoformat() if l.disbursed_at else None,
        "disbursement_method": l.disbursement_method,
        "disbursement_reference": l.disbursement_reference,
        "disbursement_notes": l.disbursement_notes,
        "cancelled_by_user_id": l.cancelled_by_user_id,
        "cancelled_at": l.cancelled_at.isoformat() if l.cancelled_at else None,
        "cancellation_reason": l.cancellation_reason,
        "defaulted_by_user_id": defaulted_by_user_id,
        "defaulted_at": defaulted_at_iso,
        "default_reason": default_reason_str,
        "notes": l.notes,
        "installments": []
    })

    for inst in l.installments:
        if inst.remaining_amount_cents == 0:
            status = "PAID"
        elif l.status in ("ACTIVE", "DEFAULTED") and inst.due_date < today_local:
            status = "OVERDUE"
        elif l.status in ("ACTIVE", "DEFAULTED") and inst.due_date == today_local:
            status = "DUE_TODAY"
        elif inst.amount_paid_cents > 0 and inst.remaining_amount_cents > 0:
            status = "PARTIALLY_PAID"
        else:
            status = "SCHEDULED"

        base["installments"].append({
            "id": inst.id,
            "installment_number": inst.installment_number,
            "due_date": inst.due_date.isoformat(),
            "beginning_principal_cents": inst.beginning_principal_cents,
            "principal_due_cents": inst.principal_due_cents,
            "interest_due_cents": inst.interest_due_cents,
            "scheduled_payment_cents": inst.scheduled_payment_cents,
            "ending_principal_cents": inst.ending_principal_cents,
            "principal_paid_cents": inst.principal_paid_cents,
            "interest_paid_cents": inst.interest_paid_cents,
            "amount_paid_cents": inst.amount_paid_cents,
            "remaining_amount_cents": inst.remaining_amount_cents,
            "paid_at": inst.paid_at.isoformat() if inst.paid_at else None,
            "status": status
        })
    return base

def serialize_loan_payment(p: LoanPaymentModel, include_allocations: bool = False, is_top_reversible: bool = False) -> dict:
    data = {
        "id": p.id,
        "payment_number": p.payment_number,
        "loan_id": p.loan_id,
        "status": p.status,
        "amount_cents": p.amount_cents,
        "interest_applied_cents": p.interest_applied_cents,
        "principal_applied_cents": p.principal_applied_cents,
        "payment_method": p.payment_method,
        "payment_reference": p.payment_reference,
        "paid_at": p.paid_at.isoformat(),
        "notes": p.notes,
        "recorded_by_user_id": p.recorded_by_user_id,
        "reversed_by_user_id": p.reversed_by_user_id,
        "reversed_at": p.reversed_at.isoformat() if p.reversed_at else None,
        "reversal_reason": p.reversal_reason,
        "created_at": p.created_at.isoformat(),
        "is_top_reversible": is_top_reversible
    }
    if include_allocations and p.allocations:
        data["allocations"] = [{
            "installment_number": a.installment_number,
            "interest_allocated_cents": a.interest_allocated_cents,
            "principal_allocated_cents": a.principal_allocated_cents,
            "total_allocated_cents": a.total_allocated_cents
        } for a in p.allocations]
    return data


@app.get("/api/loans")
def list_loans(
    search: Optional[str] = Query(default=None, max_length=120),
    status: Optional[str] = Query(default=None),
    customer_id: Optional[int] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    _, _, _, today_local = get_local_day_bounds_utc()
    query = db.query(LoanModel)

    if status and status != "ALL":
        query = query.filter(LoanModel.status == status)

    if customer_id:
        query = query.filter(LoanModel.customer_id == customer_id)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            LoanModel.loan_number.ilike(term),
            LoanModel.product_name.ilike(term)
        ))

    total = query.count()
    offset = (page - 1) * page_size
    items = (
        query.options(joinedload(LoanModel.customer), selectinload(LoanModel.installments))
        .order_by(LoanModel.id.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [serialize_loan_summary(l, today_local) for l in items]
    }


@app.post("/api/loans", dependencies=[Depends(verify_csrf)])
def create_loan(
    payload: LoanCreateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if payload.product_name not in SUPPORTED_PRODUCTS:
        raise HTTPException(status_code=422, detail=f"Supported products: {', '.join(SUPPORTED_PRODUCTS)}")

    if payload.term_installments_count < 1 or payload.term_installments_count > 104:
        raise HTTPException(status_code=422, detail="Loan term must be between 1 and 104 weekly installments.")

    if payload.assumed_interest_months < 1 or payload.assumed_interest_months > 36:
        raise HTTPException(status_code=422, detail="Assumed interest months must be between 1 and 36.")

    customer = db.query(CustomerModel).filter(CustomerModel.id == payload.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")
    if customer.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Cannot originate a loan for a non-active customer.")

    if payload.source_lead_id is not None:
        lead = db.query(LeadModel).filter(LeadModel.id == payload.source_lead_id).first()
        if not lead:
            raise HTTPException(status_code=404, detail="Source lead not found.")
        if lead.stage != "CONVERTED" or lead.converted_customer_id != customer.id:
            raise HTTPException(
                status_code=400,
                detail="Source lead must be in CONVERTED stage and linked to the selected customer."
            )

    if payload.principal_pesos is not None and payload.principal_cents is not None:
        pesos_in_cents = parse_pesos_to_cents(payload.principal_pesos, None)
        if pesos_in_cents != int(payload.principal_cents):
            raise HTTPException(
                status_code=422,
                detail="Conflicting principal_pesos and principal_cents values provided."
            )

    principal_cents = parse_pesos_to_cents(payload.principal_pesos, payload.principal_cents)
    if principal_cents <= 0:
        raise HTTPException(status_code=422, detail="Loan principal amount must be greater than zero.")
    if principal_cents > 1_000_000_000:
        raise HTTPException(
            status_code=422,
            detail="Loan principal amount cannot exceed $10,000,000.00 MXN."
        )

    try:
        start_dt = date.fromisoformat(payload.start_date)
        first_pay_dt = date.fromisoformat(payload.first_payment_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format. Use YYYY-MM-DD.")

    _, _, _, today_local = get_local_day_bounds_utc()
    if start_dt < today_local:
        raise HTTPException(
            status_code=422,
            detail=f"Contract start date ({start_dt.isoformat()}) cannot be earlier than current local date ({today_local.isoformat()})."
        )

    if first_pay_dt < start_dt:
        raise HTTPException(status_code=422, detail="First payment date cannot be earlier than start date.")

    calc_method = (payload.interest_calculation_method or "FLAT_ORIGINAL_PRINCIPAL").strip().upper()
    if calc_method not in ("FLAT_ORIGINAL_PRINCIPAL", "DECLINING_BALANCE"):
        raise HTTPException(
            status_code=422,
            detail="Invalid interest_calculation_method. Allowed: FLAT_ORIGINAL_PRINCIPAL, DECLINING_BALANCE."
        )
    interest_basis_val = "DECLINING_PRINCIPAL" if calc_method == "DECLINING_BALANCE" else "ORIGINAL_PRINCIPAL"
    rounding_policy_val = "FRENCH_LEVEL_EXACT_FINAL" if calc_method == "DECLINING_BALANCE" else "FLOOR_REGULAR_EXACT_FINAL"

    # Calculate deterministic schedule on server
    calc = LoanFinancialEngine.calculate_flat_schedule(
        principal_pesos=principal_cents / 100.0,
        monthly_rate_percent=payload.monthly_interest_rate_percent,
        assumed_interest_months=payload.assumed_interest_months,
        term_weeks=payload.term_installments_count,
        first_payment_date_iso=payload.first_payment_date,
        interest_calculation_method=calc_method
    )

    canonical_payload = {
        "customer_id": payload.customer_id,
        "source_lead_id": payload.source_lead_id,
        "product_name": payload.product_name,
        "interest_calculation_method": calc_method,
        "principal_cents": calc["principal_cents"],
        "monthly_interest_rate_bps": calc["monthly_interest_rate_bps"],
        "assumed_interest_months": calc["months_count"],
        "term_installments_count": calc["installments_count"],
        "start_date": payload.start_date,
        "first_payment_date": payload.first_payment_date
    }
    payload_json = json.dumps(canonical_payload, sort_keys=True)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()

    existing = db.query(LoanModel).filter(LoanModel.idempotency_key == payload.idempotency_key).first()
    if existing:
        if existing.request_payload_hash == payload_hash:
            _, _, _, today_local = get_local_day_bounds_utc()
            return {"idempotent_replay": True, "loan": serialize_loan_detail(existing, today_local)}
        else:
            raise HTTPException(status_code=409, detail="Idempotency key was already used with different loan parameters.")

    try:
        if db.bind and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(9004001)"))

        # Double check after lock
        existing_locked = db.query(LoanModel).filter(LoanModel.idempotency_key == payload.idempotency_key).first()
        if existing_locked:
            if existing_locked.request_payload_hash == payload_hash:
                db.rollback()
                _, _, _, today_local = get_local_day_bounds_utc()
                return {"idempotent_replay": True, "loan": serialize_loan_detail(existing_locked, today_local)}
            else:
                db.rollback()
                raise HTTPException(status_code=409, detail="Idempotency key conflict under concurrency.")

        loan_number = generate_next_loan_number(db)

        contract_rules = {
            "engine_version": "1.1.0",
            "interest_calculation_method": calc_method,
            "interest_basis": interest_basis_val,
            "interest_accrual_convention": "ON_INSTALLMENT_DUE_DATE",
            "assumed_interest_months": calc["months_count"],
            "term_installments_count": calc["installments_count"],
            "payment_frequency": "WEEKLY",
            "rounding_policy": rounding_policy_val,
            "partial_period_prorating_rule": "NOT_DEFINED_EXPLICIT_MONTHS_USED",
            "early_payoff_rebate_rule": "NOT_DEFINED_PENDING_POLICY",
            "late_fee_moratory_rule": "NOT_DEFINED_PENDING_POLICY"
        }

        new_loan = LoanModel(
            loan_number=loan_number,
            idempotency_key=payload.idempotency_key,
            request_payload_hash=payload_hash,
            customer_id=payload.customer_id,
            source_lead_id=payload.source_lead_id,
            status="DRAFT",
            product_name=payload.product_name,
            product_version="v1.1",
            currency="MXN",
            principal_cents=calc["principal_cents"],
            monthly_interest_rate_bps=calc["monthly_interest_rate_bps"],
            interest_calculation_method=calc_method,
            interest_basis=interest_basis_val,
            interest_accrual_convention="ON_INSTALLMENT_DUE_DATE",
            assumed_interest_months=calc["months_count"],
            payment_frequency="WEEKLY",
            term_installments_count=calc["installments_count"],
            rounding_policy=rounding_policy_val,
            contract_rules_json=json.dumps(contract_rules),
            monthly_interest_amount_cents=calc["monthly_interest_amount_cents"],
            total_interest_cents=calc["total_interest_cents"],
            total_repayment_cents=calc["total_repayment_cents"],
            regular_installment_cents=calc["regular_installment_cents"],
            final_installment_cents=calc["final_installment_cents"],
            start_date=start_dt,
            first_payment_date=calc["first_payment_date"],
            maturity_date=calc["maturity_date"],
            unpaid_principal_cents=0,
            remaining_contractual_interest_cents=0,
            remaining_contract_total_cents=0,
            amount_paid_cents=0,
            owner_user_id=customer.owner_user_id,
            created_by_user_id=current_user.id,
            notes=payload.notes.strip() if payload.notes else None
        )
        db.add(new_loan)
        db.flush()

        for inst in calc["schedule"]:
            db.add(LoanInstallmentModel(
                loan_id=new_loan.id,
                installment_number=inst["installment_number"],
                due_date=inst["due_date"],
                beginning_principal_cents=inst["beginning_principal_cents"],
                principal_due_cents=inst["principal_due_cents"],
                interest_due_cents=inst["interest_due_cents"],
                scheduled_payment_cents=inst["scheduled_payment_cents"],
                ending_principal_cents=inst["ending_principal_cents"],
                principal_paid_cents=0,
                interest_paid_cents=0,
                amount_paid_cents=0,
                remaining_amount_cents=inst["scheduled_payment_cents"]
            ))

        record_audit_event(
            db, "LOAN_DRAFT_CREATED", "loan", "SUCCESS",
            actor=current_user, resource_id=new_loan.loan_number, ip_address=ip,
            details={
                "loan_number": new_loan.loan_number,
                "customer_id": new_loan.customer_id,
                "principal_cents": new_loan.principal_cents
            },
            commit=False
        )
        db.commit()
        db.refresh(new_loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return {"idempotent_replay": False, "loan": serialize_loan_detail(new_loan, today_local)}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to create loan draft")
        raise HTTPException(status_code=500, detail=f"Could not create loan draft: {type(e).__name__}")


@app.get("/api/loans/{loan_id}")
def get_loan_detail(
    loan_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    loan = db.query(LoanModel).filter(LoanModel.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
    _, _, _, today_local = get_local_day_bounds_utc()
    return serialize_loan_detail(loan, today_local, db=db)


@app.post("/api/loans/{loan_id}/approve", dependencies=[Depends(verify_csrf)])
def approve_loan(
    loan_id: int,
    payload: LoanApproveRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        loan = db.query(LoanModel).filter(LoanModel.id == loan_id).with_for_update().first()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status != "DRAFT":
            raise HTTPException(status_code=409, detail=f"Only DRAFT loans can be approved. Current status: {loan.status}")

        now_utc = datetime.now(timezone.utc)
        loan.status = "APPROVED"
        loan.approved_by_user_id = current_user.id
        loan.approved_at = now_utc
        if payload.approval_notes:
            loan.approval_notes = payload.approval_notes.strip()
        loan.updated_at = now_utc

        record_audit_event(
            db, "LOAN_APPROVED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={"loan_number": loan.loan_number}, commit=False
        )
        db.commit()
        db.refresh(loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return serialize_loan_detail(loan, today_local)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to approve loan")
        raise HTTPException(status_code=500, detail=f"Could not approve loan: {type(e).__name__}")


@app.post("/api/loans/{loan_id}/activate", dependencies=[Depends(verify_csrf)])
def activate_loan(
    loan_id: int,
    payload: LoanActivateRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if not payload.confirm_disbursement:
        raise HTTPException(status_code=422, detail="Disbursement confirmation is required to activate a loan.")
    if payload.disbursement_method not in VALID_DISBURSEMENT_METHODS:
        raise HTTPException(status_code=422, detail=f"Invalid disbursement method. Allowed: {', '.join(sorted(VALID_DISBURSEMENT_METHODS))}")

    disbursed_at_dt = parse_optional_datetime_utc(payload.disbursed_at) or datetime.now(timezone.utc)
    if disbursed_at_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Disbursement time cannot be in the future.")

    try:
        loan = db.query(LoanModel).filter(LoanModel.id == loan_id).with_for_update().first()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")

        if loan.status == "ACTIVE":
            raise HTTPException(status_code=409, detail=f"Loan {loan.loan_number} is already active.")
        if loan.status == "CANCELLED":
            raise HTTPException(status_code=409, detail=f"Cannot activate loan {loan.loan_number} because it was cancelled.")
        if loan.status != "APPROVED":
            raise HTTPException(status_code=409, detail=f"Loan must be in APPROVED status before activation. Current: {loan.status}")

        now_utc = datetime.now(timezone.utc)
        
        # Asignación limpia y ordenada exigida por el trigger de Postgres
        loan.unpaid_principal_cents = int(loan.principal_cents)
        loan.remaining_contractual_interest_cents = int(loan.total_interest_cents)
        loan.remaining_contract_total_cents = int(loan.total_repayment_cents)
        loan.amount_paid_cents = 0
        
        loan.activated_by_user_id = current_user.id
        loan.activated_at = now_utc
        loan.disbursed_at = disbursed_at_dt
        loan.disbursement_method = payload.disbursement_method
        loan.disbursement_reference = payload.disbursement_reference.strip()
        if payload.disbursement_notes:
            loan.disbursement_notes = payload.disbursement_notes.strip()
            
        loan.status = "ACTIVE"
        loan.updated_at = now_utc

        db.flush() # Fuerza la validación del trigger antes del commit final

        record_audit_event(
            db, "LOAN_ACTIVATED_DISBURSED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "method": loan.disbursement_method,
                "reference": loan.disbursement_reference
            },
            commit=False
        )
        db.commit()
        db.refresh(loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return serialize_loan_detail(loan, today_local)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to activate loan")
        raise HTTPException(status_code=500, detail="Could not activate loan.")


@app.post("/api/loans/{loan_id}/cancel", dependencies=[Depends(verify_csrf)])
def cancel_loan(
    loan_id: int,
    payload: LoanCancelRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    reason = payload.cancellation_reason.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Cancellation reason must be at least 3 characters.")

    try:
        loan = db.query(LoanModel).filter(LoanModel.id == loan_id).with_for_update().first()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status == "ACTIVE":
            raise HTTPException(status_code=409, detail="Active disbursed loans cannot be cancelled.")
        if loan.status == "CANCELLED":
            raise HTTPException(status_code=409, detail="Loan is already cancelled.")
        if loan.status not in ("DRAFT", "APPROVED"):
            raise HTTPException(status_code=409, detail=f"Loans in status {loan.status} cannot be cancelled.")

        now_utc = datetime.now(timezone.utc)
        loan.status = "CANCELLED"
        loan.cancelled_by_user_id = current_user.id
        loan.cancelled_at = now_utc
        loan.cancellation_reason = reason
        loan.unpaid_principal_cents = 0
        loan.remaining_contractual_interest_cents = 0
        loan.remaining_contract_total_cents = 0
        loan.amount_paid_cents = 0
        loan.updated_at = now_utc

        record_audit_event(
            db, "LOAN_CANCELLED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={"loan_number": loan.loan_number, "reason": reason},
            commit=False
        )
        db.commit()
        db.refresh(loan)
        _, _, _, today_local = get_local_day_bounds_utc()
        return serialize_loan_detail(loan, today_local, db=db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to cancel loan")
        raise HTTPException(status_code=500, detail=f"Could not cancel loan: {type(e).__name__}")


@app.post("/api/loans/{loan_id}/default", dependencies=[Depends(verify_csrf)])
def mark_loan_defaulted(
    loan_id: int,
    payload: LoanDefaultRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    if not payload.confirm_default:
        raise HTTPException(status_code=422, detail="Explicit confirmation is required to mark a loan as DEFAULTED.")

    reason = payload.default_reason.strip()
    if len(reason) < 5:
        raise HTTPException(status_code=422, detail="Default reason must be at least 5 characters.")

    try:
        loan = db.query(LoanModel).filter(LoanModel.id == loan_id).with_for_update().first()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status == "DEFAULTED":
            raise HTTPException(status_code=409, detail=f"Loan {loan.loan_number} is already marked as DEFAULTED.")
        if loan.status != "ACTIVE":
            raise HTTPException(
                status_code=409,
                detail=f"Only ACTIVE loans can be transitioned to DEFAULTED. Current status: {loan.status}"
            )
        if loan.remaining_contract_total_cents <= 0:
            raise HTTPException(status_code=409, detail="Cannot default a loan with zero remaining balance.")

        now_utc = datetime.now(timezone.utc)
        _, _, _, today_local = get_local_day_bounds_utc()
        summary_snapshot = serialize_loan_summary(loan, today_local)

        # Keep immutable origination loan.notes untouched
        loan.status = "DEFAULTED"
        loan.updated_at = now_utc

        db.add(InteractionModel(
            lead_id=loan.source_lead_id,
            customer_id=loan.customer_id,
            interaction_type="NOTE",
            direction="INTERNAL",
            visibility="INTERNAL",
            outcome="FOLLOW_UP_REQUIRED",
            content=(
                f"⚠️ LOAN MARKED AS DEFAULTED ({loan.loan_number}) — "
                f"Unpaid Principal: ${loan.unpaid_principal_cents / 100:,.2f} MXN | "
                f"Remaining Total: ${loan.remaining_contract_total_cents / 100:,.2f} MXN | "
                f"Days Past Due: {summary_snapshot['days_past_due']}d | "
                f"Reason: {reason}"
            ),
            created_by_user_id=current_user.id
        ))

        record_audit_event(
            db, "LOAN_MARKED_DEFAULTED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "customer_id": loan.customer_id,
                "unpaid_principal_cents": loan.unpaid_principal_cents,
                "remaining_total_cents": loan.remaining_contract_total_cents,
                "overdue_amount_cents": summary_snapshot["overdue_amount_cents"],
                "days_past_due": summary_snapshot["days_past_due"]
            },
            commit=False
        )
        db.commit()
        db.refresh(loan)
        return serialize_loan_detail(loan, today_local, db=db)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to mark loan as defaulted")
        raise HTTPException(status_code=500, detail=f"Could not mark loan as defaulted: {type(e).__name__}")


@app.post("/api/loans/{loan_id}/collections-log", dependencies=[Depends(verify_csrf)])
def create_loan_collection_log(
    loan_id: int,
    payload: LoanCollectionLogRequest,
    request: Request,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    loan = db.query(LoanModel).filter(LoanModel.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
    if loan.status not in ("ACTIVE", "DEFAULTED"):
        raise HTTPException(status_code=409, detail="Collection logs can only be recorded on ACTIVE or DEFAULTED loans.")

    itype = payload.interaction_type.strip().upper()
    outcome = payload.outcome.strip().upper()
    if itype not in VALID_INTERACTION_TYPES:
        raise HTTPException(status_code=422, detail="Invalid interaction_type.")
    if outcome not in VALID_CALL_OUTCOMES:
        raise HTTPException(status_code=422, detail="Invalid call outcome.")

    follow_up_utc = parse_optional_datetime_utc(payload.next_follow_up_at)
    promised_cents = parse_pesos_to_cents(payload.promised_amount_pesos, None) if payload.promised_amount_pesos else 0

    header_parts = [f"[COLLECTION {loan.loan_number}]"]
    if promised_cents > 0:
        header_parts.append(f"Promised: ${promised_cents / 100:,.2f} MXN")
    if follow_up_utc:
        header_parts.append(f"Follow-up: {follow_up_utc.astimezone(get_crm_timezone()).strftime('%Y-%m-%d %H:%M')}")
    formatted_content = f"{' • '.join(header_parts)} — {payload.notes.strip()}"

    try:
        interaction = InteractionModel(
            lead_id=loan.source_lead_id,
            customer_id=loan.customer_id,
            interaction_type=itype,
            direction="OUTBOUND",
            visibility="INTERNAL",
            outcome=outcome,
            content=formatted_content,
            next_follow_up_at=follow_up_utc,
            created_by_user_id=current_user.id
        )
        db.add(interaction)
        db.flush()

        record_audit_event(
            db, "LOAN_COLLECTION_LOGGED", "loan", "SUCCESS",
            actor=current_user, resource_id=loan.loan_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "customer_id": loan.customer_id,
                "interaction_type": itype,
                "outcome": outcome,
                "promised_cents": promised_cents
            },
            commit=False
        )
        db.commit()
        db.refresh(interaction)
        return serialize_interaction(interaction, {current_user.id: current_user})
    except Exception as e:
        db.rollback()
        logger.exception("Failed to record collection log")
        raise HTTPException(status_code=500, detail=f"Could not save collection log: {type(e).__name__}")


@app.get("/api/collections/queue")
def get_collections_queue(
    bucket: str = Query(default="ALL"),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    _, _, _, today_local = get_local_day_bounds_utc()
    candidate_loans = (
        db.query(LoanModel)
        .options(joinedload(LoanModel.customer), selectinload(LoanModel.installments))
        .filter(LoanModel.status.in_(["ACTIVE", "DEFAULTED"]))
        .order_by(LoanModel.id.desc())
        .all()
    )

    cust_ids = {l.customer_id for l in candidate_loans}
    latest_interactions = {}
    if cust_ids:
        int_rows = (
            db.query(InteractionModel)
            .filter(InteractionModel.customer_id.in_(cust_ids))
            .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
            .all()
        )
        for ir in int_rows:
            if ir.customer_id not in latest_interactions:
                latest_interactions[ir.customer_id] = ir

    queue_items = []
    overdue_active_count = 0
    due_today_active_count = 0
    defaulted_count = 0
    total_overdue_cents = 0
    total_defaulted_cents = 0

    bucket_norm = (bucket or "ALL").strip().upper()

    for l in candidate_loans:
        s = serialize_loan_summary(l, today_local)
        is_defaulted = (l.status == "DEFAULTED")
        is_overdue = (l.status == "ACTIVE" and s["overdue_amount_cents"] > 0)
        is_due_today = (l.status == "ACTIVE" and s["overdue_amount_cents"] == 0 and s["due_today_cents"] > 0)

        if not (is_defaulted or is_overdue or is_due_today):
            continue

        if is_defaulted:
            defaulted_count += 1
            total_defaulted_cents += l.remaining_contract_total_cents
        elif is_overdue:
            overdue_active_count += 1
            total_overdue_cents += s["overdue_amount_cents"]
        elif is_due_today:
            due_today_active_count += 1

        if bucket_norm == "OVERDUE" and not is_overdue:
            continue
        if bucket_norm == "DEFAULTED" and not is_defaulted:
            continue
        if bucket_norm == "DUE_TODAY" and not is_due_today:
            continue

        last_int = latest_interactions.get(l.customer_id)
        s["last_collection_outcome"] = last_int.outcome if last_int else None
        s["last_collection_note"] = last_int.content[:180] if last_int else None
        s["last_contacted_at"] = last_int.created_at.isoformat() if last_int and last_int.created_at else None
        s["next_follow_up_at"] = last_int.next_follow_up_at.isoformat() if last_int and last_int.next_follow_up_at else None
        queue_items.append(s)

    queue_items.sort(
        key=lambda x: (
            1 if x["status"] == "DEFAULTED" else 0,
            x["days_past_due"],
            x["overdue_amount_cents"],
            x["current_payable_amount_cents"]
        ),
        reverse=True
    )

    return {
        "counts": {
            "overdue_active": overdue_active_count,
            "due_today_active": due_today_active_count,
            "defaulted": defaulted_count,
            "total_actionable": overdue_active_count + due_today_active_count + defaulted_count
        },
        "totals": {
            "overdue_active_cents": total_overdue_cents,
            "defaulted_remaining_cents": total_defaulted_cents
        },
        "items": queue_items
    }


@app.get("/api/loans/{loan_id}/payments")
def list_loan_payments(
    loan_id: int,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    loan = db.query(LoanModel).filter(LoanModel.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found.")
        
    payments = db.query(LoanPaymentModel).filter(LoanPaymentModel.loan_id == loan_id).order_by(LoanPaymentModel.loan_payment_seq.desc()).all()
    top_posted_id = next((p.id for p in payments if p.status == 'POSTED'), None)
    
    return [serialize_loan_payment(p, include_allocations=True, is_top_reversible=(p.id == top_posted_id)) for p in payments]


@app.post("/api/loans/{loan_id}/payments", dependencies=[Depends(verify_csrf)])
def create_loan_payment(
    loan_id: int,
    payload: LoanPaymentCreateRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    amount_cents = parse_pesos_to_cents(payload.amount_pesos, payload.amount_cents)
    if amount_cents <= 0:
        raise HTTPException(status_code=422, detail="Payment amount must be greater than zero.")
    if payload.payment_method not in VALID_PAYMENT_METHODS:
        raise HTTPException(status_code=422, detail="Invalid payment method.")

    explicit_paid_at_dt = parse_optional_datetime_utc(payload.paid_at)
    if explicit_paid_at_dt and explicit_paid_at_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="Payment effective date cannot be in the future.")

    payment_ref_clean = (payload.payment_reference or "").strip() or None
    notes_clean = (payload.notes or "").strip() or None
    apply_rebate = bool(getattr(payload, "apply_early_payoff_rebate", False))

    # Hash determinista: si el cliente omite paid_at se hashea None, nunca datetime.now()
    canonical = {
        "loan_id": loan_id,
        "amount_cents": amount_cents,
        "payment_method": payload.payment_method,
        "payment_reference": payment_ref_clean.upper() if payment_ref_clean else None,
        "paid_at": explicit_paid_at_dt.isoformat() if explicit_paid_at_dt else None,
        "apply_early_payoff_rebate": apply_rebate,
        "notes": notes_clean
    }
    payload_hash = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()

    def _build_replay_response(existing_pay: LoanPaymentModel, target_loan: Optional[LoanModel] = None) -> dict:
        _, _, _, today_replay = get_local_day_bounds_utc()
        loan_obj = target_loan or db.query(LoanModel).filter(LoanModel.id == existing_pay.loan_id).first()
        latest_posted = (
            db.query(LoanPaymentModel.id)
            .filter(LoanPaymentModel.loan_id == existing_pay.loan_id, LoanPaymentModel.status == "POSTED")
            .order_by(LoanPaymentModel.loan_payment_seq.desc())
            .first()
        )
        is_top = bool(latest_posted and latest_posted[0] == existing_pay.id)
        return {
            "idempotent_replay": True,
            "payment": serialize_loan_payment(existing_pay, include_allocations=True, is_top_reversible=is_top),
            "loan": serialize_loan_detail(loan_obj, today_replay, db=db) if loan_obj else None
        }

    existing = db.query(LoanPaymentModel).filter(LoanPaymentModel.idempotency_key == payload.idempotency_key).first()
    if existing:
        if existing.request_payload_hash == payload_hash:
            return _build_replay_response(existing)
        raise HTTPException(status_code=409, detail="Idempotency key collision with different payload.")

    try:
        loan = db.query(LoanModel).filter(LoanModel.id == loan_id).with_for_update().first()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")

        # Re-verificar idempotencia bajo el lock del préstamo ANTES de validar estatus, saldo o referencia duplicada
        existing_locked = db.query(LoanPaymentModel).filter(LoanPaymentModel.idempotency_key == payload.idempotency_key).first()
        if existing_locked:
            if existing_locked.request_payload_hash == payload_hash:
                replay_resp = _build_replay_response(existing_locked, target_loan=loan)
                db.rollback()
                return replay_resp
            db.rollback()
            raise HTTPException(status_code=409, detail="Concurrency collision on idempotency key.")

        if loan.status != "ACTIVE":
            raise HTTPException(status_code=409, detail=f"Payments can only be applied to ACTIVE loans. Current: {loan.status}")
        if amount_cents > loan.remaining_contract_total_cents:
            raise HTTPException(status_code=422, detail=f"Amount exceeds remaining balance. Max allowed: ${loan.remaining_contract_total_cents / 100:,.2f} MXN")

        if payload.payment_method in ("SPEI_TRANSFER", "BANK_DEPOSIT", "CHECK") and payment_ref_clean:
            ref_upper = payment_ref_clean.upper()
            dup_ref = db.query(LoanPaymentModel).filter(
                LoanPaymentModel.payment_method == payload.payment_method,
                func.upper(func.trim(LoanPaymentModel.payment_reference)) == ref_upper,
                LoanPaymentModel.status == "POSTED"
            ).first()
            if dup_ref:
                raise HTTPException(status_code=409, detail=f"Duplicate payment reference detected: {ref_upper}")

        paid_at_dt = explicit_paid_at_dt or datetime.now(timezone.utc)

        installments = db.query(LoanInstallmentModel).filter(
            LoanInstallmentModel.loan_id == loan_id, LoanInstallmentModel.remaining_amount_cents > 0
        ).order_by(LoanInstallmentModel.installment_number.asc()).with_for_update().all()

        _, _, _, today_local = get_local_day_bounds_utc()
        rebated_installments_map = {}
        total_rebated_interest_cents = 0
        effective_notes = notes_clean

        if apply_rebate:
            if current_user.role not in ("OWNER_ADMIN", "CREDIT_ADVISOR"):
                raise HTTPException(
                    status_code=403,
                    detail="Only OWNER_ADMIN or CREDIT_ADVISOR can authorize an early payoff interest rebate."
                )

            unpaid_principal = 0
            unpaid_accrued_interest = 0
            unearned_future_interest = 0
            for inst in installments:
                rem_p = max(0, int(inst.principal_due_cents or 0) - int(inst.principal_paid_cents or 0))
                rem_i = max(0, int(inst.interest_due_cents or 0) - int(inst.interest_paid_cents or 0))
                unpaid_principal += rem_p
                if inst.due_date and inst.due_date <= today_local:
                    unpaid_accrued_interest += rem_i
                else:
                    unearned_future_interest += rem_i

            expected_early_cents = unpaid_principal + unpaid_accrued_interest
            if unearned_future_interest <= 0:
                raise HTTPException(
                    status_code=422,
                    detail="This loan has no unearned future interest eligible for early payoff rebate."
                )
            if amount_cents != expected_early_cents:
                raise HTTPException(
                    status_code=422,
                    detail=f"Early payoff with interest rebate requires exact settlement amount of ${expected_early_cents / 100:,.2f} MXN."
                )

            for inst in installments:
                if inst.due_date and inst.due_date > today_local:
                    rem_i = max(0, int(inst.interest_due_cents or 0) - int(inst.interest_paid_cents or 0))
                    if rem_i > 0:
                        rebated_installments_map[str(inst.id)] = rem_i
                        total_rebated_interest_cents += rem_i
                        inst.interest_due_cents = int(inst.interest_paid_cents or 0)
                        inst.scheduled_payment_cents = int(inst.principal_due_cents or 0) + inst.interest_due_cents
                        if hasattr(inst, "remaining_interest_cents"):
                            inst.remaining_interest_cents = 0
                        inst.remaining_amount_cents = max(0, inst.scheduled_payment_cents - int(inst.amount_paid_cents or 0))

            loan.total_interest_cents = max(0, int(loan.total_interest_cents or 0) - total_rebated_interest_cents)
            loan.total_repayment_cents = int(loan.principal_cents or 0) + loan.total_interest_cents
            loan.remaining_contractual_interest_cents = max(0, int(loan.remaining_contractual_interest_cents or 0) - total_rebated_interest_cents)
            loan.remaining_contract_total_cents = int(loan.unpaid_principal_cents or 0) + loan.remaining_contractual_interest_cents

            rebate_tag = f"[Early Payoff Rebate: -${total_rebated_interest_cents / 100:,.2f} MXN future interest forgiven]"
            effective_notes = f"{effective_notes} {rebate_tag}".strip() if effective_notes else rebate_tag

        result = LoanPaymentEngine.execute_waterfall_allocation(
            db=db, loan=loan, installments=installments, amount_cents=amount_cents,
            payment_method=payload.payment_method, payment_reference=payment_ref_clean,
            paid_at_dt=paid_at_dt, notes=effective_notes, user_id=current_user.id,
            idempotency_key=payload.idempotency_key, payload_hash=payload_hash,
            rebate_detail_json=serialize_rebate_detail(rebated_installments_map)
        )

        record_audit_event(
            db, "LOAN_PAYMENT_RECORDED", "loan", "SUCCESS", actor=current_user,
            resource_id=result["payment"].payment_number, ip_address=ip,
            details={
                "loan_number": loan.loan_number,
                "amount_cents": amount_cents,
                "method": payload.payment_method,
                "early_payoff_rebate_cents": total_rebated_interest_cents,
                "rebated_installments_count": len(rebated_installments_map)
            },
            commit=False
        )
        db.commit()
        db.refresh(result["payment"])
        return {
            "idempotent_replay": False,
            "payment": serialize_loan_payment(result["payment"], include_allocations=True, is_top_reversible=True),
            "loan": serialize_loan_detail(loan, today_local, db=db)
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as e:
        db.rollback()
        orig_msg = str(getattr(e, "orig", e))
        if "uq_loan_payments_posted_reference" in orig_msg:
            raise HTTPException(status_code=409, detail="Duplicate payment reference detected.")
        if "uq_loan_payments_idempotency_key" in orig_msg:
            raise HTTPException(status_code=409, detail="Concurrency collision on idempotency key.")
        logger.exception("Integrity error while recording payment")
        raise HTTPException(status_code=500, detail="Payment could not be recorded.")
    except Exception:
        db.rollback()
        logger.exception("Failed to record payment")
        raise HTTPException(status_code=500, detail="Payment could not be recorded.")


@app.post("/api/loans/{loan_id}/payments/{payment_id}/reverse", dependencies=[Depends(verify_csrf)])
def reverse_loan_payment(
    loan_id: int,
    payment_id: int,
    payload: LoanPaymentReverseRequest,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        loan = db.query(LoanModel).filter(LoanModel.id == loan_id).with_for_update().first()
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found.")
        if loan.status == "DEFAULTED":
            raise HTTPException(status_code=409, detail="Cannot reverse payments on a DEFAULTED loan.")

        target_payment = db.query(LoanPaymentModel).filter(
            LoanPaymentModel.id == payment_id, LoanPaymentModel.loan_id == loan_id
        ).with_for_update().first()
        
        if not target_payment:
            raise HTTPException(status_code=404, detail="Payment not found.")
        if target_payment.status == "REVERSED":
            raise HTTPException(status_code=409, detail="Payment is already reversed.")

        latest_payment = db.query(LoanPaymentModel.id).filter(
            LoanPaymentModel.loan_id == loan_id, LoanPaymentModel.status == 'POSTED'
        ).order_by(LoanPaymentModel.loan_payment_seq.desc()).first()
        
        if not latest_payment or latest_payment[0] != payment_id:
            raise HTTPException(status_code=409, detail="Only the most recent POSTED payment can be reversed to preserve ledger integrity.")

        LoanPaymentEngine.execute_reversal_restoration(db, loan, target_payment, payload.reversal_reason.strip(), current_user.id)

        # Restore rebated future interest if this payment was an early payoff settlement.
        # Fuente de verdad: loan_payments.rebate_detail_json. Cualquier error aqui aborta TODA la
        # reversa (rollback): nunca se debe dejar el prestamo a medio restaurar.
        rebated_map = parse_rebate_detail(target_payment.rebate_detail_json)
        if rebated_map:
            rebated_insts = (
                db.query(LoanInstallmentModel)
                .filter(
                    LoanInstallmentModel.loan_id == loan.id,
                    LoanInstallmentModel.id.in_(list(rebated_map.keys()))
                )
                .with_for_update()
                .all()
            )
            inst_by_id = {int(i.id): i for i in rebated_insts}
            missing_ids = set(rebated_map.keys()) - set(inst_by_id.keys())
            if missing_ids:
                raise ValueError(f"Data corruption: rebated installments not found: {sorted(missing_ids)}")

            restored_total_cents = 0
            for inst_id, add_cents in rebated_map.items():
                inst_obj = inst_by_id[inst_id]
                inst_obj.interest_due_cents = int(inst_obj.interest_due_cents or 0) + add_cents
                inst_obj.scheduled_payment_cents = int(inst_obj.principal_due_cents or 0) + inst_obj.interest_due_cents
                inst_obj.remaining_amount_cents = max(
                    0, inst_obj.scheduled_payment_cents - int(inst_obj.amount_paid_cents or 0)
                )
                restored_total_cents += add_cents

            # execute_reversal_restoration dejo los saldos del prestamo en sus valores "prev_*"
            # (ya con el rebate aplicado); sumar de vuelta lo perdonado reconstruye el estado original.
            loan.total_interest_cents = int(loan.total_interest_cents or 0) + restored_total_cents
            loan.total_repayment_cents = int(loan.principal_cents or 0) + loan.total_interest_cents
            loan.remaining_contractual_interest_cents = (
                int(loan.remaining_contractual_interest_cents or 0) + restored_total_cents
            )
            loan.remaining_contract_total_cents = (
                int(loan.remaining_contract_total_cents or 0) + restored_total_cents
            )
            db.flush()

        record_audit_event(
            db, "LOAN_PAYMENT_REVERSED", "loan", "SUCCESS", actor=current_user,
            resource_id=target_payment.payment_number, ip_address=ip,
            details={"loan_number": loan.loan_number, "amount_cents": target_payment.amount_cents, "reason": payload.reversal_reason}, commit=False
        )
        db.commit()
        db.refresh(target_payment)
        _, _, _, today_local = get_local_day_bounds_utc()
        return {
            "message": "Payment reversed successfully",
            "payment": serialize_loan_payment(target_payment),
            "loan": serialize_loan_detail(loan, today_local, db=db)
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.exception("Failed to reverse payment")
        raise HTTPException(status_code=500, detail=f"Reversal failed: {type(e).__name__}")


@app.get("/api/dashboard/summary")
def get_dashboard_summary(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    _, _, _, today_local = get_local_day_bounds_utc()
    active_loans = db.query(LoanModel).filter(LoanModel.status == "ACTIVE").all()
    paid_off_loans_count = db.query(LoanModel).filter(LoanModel.status == "PAID_OFF").count()
    defaulted_loans_count = db.query(LoanModel).filter(LoanModel.status == "DEFAULTED").count()

    active_portfolio_cents = sum(l.unpaid_principal_cents for l in active_loans)
    total_outstanding_principal_cents = active_portfolio_cents
    total_contract_due_cents = sum(l.remaining_contract_total_cents for l in active_loans)

    due_today_cents = 0
    overdue_amount_cents = 0
    due_today_installments_count = 0
    overdue_installments_count = 0
    overdue_loan_ids = set()

    if active_loans:
        loan_ids = [l.id for l in active_loans]
        unpaid_insts = (
            db.query(LoanInstallmentModel)
            .filter(LoanInstallmentModel.loan_id.in_(loan_ids), LoanInstallmentModel.remaining_amount_cents > 0)
            .all()
        )
        for inst in unpaid_insts:
            if inst.due_date == today_local:
                due_today_cents += inst.remaining_amount_cents
                due_today_installments_count += 1
            elif inst.due_date < today_local:
                overdue_amount_cents += inst.remaining_amount_cents
                overdue_installments_count += 1
                overdue_loan_ids.add(inst.loan_id)

    customers_count = db.query(CustomerModel).count()
    open_leads_count = db.query(LeadModel).filter(LeadModel.stage.notin_(["CONVERTED", "LOST"])).count()
    active_loans_count = len(active_loans)
    overdue_loans_count = len(overdue_loan_ids)
    current_loans_count = max(0, active_loans_count - overdue_loans_count)

    # Bloque G-2: Semana ISO para pagos cobrados
    crm_tz = get_crm_timezone()
    now_local = datetime.now(crm_tz)
    monday_local = (now_local - timedelta(days=now_local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday_local = (monday_local + timedelta(days=6)).replace(hour=23, minute=59, second=59, microsecond=999999)

    collected_week_query = db.query(
        func.coalesce(func.sum(LoanPaymentModel.amount_cents), 0),
        func.count(LoanPaymentModel.id)
    ).filter(
        LoanPaymentModel.status == 'POSTED',
        LoanPaymentModel.paid_at >= monday_local.astimezone(timezone.utc),
        LoanPaymentModel.paid_at <= sunday_local.astimezone(timezone.utc)
    ).first()

    collected_week_cents = int(collected_week_query[0] or 0) if collected_week_query else 0
    collected_week_payments_count = int(collected_week_query[1] or 0) if collected_week_query else 0

    return {
        "active_loan_portfolio_cents": active_portfolio_cents,
        "due_today_cents": due_today_cents,
        "overdue_amount_cents": overdue_amount_cents,
        "total_outstanding_principal_cents": total_outstanding_principal_cents,
        "total_amount_due_cents": total_contract_due_cents,
        "collected_this_week_enabled": True,
        "collected_this_week_cents": collected_week_cents,
        "counts": {
            "active_loans": active_loans_count,
            "current_loans": current_loans_count,
            "overdue_loans": overdue_loans_count,
            "paid_off_loans": paid_off_loans_count,
            "defaulted_loans": defaulted_loans_count,
            "due_today_installments": due_today_installments_count,
            "overdue_installments": overdue_installments_count,
            "collected_week_payments": collected_week_payments_count,
            "customers": customers_count,
            "open_leads": open_leads_count
        }
    }

# --- CONVERSATIONS & NURA ASSISTANT ---
@app.get("/api/conversations")
def listar_conversaciones(current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    convs = (
        db.query(ConversationModel)
        .filter(ConversationModel.user_id == current_user.id)
        .order_by(ConversationModel.updated_at.desc())
        .all()
    )
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in convs]


@app.post("/api/conversations", dependencies=[Depends(verify_csrf)])
def crear_conversacion(payload: dict, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    nueva = ConversationModel(user_id=current_user.id, title=str(payload.get("title", "New Consultation"))[:60])
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return {"id": nueva.id, "title": nueva.title}


@app.get("/api/conversations/{conv_id}/messages")
def obtener_mensajes(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = (
        db.query(ConversationModel)
        .filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = (
        db.query(ChatMessageModel)
        .filter(ChatMessageModel.conversation_id == conv_id)
        .order_by(ChatMessageModel.created_at.asc())
        .all()
    )
    return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in msgs]


@app.delete("/api/conversations/{conv_id}", dependencies=[Depends(verify_csrf)])
def eliminar_conversacion(conv_id: int, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    conv = (
        db.query(ConversationModel)
        .filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id)
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"message": "Deleted"}


@app.post("/chat-ejecutivo", dependencies=[Depends(verify_csrf)])
def chat_ejecutivo(
    req: ChatRequest,
    request: Request,
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        conv_id = req.conversation_id
        if conv_id:
            conv = (
                db.query(ConversationModel)
                .filter(ConversationModel.id == conv_id, ConversationModel.user_id == current_user.id)
                .first()
            )
            if not conv:
                raise HTTPException(status_code=403, detail="Unauthorized conversation")
        else:
            conv = ConversationModel(user_id=current_user.id, title=req.mensaje[:30] + "...")
            db.add(conv)
            db.commit()
            db.refresh(conv)
            conv_id = conv.id

        prior_messages = (
            db.query(ChatMessageModel)
            .filter(ChatMessageModel.conversation_id == conv_id)
            .order_by(ChatMessageModel.created_at.asc())
            .all()
        )

        db.add(ChatMessageModel(conversation_id=conv_id, role="user", content=req.mensaje))
        db.commit()

        respuesta_texto = AIService.generate_reply(
            user_message=req.mensaje,
            prior_messages=prior_messages,
            user_role=current_user.role
        )

        db.add(ChatMessageModel(conversation_id=conv_id, role="executive", content=respuesta_texto))
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()

        record_audit_event(
            db, "AI_CHAT_REQUEST", "chat", "SUCCESS",
            actor=current_user, resource_id=str(conv_id), ip_address=ip,
            details={"msg_len": len(req.mensaje), "role": current_user.role}, commit=True
        )
        return {"conversation_id": conv_id, "respuesta": respuesta_texto}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Chat error: {type(e).__name__}")
        raise HTTPException(status_code=500, detail="Error processing consultation.")


@app.get("/", response_class=FileResponse)
def home():
    return FileResponse("index.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
