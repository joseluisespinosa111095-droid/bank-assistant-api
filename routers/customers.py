import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote as url_quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.config import (
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
    UPLOAD_READ_CHUNK_BYTES,
    VALID_CALL_OUTCOMES,
    VALID_CUSTOMER_STATUSES,
    VALID_DOCUMENT_TYPES,
    VALID_ENTITY_TYPES,
    VALID_INTERACTION_DIRECTIONS,
    VALID_INTERACTION_TYPES,
    VALID_INTERACTION_VISIBILITIES,
    VALID_VERIFICATION_STATUSES,
    logger,
)
from core.security import get_client_ip
from core.utils import (
    generate_next_customer_number,
    normalize_phone_number,
    parse_optional_datetime_utc,
)
from dependencies.auth import require_roles, resolve_tenant_owner_user_id, verify_csrf
from dependencies.db import get_db
from models import CustomerDocumentModel, CustomerModel, InteractionModel, UserModel
from schemas.core_crm import CustomerCreateRequest, CustomerUpdateRequest, InteractionCreateRequest
from services.audit import record_audit_event
from services.customer_detail import (
    get_customer_active_loan_summary,
    get_customer_detail_payload,
    get_customers_loan_stats,
)
from services.serializers_core import (
    resolve_customer_name_for_create,
    resolve_customer_name_for_update,
    serialize_customer,
    serialize_interaction,
)

router = APIRouter()


# --- DOCUMENT UPLOAD SECURITY (PURE HELPERS) BEGIN ---
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
# --- DOCUMENT UPLOAD SECURITY (PURE HELPERS) END ---


# --- STAGE 1 & 2: CUSTOMERS ENDPOINTS ---
@router.get("/api/customers")
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
    query = db.query(CustomerModel).filter(CustomerModel.tenant_id == current_user.tenant_id)

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

    cust_ids = [c.id for c in items]
    loan_stats, last_payments_map = get_customers_loan_stats(db, current_user.tenant_id, cust_ids)

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


@router.post("/api/customers", dependencies=[Depends(verify_csrf)])
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

    dup_phone = (
        db.query(CustomerModel)
        .filter(
            CustomerModel.tenant_id == current_user.tenant_id,
            CustomerModel.phone_primary_normalized == phone_norm
        )
        .first()
    )
    if dup_phone:
        raise HTTPException(
            status_code=409,
            detail=f"Primary phone number is already registered under customer {dup_phone.customer_number}."
        )

    if email_clean:
        dup_email = (
            db.query(CustomerModel)
            .filter(
                CustomerModel.tenant_id == current_user.tenant_id,
                CustomerModel.email == email_clean
            )
            .first()
        )
        if dup_email:
            raise HTTPException(
                status_code=409,
                detail=f"Email address is already registered under customer {dup_email.customer_number}."
            )

    if rfc_clean:
        dup_rfc = (
            db.query(CustomerModel)
            .filter(
                CustomerModel.tenant_id == current_user.tenant_id,
                CustomerModel.tax_id_rfc == rfc_clean
            )
            .first()
        )
        if dup_rfc:
            raise HTTPException(
                status_code=409,
                detail=f"RFC / Tax ID is already registered under customer {dup_rfc.customer_number}."
            )

    try:
        owner_id = resolve_tenant_owner_user_id(db, current_user.tenant_id, payload.owner_user_id, current_user.id)
        cust_number = generate_next_customer_number(db, current_user.tenant_id)

        new_customer = CustomerModel(
            tenant_id=current_user.tenant_id,
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


@router.get("/api/customers/{customer_id}")
def get_customer_detail(
    customer_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    data = get_customer_detail_payload(db, current_user.tenant_id, customer_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Customer not found.")
    return data


@router.patch("/api/customers/{customer_id}", dependencies=[Depends(verify_csrf)])
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
        customer = (
            db.query(CustomerModel)
            .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == current_user.tenant_id)
            .with_for_update()
            .first()
        )
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
                .filter(
                    CustomerModel.tenant_id == current_user.tenant_id,
                    CustomerModel.phone_primary_normalized == phone_norm,
                    CustomerModel.id != customer.id
                )
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
                    .filter(
                        CustomerModel.tenant_id == current_user.tenant_id,
                        CustomerModel.email == email_clean,
                        CustomerModel.id != customer.id
                    )
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
                    .filter(
                        CustomerModel.tenant_id == current_user.tenant_id,
                        CustomerModel.tax_id_rfc == rfc_clean,
                        CustomerModel.id != customer.id
                    )
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
            customer.owner_user_id = resolve_tenant_owner_user_id(
                db, current_user.tenant_id, payload.owner_user_id, customer.owner_user_id
            )
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
        active_loans_count, unpaid_principal = get_customer_active_loan_summary(
            db, current_user.tenant_id, customer
        )
        return serialize_customer(customer, active_loans_count=active_loans_count, unpaid_principal_cents=unpaid_principal)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("Failed to update customer")
        raise HTTPException(status_code=500, detail="Could not update customer record.")


@router.get("/api/customers/{customer_id}/interactions")
def list_customer_interactions(
    customer_id: int,
    current_user: UserModel = Depends(
        require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")
    ),
    db: Session = Depends(get_db)
):
    customer = (
        db.query(CustomerModel.id)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    interactions = (
        db.query(InteractionModel)
        .filter(
            InteractionModel.tenant_id == current_user.tenant_id,
            InteractionModel.customer_id == customer_id
        )
        .order_by(InteractionModel.created_at.desc(), InteractionModel.id.desc())
        .all()
    )
    user_ids = {i.created_by_user_id for i in interactions}
    users = (
        db.query(UserModel)
        .filter(UserModel.tenant_id == current_user.tenant_id, UserModel.id.in_(user_ids))
        .all()
        if user_ids else []
    )
    user_map = {u.id: u for u in users}

    return [serialize_interaction(i, user_map) for i in interactions]


@router.post("/api/customers/{customer_id}/interactions", dependencies=[Depends(verify_csrf)])
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
    customer = (
        db.query(CustomerModel)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == current_user.tenant_id)
        .first()
    )
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
            tenant_id=current_user.tenant_id,
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


@router.post("/api/customers/{customer_id}/documents", dependencies=[Depends(verify_csrf)])
def upload_customer_document(
    customer_id: int,
    request: Request,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db)
):
    ip = get_client_ip(request)
    customer = (
        db.query(CustomerModel)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == current_user.tenant_id)
        .first()
    )
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
            tenant_id=current_user.tenant_id,
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


