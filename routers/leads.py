import calendar
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.config import (
    QUALIFIED_OR_LATER_STAGES,
    SUPPORTED_PRODUCTS,
    VALID_CALL_OUTCOMES,
    VALID_ENTITY_TYPES,
    VALID_FOLLOW_UP_TYPES,
    VALID_INTERACTION_DIRECTIONS,
    VALID_INTERACTION_TYPES,
    VALID_INTERACTION_VISIBILITIES,
    VALID_LEAD_PRIORITIES,
    VALID_LEAD_SOURCES,
    VALID_LEAD_STAGES,
    logger,
)
from core.security import get_client_ip
from core.utils import (
    generate_next_customer_number,
    generate_next_lead_number,
    get_crm_timezone,
    get_local_day_bounds_utc,
    normalize_phone_number,
    parse_optional_datetime_utc,
    parse_pesos_to_cents,
)
from dependencies.auth import require_roles, resolve_tenant_owner_user_id, verify_csrf
from dependencies.db import get_db
from models import CustomerModel, InteractionModel, LeadModel, LeadStageHistoryModel, UserModel
from schemas.core_crm import (
    InteractionCreateRequest,
    LeadConvertRequest,
    LeadCreateRequest,
    LeadUpdateRequest,
)
from services.audit import record_audit_event
from services.serializers_core import (
    compute_call_bucket,
    normalize_and_validate_curp,
    resolve_lead_name_for_create,
    resolve_lead_name_for_update,
    serialize_customer,
    serialize_interaction,
    serialize_lead,
)

router = APIRouter()


# --- STAGE 2: LEADS, CALL LIST & CONVERSION ---
@router.get("/api/leads")
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
        .filter(
            LeadModel.tenant_id == current_user.tenant_id,
            LeadModel.stage.notin_(["CONVERTED", "LOST"])
        )
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

    query = db.query(LeadModel).filter(LeadModel.tenant_id == current_user.tenant_id)

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
            LeadModel.curp.ilike(term),
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


