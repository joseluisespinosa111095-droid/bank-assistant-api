from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from core.config import logger
from core.security import get_client_ip
from dependencies.auth import require_commercial_tenant, verify_csrf
from dependencies.db import get_db
from models import ChatMessageModel, ConversationModel, UserModel
from schemas.core_crm import ChatRequest
from services.audit import record_audit_event
from services.external import AIService
from services.modules import get_enabled_module_codes

router = APIRouter()


# --- CONVERSATIONS & NURA ASSISTANT ---
@router.get("/api/conversations")
def listar_conversaciones(current_user: UserModel = Depends(require_commercial_tenant), db: Session = Depends(get_db)):
    convs = (
        db.query(ConversationModel)
        .filter(
            ConversationModel.tenant_id == current_user.tenant_id,
            ConversationModel.user_id == current_user.id
        )
        .order_by(ConversationModel.updated_at.desc())
        .all()
    )
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in convs]


@router.post("/api/conversations", dependencies=[Depends(verify_csrf)])
def crear_conversacion(payload: dict, current_user: UserModel = Depends(require_commercial_tenant), db: Session = Depends(get_db)):
    nueva = ConversationModel(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        title=str(payload.get("title", "New Consultation"))[:60]
    )
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    return {"id": nueva.id, "title": nueva.title}


@router.get("/api/conversations/{conv_id}/messages")
def obtener_mensajes(conv_id: int, current_user: UserModel = Depends(require_commercial_tenant), db: Session = Depends(get_db)):
    conv = (
        db.query(ConversationModel)
        .filter(
            ConversationModel.id == conv_id,
            ConversationModel.tenant_id == current_user.tenant_id,
            ConversationModel.user_id == current_user.id
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = (
        db.query(ChatMessageModel)
        .filter(
            ChatMessageModel.tenant_id == current_user.tenant_id,
            ChatMessageModel.conversation_id == conv_id
        )
        .order_by(ChatMessageModel.created_at.asc())
        .all()
    )
    return [{"role": m.role, "content": m.content, "created_at": m.created_at} for m in msgs]


@router.delete("/api/conversations/{conv_id}", dependencies=[Depends(verify_csrf)])
def eliminar_conversacion(conv_id: int, current_user: UserModel = Depends(require_commercial_tenant), db: Session = Depends(get_db)):
    conv = (
        db.query(ConversationModel)
        .filter(
            ConversationModel.id == conv_id,
            ConversationModel.tenant_id == current_user.tenant_id,
            ConversationModel.user_id == current_user.id
        )
        .first()
    )
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    db.delete(conv)
    db.commit()
    return {"message": "Deleted"}


@router.post("/chat-ejecutivo", dependencies=[Depends(verify_csrf)])
def chat_ejecutivo(
    req: ChatRequest,
    request: Request,
    current_user: UserModel = Depends(require_commercial_tenant),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    try:
        conv_id = req.conversation_id
        if conv_id:
            conv = (
                db.query(ConversationModel)
                .filter(
                    ConversationModel.id == conv_id,
                    ConversationModel.tenant_id == current_user.tenant_id,
                    ConversationModel.user_id == current_user.id
                )
                .first()
            )
            if not conv:
                raise HTTPException(status_code=403, detail="Unauthorized conversation")
        else:
            conv = ConversationModel(
                tenant_id=current_user.tenant_id,
                user_id=current_user.id,
                title=req.mensaje[:30] + "..."
            )
            db.add(conv)
            db.commit()
            db.refresh(conv)
            conv_id = conv.id

        prior_messages = (
            db.query(ChatMessageModel)
            .filter(
                ChatMessageModel.tenant_id == current_user.tenant_id,
                ChatMessageModel.conversation_id == conv_id
            )
            .order_by(ChatMessageModel.created_at.asc())
            .all()
        )

        db.add(ChatMessageModel(tenant_id=current_user.tenant_id, conversation_id=conv_id, role="user", content=req.mensaje))
        db.commit()

        enabled_modules = get_enabled_module_codes(
            db,
            current_user.tenant_id,
            is_superadmin=bool(current_user.is_superadmin),
        )

        respuesta_texto = AIService.generate_reply(
            user_message=req.mensaje,
            prior_messages=prior_messages,
            user_role=current_user.role,
            enabled_modules=enabled_modules,
        )

        db.add(ChatMessageModel(tenant_id=current_user.tenant_id, conversation_id=conv_id, role="executive", content=respuesta_texto))
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
