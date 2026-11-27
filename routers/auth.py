import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import (
    DUMMY_BCRYPT_HASH,
    IS_PRODUCTION,
    SUPPORTED_PRODUCTS,
    VALID_LANGUAGES,
    logger,
    pwd_context,
)
from core.schema_guard import is_auth_security_schema_ready
from core.security import (
    compute_email_hmac_buckets,
    consume_rate_limit_atomic,
    get_client_ip,
    hash_token,
    mask_email_for_audit,
    reset_rate_limit_bucket,
)
from dependencies.auth import get_current_user, verify_csrf
from dependencies.db import get_db
from models import PasswordResetTokenModel, SessionModel, TenantModel, UserModel
from schemas.auth import (
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    UserChangePasswordRequest,
    UserLanguageUpdateRequest,
    UserLogin,
)
from services.audit import record_audit_event
from services.external import EmailDeliveryService, GENERIC_PASSWORD_RESET_MESSAGE

router = APIRouter()


# --- AUTHENTICATION & ATOMIC BOOTSTRAP ENDPOINTS ---
@router.post("/api/auth/login")
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

    tenant = db.query(TenantModel).filter(TenantModel.id == user.tenant_id).first()
    if not tenant or not tenant.is_active:
        record_audit_event(
            db, "LOGIN_BLOCKED_TENANT_INACTIVE", "auth", "DENIED",
            actor=user, resource_id=str(user.tenant_id), ip_address=ip, commit=True
        )
        raise HTTPException(status_code=403, detail="Organization account is inactive or suspended.")

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
        "tenant_id": user.tenant_id,
        "tenant_slug": tenant.slug,
        "tenant_name": tenant.name,
        "is_superadmin": bool(user.is_superadmin),
        "preferred_language": user.preferred_language if user.preferred_language in VALID_LANGUAGES else "en"
    }


@router.post("/api/auth/password-reset/request")
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
            fallback_base_url=f"{request.url.scheme}://{request.url.netloc}"
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


@router.post("/api/auth/password-reset/confirm")
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


@router.post("/api/auth/logout", dependencies=[Depends(verify_csrf)])
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


@router.get("/api/auth/me")
def get_me(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    tenant = db.query(TenantModel).filter(TenantModel.id == current_user.tenant_id).first()
    return {
        "id": current_user.id,
        "tenant_id": current_user.tenant_id,
        "tenant_slug": tenant.slug if tenant else None,
        "tenant_name": tenant.name if tenant else None,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "is_superadmin": bool(current_user.is_superadmin),
        "preferred_language": current_user.preferred_language if current_user.preferred_language in VALID_LANGUAGES else "en",
        "is_active": current_user.is_active,
        "supported_products": SUPPORTED_PRODUCTS
    }


@router.patch("/api/auth/me/language", dependencies=[Depends(verify_csrf)])
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


@router.post("/api/auth/me/password", dependencies=[Depends(verify_csrf)])
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
