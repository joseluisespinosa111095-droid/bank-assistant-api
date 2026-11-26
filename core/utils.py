from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import os
import re
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session


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


def next_tenant_sequence(db: Session, tenant_id: int, entity_type: str) -> int:
    row = db.execute(
        text("""
            INSERT INTO tenant_sequences (tenant_id, entity_type, last_value)
            VALUES (:tenant_id, :entity_type, 1)
            ON CONFLICT (tenant_id, entity_type) DO UPDATE
            SET last_value = tenant_sequences.last_value + 1
            RETURNING last_value
        """),
        {"tenant_id": tenant_id, "entity_type": entity_type}
    ).fetchone()
    return int(row[0])


def generate_next_customer_number(db: Session, tenant_id: int) -> str:
    return f"CUST-{next_tenant_sequence(db, tenant_id, 'customers'):05d}"


def generate_next_lead_number(db: Session, tenant_id: int) -> str:
    return f"LD-{next_tenant_sequence(db, tenant_id, 'leads'):05d}"


def generate_next_loan_number(db: Session, tenant_id: int) -> str:
    return f"LN-{next_tenant_sequence(db, tenant_id, 'loans'):05d}"


def generate_next_payment_number(db: Session, tenant_id: int) -> str:
    return f"PAY-{next_tenant_sequence(db, tenant_id, 'loan_payments'):06d}"


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
