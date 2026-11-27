from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from dependencies.auth import require_superadmin
from dependencies.db import get_db
from models import UserModel

router = APIRouter()


# --- HEALTH CHECK & SCHEMA STATUS ENDPOINTS ---
@router.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected", "version": "7.4.0"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database unhealthy: {type(e).__name__}")


@router.get("/api/admin/schema-status")
def schema_status(
    admin: UserModel = Depends(require_superadmin),
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


@router.get("/", response_class=FileResponse)
def home():
    return FileResponse("index.html", headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
