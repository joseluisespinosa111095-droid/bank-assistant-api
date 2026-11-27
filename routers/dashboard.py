from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.utils import get_local_day_bounds_utc
from dependencies.auth import require_commercial_tenant
from dependencies.db import get_db
from models import CustomerModel, InteractionModel, LeadModel, UserModel
from services.serializers_core import compute_call_bucket, serialize_interaction

router = APIRouter()


@router.get("/api/dashboard/summary")
def get_dashboard_summary(
    current_user: UserModel = Depends(require_commercial_tenant),
    db: Session = Depends(get_db),
):
    """Core-only CRM dashboard summary (0 Lending dependencies)."""
    today_start_utc, today_end_utc, upcoming_end_utc, _ = get_local_day_bounds_utc()

    customers_count = (
        db.query(CustomerModel)
        .filter(CustomerModel.tenant_id == current_user.tenant_id)
        .count()
    )
    active_customers_count = (
        db.query(CustomerModel)
        .filter(
            CustomerModel.tenant_id == current_user.tenant_id,
            CustomerModel.status == "ACTIVE",
        )
        .count()
    )

    open_leads_all = (
        db.query(LeadModel)
        .filter(
            LeadModel.tenant_id == current_user.tenant_id,
            LeadModel.stage.notin_(["CONVERTED", "LOST"]),
        )
        .all()
    )
    overdue_count = 0
    today_count = 0
    upcoming_count = 0
    for lead in open_leads_all:
        bucket = compute_call_bucket(lead, today_start_utc, today_end_utc)
        if bucket == "OVERDUE":
            overdue_count += 1
        elif bucket == "TODAY":
            today_count += 1
        elif bucket == "UPCOMING" and lead.next_follow_up_at:
            nfa = (
                lead.next_follow_up_at
                if lead.next_follow_up_at.tzinfo
                else lead.next_follow_up_at.replace(tzinfo=timezone.utc)
            )
            if nfa <= upcoming_end_utc:
                upcoming_count += 1

    recent_interactions = (
        db.query(InteractionModel)
        .filter(InteractionModel.tenant_id == current_user.tenant_id)
        .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
        .limit(5)
        .all()
    )
    user_ids = {i.created_by_user_id for i in recent_interactions if i.created_by_user_id is not None}
    user_map = (
        {
            u.id: u
            for u in db.query(UserModel)
            .filter(UserModel.tenant_id == current_user.tenant_id, UserModel.id.in_(user_ids))
            .all()
        }
        if user_ids
        else {}
    )

    return {
        "counts": {
            "customers": customers_count,
            "active_customers": active_customers_count,
            "open_leads": len(open_leads_all),
            "call_list_overdue": overdue_count,
            "call_list_today": today_count,
            "call_list_upcoming": upcoming_count,
        },
        "call_list": {
            "open_total": len(open_leads_all),
            "overdue": overdue_count,
            "today": today_count,
            "upcoming": upcoming_count,
        },
        "recent_activity": [serialize_interaction(i, user_map) for i in recent_interactions],
    }