@router.post("/api/leads", dependencies=[Depends(verify_csrf)])
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

    try:
        name_fields = resolve_lead_name_for_create(
            entity_type=entity_type,
            first_name=payload.first_name,
            middle_name=payload.middle_name,
            last_name=payload.last_name,
            full_name=payload.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    curp_clean = None
    if entity_type == "INDIVIDUAL":
        curp_required = bool(name_fields["used_split"]) or (payload.curp is not None)
        try:
            curp_clean = normalize_and_validate_curp(payload.curp, required=curp_required)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    follow_up_type = None
    if payload.next_follow_up_type and payload.next_follow_up_type.strip():
        ft = payload.next_follow_up_type.strip().upper()
        if ft not in VALID_FOLLOW_UP_TYPES:
            raise HTTPException(status_code=422, detail="Invalid next_follow_up_type. Allowed: CALL, MEETING.")
        follow_up_type = ft
    elif follow_up_utc is not None:
        follow_up_type = "CALL"

    dup_open_phone = (
        db.query(LeadModel)
        .filter(
            LeadModel.tenant_id == current_user.tenant_id,
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
                LeadModel.tenant_id == current_user.tenant_id,
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
        owner_id = resolve_tenant_owner_user_id(db, current_user.tenant_id, payload.owner_user_id, current_user.id)
        lead_num = generate_next_lead_number(db, current_user.tenant_id)

        new_lead = LeadModel(
            tenant_id=current_user.tenant_id,
            lead_number=lead_num,
            full_name=name_fields["full_name"],
            first_name=name_fields["first_name"],
            middle_name=name_fields["middle_name"],
            last_name=name_fields["last_name"],
            entity_type=entity_type,
            trade_name=payload.trade_name.strip() if payload.trade_name and payload.trade_name.strip() else None,
            legal_representative_name=payload.legal_representative_name.strip() if payload.legal_representative_name and payload.legal_representative_name.strip() else None,
            tax_id_rfc=rfc_clean,
            curp=curp_clean,
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
            next_follow_up_type=follow_up_type,
            summary_notes=payload.summary_notes.strip() if payload.summary_notes and payload.summary_notes.strip() else None,
            owner_user_id=owner_id,
            created_by_user_id=current_user.id
        )
        db.add(new_lead)
        db.flush()

        db.add(LeadStageHistoryModel(
            tenant_id=current_user.tenant_id,
            lead_id=new_lead.id,
            from_stage=None,
            to_stage=new_lead.stage,
            changed_by_user_id=current_user.id,
            reason_or_note="Initial lead registration"
        ))

        if new_lead.summary_notes:
            db.add(InteractionModel(
                tenant_id=current_user.tenant_id,
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


@router.get("/api/leads/calendar")
def get_leads_calendar(
    year: int = Query(..., ge=2020, le=2100),
    month: int = Query(..., ge=1, le=12),
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db),
):
    """Scheduled calls & in-person appointments for a given local calendar month (tenant-scoped)."""
    crm_tz = get_crm_timezone()
    last_day = calendar.monthrange(year, month)[1]
    month_start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=crm_tz)
    month_end_local = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=crm_tz)
    month_start_utc = month_start_local.astimezone(timezone.utc)
    month_end_utc = month_end_local.astimezone(timezone.utc)

    def _to_local_dt(dt_val: datetime) -> datetime:
        if dt_val.tzinfo is None:
            dt_val = dt_val.replace(tzinfo=timezone.utc)
        return dt_val.astimezone(crm_tz)

    events: list[dict] = []
    seen_keys: set[tuple] = set()

    # 1. Open leads with scheduled next_follow_up_at in the requested month
    lead_rows = (
        db.query(LeadModel)
        .filter(
            LeadModel.tenant_id == current_user.tenant_id,
            LeadModel.stage.notin_(["CONVERTED", "LOST"]),
            LeadModel.next_follow_up_at.isnot(None),
            LeadModel.next_follow_up_at >= month_start_utc,
            LeadModel.next_follow_up_at <= month_end_utc,
        )
        .order_by(LeadModel.next_follow_up_at.asc(), LeadModel.id.asc())
        .all()
    )
    for l in lead_rows:
        local_dt = _to_local_dt(l.next_follow_up_at)
        act_type = "MEETING" if (l.next_follow_up_type or "").upper() == "MEETING" else "CALL"
        day_iso = local_dt.date().isoformat()
        time_hm = local_dt.strftime("%H:%M")
        key = ("LEAD", l.id, day_iso, time_hm)
        seen_keys.add(key)
        events.append({
            "id": f"lead-{l.id}",
            "date": day_iso,
            "time": time_hm,
            "scheduled_at": local_dt.isoformat(),
            "activity_type": act_type,
            "person_name": l.full_name,
            "entity_kind": "LEAD",
            "lead_id": l.id,
            "customer_id": None,
            "record_number": l.lead_number,
            "phone_primary": l.phone_primary,
            "stage": l.stage,
            "product_interest": l.product_interest,
            "notes": l.summary_notes,
        })

    # 2. Scheduled interactions (for customers or additional appointments on leads) in the month
    inter_rows = (
        db.query(InteractionModel)
        .filter(
            InteractionModel.tenant_id == current_user.tenant_id,
            InteractionModel.next_follow_up_at.isnot(None),
            InteractionModel.next_follow_up_at >= month_start_utc,
            InteractionModel.next_follow_up_at <= month_end_utc,
        )
        .order_by(InteractionModel.next_follow_up_at.asc(), InteractionModel.id.asc())
        .all()
    )
    if inter_rows:
        lead_ids = {i.lead_id for i in inter_rows if i.lead_id is not None}
        cust_ids = {i.customer_id for i in inter_rows if i.customer_id is not None}
        leads_map = (
            {
                l.id: l
                for l in db.query(LeadModel)
                .filter(LeadModel.tenant_id == current_user.tenant_id, LeadModel.id.in_(lead_ids))
                .all()
            }
            if lead_ids
            else {}
        )
        custs_map = (
            {
                c.id: c
                for c in db.query(CustomerModel)
                .filter(CustomerModel.tenant_id == current_user.tenant_id, CustomerModel.id.in_(cust_ids))
                .all()
            }
            if cust_ids
            else {}
        )
        for inter in inter_rows:
            local_dt = _to_local_dt(inter.next_follow_up_at)
            day_iso = local_dt.date().isoformat()
            time_hm = local_dt.strftime("%H:%M")

            raw_ft = (inter.next_follow_up_type or "").upper()
            if raw_ft in VALID_FOLLOW_UP_TYPES:
                act_type = raw_ft
            elif (inter.interaction_type or "").upper() == "MEETING":
                act_type = "MEETING"
            else:
                act_type = "CALL"

            if inter.lead_id and inter.lead_id in leads_map:
                l = leads_map[inter.lead_id]
                if ("LEAD", l.id, day_iso, time_hm) in seen_keys:
                    continue
                seen_keys.add(("LEAD", l.id, day_iso, time_hm))
                events.append({
                    "id": f"int-{inter.id}",
                    "date": day_iso,
                    "time": time_hm,
                    "scheduled_at": local_dt.isoformat(),
                    "activity_type": act_type,
                    "person_name": l.full_name,
                    "entity_kind": "LEAD",
                    "lead_id": l.id,
                    "customer_id": l.converted_customer_id,
                    "record_number": l.lead_number,
                    "phone_primary": l.phone_primary,
                    "stage": l.stage,
                    "product_interest": l.product_interest,
                    "notes": inter.content,
                })
            elif inter.customer_id and inter.customer_id in custs_map:
                c = custs_map[inter.customer_id]
                if ("CUSTOMER", c.id, day_iso, time_hm) in seen_keys:
                    continue
                seen_keys.add(("CUSTOMER", c.id, day_iso, time_hm))
                events.append({
                    "id": f"int-{inter.id}",
                    "date": day_iso,
                    "time": time_hm,
                    "scheduled_at": local_dt.isoformat(),
                    "activity_type": act_type,
                    "person_name": c.full_legal_name,
                    "entity_kind": "CUSTOMER",
                    "lead_id": None,
                    "customer_id": c.id,
                    "record_number": c.customer_number,
                    "phone_primary": c.phone_primary,
                    "stage": c.status,
                    "product_interest": None,
                    "notes": inter.content,
                })

    events.sort(key=lambda e: (e["date"], e["time"], e["person_name"]))
    calls_count = sum(1 for e in events if e["activity_type"] == "CALL")
    meetings_count = sum(1 for e in events if e["activity_type"] == "MEETING")
    return {
        "year": year,
        "month": month,
        "timezone": str(crm_tz),
        "counts": {
            "total": len(events),
            "calls": calls_count,
            "meetings": meetings_count,
        },
        "events": events,
    }


@router.get("/api/leads/{lead_id}")
def get_lead_detail(
    lead_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    lead = (
        db.query(LeadModel)
        .filter(LeadModel.id == lead_id, LeadModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    today_start_utc, today_end_utc, _, _ = get_local_day_bounds_utc()
    data = serialize_lead(lead, today_start_utc, today_end_utc)

    history_rows = (
        db.query(LeadStageHistoryModel)
        .filter(
            LeadStageHistoryModel.tenant_id == current_user.tenant_id,
            LeadStageHistoryModel.lead_id == lead.id
        )
        .order_by(LeadStageHistoryModel.changed_at.desc(), LeadStageHistoryModel.id.desc())
        .all()
    )
    interaction_rows = (
        db.query(InteractionModel)
        .filter(
            InteractionModel.tenant_id == current_user.tenant_id,
            InteractionModel.lead_id == lead.id
        )
        .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
        .all()
    )

    user_ids = {h.changed_by_user_id for h in history_rows} | {i.created_by_user_id for i in interaction_rows}
    users = (
        db.query(UserModel)
        .filter(UserModel.tenant_id == current_user.tenant_id, UserModel.id.in_(user_ids))
        .all()
        if user_ids else []
    )
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


@router.patch("/api/leads/{lead_id}", dependencies=[Depends(verify_csrf)])
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
        lead = (
            db.query(LeadModel)
            .filter(LeadModel.id == lead_id, LeadModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found.")

        if lead.stage == "CONVERTED":
            raise HTTPException(
                status_code=400,
                detail="Converted leads are locked. Manage this borrower in the Customers module."
            )

        updated_fields = []
        old_stage = lead.stage

        final_entity_type = lead.entity_type
        type_changed = False
        if payload.entity_type is not None:
            et = payload.entity_type.strip().upper()
            if et not in VALID_ENTITY_TYPES:
                raise HTTPException(status_code=422, detail="Invalid entity_type.")
            if et != lead.entity_type:
                type_changed = True
                lead.entity_type = et
                updated_fields.append("entity_type")
            final_entity_type = et

        try:
            name_updates = resolve_lead_name_for_update(
                final_entity_type=final_entity_type,
                type_changed=type_changed,
                current_first=lead.first_name,
                current_middle=lead.middle_name,
                current_last=lead.last_name,
                first_name=payload.first_name,
                middle_name=payload.middle_name,
                last_name=payload.last_name,
                full_name=payload.full_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        if name_updates:
            for k, v in name_updates.items():
                setattr(lead, k, v)
                updated_fields.append(k)

        if final_entity_type == "BUSINESS":
            if lead.curp is not None:
                lead.curp = None
                updated_fields.append("curp")
        elif payload.curp is not None:
            try:
                lead.curp = normalize_and_validate_curp(payload.curp, required=True)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            updated_fields.append("curp")

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
                    LeadModel.tenant_id == current_user.tenant_id,
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
                        LeadModel.tenant_id == current_user.tenant_id,
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

        if payload.owner_user_id is not None:
            lead.owner_user_id = resolve_tenant_owner_user_id(
                db, current_user.tenant_id, payload.owner_user_id, lead.owner_user_id
            )
            updated_fields.append("owner_user_id")

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
            lead.next_follow_up_type = None
            updated_fields.append("next_follow_up_at")
        elif payload.next_follow_up_at is not None:
            lead.next_follow_up_at = parse_optional_datetime_utc(payload.next_follow_up_at)
            updated_fields.append("next_follow_up_at")
            if payload.next_follow_up_type is None and lead.next_follow_up_at is not None and not lead.next_follow_up_type:
                lead.next_follow_up_type = "CALL"

        if payload.next_follow_up_type is not None:
            ft = payload.next_follow_up_type.strip().upper()
            if ft and ft not in VALID_FOLLOW_UP_TYPES:
                raise HTTPException(status_code=422, detail="Invalid next_follow_up_type. Allowed: CALL, MEETING.")
            lead.next_follow_up_type = ft or None
            updated_fields.append("next_follow_up_type")

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
                    tenant_id=current_user.tenant_id,
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


@router.post("/api/leads/{lead_id}/interactions", dependencies=[Depends(verify_csrf)])
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
        lead = (
            db.query(LeadModel)
            .filter(LeadModel.id == lead_id, LeadModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
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
        follow_up_type = None
        if payload.next_follow_up_type and payload.next_follow_up_type.strip():
            ft = payload.next_follow_up_type.strip().upper()
            if ft not in VALID_FOLLOW_UP_TYPES:
                raise HTTPException(status_code=422, detail="Invalid next_follow_up_type. Allowed: CALL, MEETING.")
            follow_up_type = ft
        elif follow_up_utc is not None:
            follow_up_type = "MEETING" if itype == "MEETING" else "CALL"

        now_utc = datetime.now(timezone.utc)

        interaction = InteractionModel(
            tenant_id=current_user.tenant_id,
            lead_id=lead.id,
            customer_id=lead.converted_customer_id,
            interaction_type=itype,
            direction=direction,
            visibility=visibility,
            outcome=outcome,
            content=payload.content.strip(),
            next_follow_up_at=follow_up_utc,
            next_follow_up_type=follow_up_type,
            created_by_user_id=current_user.id
        )
        db.add(interaction)

        if itype in ("CALL", "WHATSAPP", "MEETING", "EMAIL"):
            lead.last_contacted_at = now_utc
            if outcome:
                lead.last_interaction_outcome = outcome

        if payload.clear_next_follow_up:
            lead.next_follow_up_at = None
            lead.next_follow_up_type = None
        elif follow_up_utc is not None:
            lead.next_follow_up_at = follow_up_utc
            lead.next_follow_up_type = follow_up_type

        if lead.stage == "NEW" and outcome:
            old_st = lead.stage
            if outcome in ("ANSWERED", "INTERESTED", "REQUESTED_CALLBACK", "FOLLOW_UP_REQUIRED"):
                lead.stage = "CONTACTED"
            elif outcome in ("NO_ANSWER", "VOICEMAIL", "WRONG_NUMBER"):
                lead.stage = "CONTACT_ATTEMPTED"
            if lead.stage != old_st:
                db.add(LeadStageHistoryModel(
                    tenant_id=current_user.tenant_id,
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


@router.post("/api/leads/{lead_id}/convert", dependencies=[Depends(verify_csrf)])
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
        lead = (
            db.query(LeadModel)
            .filter(LeadModel.id == lead_id, LeadModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
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

        curp_clean = None
        if entity_type == "INDIVIDUAL":
            raw_curp = payload.curp if payload.curp is not None else lead.curp
            if raw_curp:
                try:
                    curp_clean = normalize_and_validate_curp(raw_curp, required=False)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc))

        if entity_type == "BUSINESS" and not legal_rep:
            raise HTTPException(
                status_code=422,
                detail="Legal Representative Name is required to convert a Business (Persona Moral) lead."
            )

        match_phone = (
            db.query(CustomerModel)
            .filter(
                CustomerModel.tenant_id == current_user.tenant_id,
                CustomerModel.phone_primary_normalized == lead.phone_primary_normalized
            )
            .first()
        )
        match_email = (
            db.query(CustomerModel)
            .filter(
                CustomerModel.tenant_id == current_user.tenant_id,
                CustomerModel.email == lead.email
            )
            .first()
            if lead.email else None
        )
        match_rfc = (
            db.query(CustomerModel)
            .filter(
                CustomerModel.tenant_id == current_user.tenant_id,
                CustomerModel.tax_id_rfc == rfc_clean
            )
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
            cust_number = generate_next_customer_number(db, current_user.tenant_id)
            target_customer = CustomerModel(
                tenant_id=current_user.tenant_id,
                customer_number=cust_number,
                entity_type=entity_type,
                status="ACTIVE",
                verification_status="PENDING_VERIFICATION",
                full_legal_name=lead.full_name,
                first_name=lead.first_name if entity_type == "INDIVIDUAL" else None,
                middle_name=lead.middle_name if entity_type == "INDIVIDUAL" else None,
                last_name=lead.last_name if entity_type == "INDIVIDUAL" else None,
                trade_name=trade_name,
                legal_representative_name=legal_rep,
                tax_id_rfc=rfc_clean,
                curp=curp_clean,
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
            InteractionModel.tenant_id == current_user.tenant_id,
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
            tenant_id=current_user.tenant_id,
            lead_id=lead.id,
            from_stage=old_stage,
            to_stage="CONVERTED",
            changed_by_user_id=current_user.id,
            reason_or_note=conversion_note[:500]
        ))

        db.add(InteractionModel(
            tenant_id=current_user.tenant_id,
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
        return {
            "message": "Lead converted successfully",
            "linked_existing": linked_existing,
            "lead_id": lead.id,
            "lead_number": lead.lead_number,
            "customer": serialize_customer(target_customer)
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Lead conversion failed")
        raise HTTPException(status_code=500, detail="Could not complete lead conversion.")