@router.get("/api/customers/{customer_id}/documents")
def list_customer_documents(
    customer_id: int,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    customer = (
        db.query(CustomerModel.id)
        .filter(CustomerModel.id == customer_id, CustomerModel.tenant_id == current_user.tenant_id)
        .first()
    )
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found.")

    docs = (
        db.query(CustomerDocumentModel)
        .filter(
            CustomerDocumentModel.tenant_id == current_user.tenant_id,
            CustomerDocumentModel.customer_id == customer_id,
            CustomerDocumentModel.status == "ACTIVE"
        )
        .order_by(CustomerDocumentModel.id.desc())
        .all()
    )
    user_ids = {d.uploaded_by_user_id for d in docs}
    users = (
        {
            u.id: u.full_name or u.email
            for u in db.query(UserModel).filter(
                UserModel.tenant_id == current_user.tenant_id,
                UserModel.id.in_(user_ids)
            ).all()
        }
        if user_ids else {}
    )

    return [{
        "id": d.id, "document_type": d.document_type, "original_filename": d.original_filename,
        "mime_type": d.mime_type, "file_size_bytes": d.file_size_bytes, "file_hash": d.file_hash,
        "uploaded_by_name": users.get(d.uploaded_by_user_id, "System"),
        "created_at": d.created_at.isoformat()
    } for d in docs]


@router.get("/api/customers/{customer_id}/documents/{doc_id}/download")
def download_customer_document(
    customer_id: int,
    doc_id: int,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "SALES_AGENT", "CREDIT_ADVISOR", "OPERATIONS_OFFICER")),
    db: Session = Depends(get_db)
):
    doc = (
        db.query(CustomerDocumentModel)
        .filter(
            CustomerDocumentModel.id == doc_id,
            CustomerDocumentModel.customer_id == customer_id,
            CustomerDocumentModel.tenant_id == current_user.tenant_id,
            CustomerDocumentModel.status == "ACTIVE"
        )
        .first()
    )
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


@router.delete("/api/customers/{customer_id}/documents/{doc_id}", dependencies=[Depends(verify_csrf)])
def delete_customer_document(
    customer_id: int,
    doc_id: int,
    request: Request,
    current_user: UserModel = Depends(require_roles("OWNER_ADMIN", "CREDIT_ADVISOR")),
    db: Session = Depends(get_db)
):
    doc = (
        db.query(CustomerDocumentModel)
        .filter(
            CustomerDocumentModel.id == doc_id,
            CustomerDocumentModel.customer_id == customer_id,
            CustomerDocumentModel.tenant_id == current_user.tenant_id,
            CustomerDocumentModel.status == "ACTIVE"
        )
        .first()
    )
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
