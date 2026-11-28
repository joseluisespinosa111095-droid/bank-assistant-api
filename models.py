from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text,
    DateTime, Date, Boolean, JSON, ForeignKey, ForeignKeyConstraint,
    PrimaryKeyConstraint, UniqueConstraint, CheckConstraint, Index,
    event, text, inspect, LargeBinary, true, false
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import declarative_base, relationship, deferred


Base = declarative_base()


# --- SQLALCHEMY MODELS ---
class TenantModel(Base):
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(160), nullable=False)
    slug = Column(String(80), unique=True, index=True, nullable=False)
    legal_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True, server_default=true(), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class TenantSequenceModel(Base):
    __tablename__ = "tenant_sequences"
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False)
    entity_type = Column(String(40), nullable=False)
    last_value = Column(Integer, default=0, server_default="0", nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("tenant_id", "entity_type", name="pk_tenant_sequences"),
    )


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("preferred_language IN ('en', 'es')", name="ck_users_preferred_language_valid"),
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
    )
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String(120), nullable=True)
    password_hash = Column(String, nullable=False)
    role = Column(String(30), default="SALES_AGENT", nullable=False, index=True)
    is_superadmin = Column(Boolean, default=False, server_default=false(), nullable=False, index=True)
    preferred_language = Column(String(10), default="en", server_default="en", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    tenant = relationship("TenantModel")
    conversations = relationship("ConversationModel", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("SessionModel", back_populates="user", cascade="all, delete-orphan")
    password_reset_tokens = relationship("PasswordResetTokenModel", back_populates="user", cascade="all, delete-orphan")
    audit_events = relationship("AuditLogModel", back_populates="actor")


class SessionModel(Base):
    __tablename__ = "sessions"
    token = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    user = relationship("UserModel", back_populates="sessions")


class RateLimitModel(Base):
    __tablename__ = "rate_limits"
    id = Column(Integer, primary_key=True, autoincrement=True)
    bucket_key = Column(String(140), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=1)
    window_started_at = Column(DateTime(timezone=True), nullable=False)
    window_expires_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("bucket_key", name="uq_rate_limits_bucket_key"),
        CheckConstraint("length(trim(bucket_key)) >= 4", name="ck_rate_limits_bucket_key_non_empty"),
        CheckConstraint("attempt_count >= 1", name="ck_rate_limits_attempts_positive"),
        CheckConstraint("window_expires_at > window_started_at", name="ck_rate_limits_window_valid"),
        Index("ix_rate_limits_window_expires_at", "window_expires_at"),
    )


class PasswordResetTokenModel(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    request_ip = Column(String(45), nullable=True)

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
        CheckConstraint("length(token_hash) = 64", name="ck_password_reset_tokens_hash_len"),
        CheckConstraint("expires_at > created_at", name="ck_password_reset_tokens_expiry_valid"),
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
        Index(
            "uq_password_reset_tokens_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("used_at IS NULL"),
            sqlite_where=text("used_at IS NULL")
        ),
    )

    user = relationship("UserModel", back_populates="password_reset_tokens")


class ConversationModel(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    user_id = Column(Integer, nullable=False)
    title = Column(String, default="New Consultation")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_conversations_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="CASCADE",
            name="fk_conversations_tenant_user"
        ),
    )

    user = relationship("UserModel", back_populates="conversations")
    messages = relationship("ChatMessageModel", back_populates="conversation", cascade="all, delete-orphan")


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    conversation_id = Column(Integer, index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "conversation_id"],
            ["conversations.tenant_id", "conversations.id"],
            ondelete="CASCADE",
            name="fk_chat_messages_tenant_conversation"
        ),
    )

    conversation = relationship("ConversationModel", back_populates="messages")


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True, index=True)
    scope = Column(String(20), nullable=False, default="TENANT", index=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=True, index=True)
    actor_role = Column(String(30), nullable=True)
    event_type = Column(String(60), nullable=False, index=True)
    resource_type = Column(String(40), nullable=False)
    resource_id = Column(String(60), nullable=True)
    outcome = Column(String(20), nullable=False)
    ip_address = Column(String(45), nullable=True)
    details_json = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("scope IN ('ANONYMOUS', 'TENANT', 'PLATFORM')", name="ck_audit_logs_scope_valid"),
    )

    actor = relationship("UserModel", back_populates="audit_events")


class CustomerModel(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_number = Column(String(24), index=True, nullable=False)
    entity_type = Column(String(20), default="INDIVIDUAL", nullable=False)
    status = Column(String(30), default="ACTIVE", index=True, nullable=False)
    verification_status = Column(String(30), default="PENDING_VERIFICATION", nullable=False)
    full_legal_name = Column(String(180), index=True, nullable=False)
    # Personas fisicas: nombre / segundo nombre (opcional) / apellidos. full_legal_name se arma a partir de ellos.
    # Personas morales: estas columnas quedan en NULL y full_legal_name es la razon social.
    first_name = Column(String(50), nullable=True)
    middle_name = Column(String(50), nullable=True)
    last_name = Column(String(75), nullable=True)
    trade_name = Column(String(160), nullable=True)
    legal_representative_name = Column(String(160), nullable=True)
    tax_id_rfc = Column(String(20), index=True, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    phone_primary = Column(String(25), nullable=False)
    phone_primary_normalized = Column(String(20), index=True, nullable=False)
    phone_secondary = Column(String(25), nullable=True)
    phone_secondary_normalized = Column(String(20), nullable=True)
    email = Column(String(255), index=True, nullable=True)
    address_full = Column(Text, nullable=True)
    employment_status = Column(String(60), nullable=True)
    employer_or_business_name = Column(String(160), nullable=True)
    occupation_or_industry = Column(String(120), nullable=True)
    employment_address = Column(Text, nullable=True)
    owner_user_id = Column(Integer, index=True, nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    customer_since = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_customers_tenant_id_id"),
        UniqueConstraint("tenant_id", "customer_number", name="uq_customers_tenant_customer_number"),
        UniqueConstraint("tenant_id", "phone_primary_normalized", name="uq_customers_tenant_phone_primary"),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_customers_tenant_owner_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_customers_tenant_created_by_user"
        ),
    )

    loans = relationship("LoanModel", back_populates="customer", cascade="all, delete-orphan")
    documents = relationship("CustomerDocumentModel", back_populates="customer", cascade="all, delete-orphan")


class CustomerDocumentModel(Base):
    __tablename__ = "customer_documents"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    customer_id = Column(Integer, nullable=False, index=True)
    document_type = Column(String(40), nullable=False)
    original_filename = Column(String(255), nullable=False)
    mime_type = Column(String(100), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=False)
    file_data = deferred(Column(LargeBinary, nullable=False))  # BYTEA (deferred: solo se carga al descargar)
    status = Column(String(30), default="ACTIVE", nullable=False)
    uploaded_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_customer_documents_tenant_customer"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_customer_documents_tenant_uploaded_by"
        ),
    )

    customer = relationship("CustomerModel", back_populates="documents")


class LeadModel(Base):
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint("requested_amount_cents >= 0", name="ck_leads_amount_non_negative"),
        UniqueConstraint("tenant_id", "id", name="uq_leads_tenant_id_id"),
        UniqueConstraint("tenant_id", "lead_number", name="uq_leads_tenant_lead_number"),
        UniqueConstraint("tenant_id", "converted_customer_id", "id", name="uq_leads_tenant_converted_customer_id"),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_leads_tenant_owner_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_leads_tenant_created_by_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "converted_customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_leads_tenant_converted_customer"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "converted_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_leads_tenant_converted_by_user"
        ),
    )
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    lead_number = Column(String(24), index=True, nullable=False)
    full_name = Column(String(180), index=True, nullable=False)
    entity_type = Column(String(20), default="INDIVIDUAL", nullable=False)
    trade_name = Column(String(160), nullable=True)
    legal_representative_name = Column(String(160), nullable=True)
    tax_id_rfc = Column(String(20), nullable=True)
    phone_primary = Column(String(25), nullable=False)
    phone_primary_normalized = Column(String(20), index=True, nullable=False)
    email = Column(String(255), index=True, nullable=True)
    source = Column(String(40), default="DIRECT_OUTREACH", nullable=False)
    product_interest = Column(String(60), default="Personal Loan", nullable=False)
    requested_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False)
    currency = Column(String(3), default="MXN", nullable=False)
    stage = Column(String(35), default="NEW", index=True, nullable=False)
    priority = Column(String(15), default="MEDIUM", nullable=False)
    lost_reason = Column(String(160), nullable=True)
    last_contacted_at = Column(DateTime(timezone=True), nullable=True)
    last_interaction_outcome = Column(String(40), nullable=True)
    next_follow_up_at = Column(DateTime(timezone=True), index=True, nullable=True)
    summary_notes = Column(Text, nullable=True)
    owner_user_id = Column(Integer, index=True, nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    converted_customer_id = Column(Integer, index=True, nullable=True)
    converted_at = Column(DateTime(timezone=True), nullable=True)
    converted_by_user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class LeadStageHistoryModel(Base):
    __tablename__ = "lead_stage_history"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    lead_id = Column(Integer, index=True, nullable=False)
    from_stage = Column(String(35), nullable=True)
    to_stage = Column(String(35), nullable=False)
    changed_by_user_id = Column(Integer, nullable=False)
    reason_or_note = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            ["leads.tenant_id", "leads.id"],
            ondelete="RESTRICT",
            name="fk_lead_stage_history_tenant_lead"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "changed_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_lead_stage_history_tenant_changed_by"
        ),
    )


class InteractionModel(Base):
    __tablename__ = "interactions"
    __table_args__ = (
        CheckConstraint("lead_id IS NOT NULL OR customer_id IS NOT NULL", name="ck_interactions_target_present"),
        ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            ["leads.tenant_id", "leads.id"],
            ondelete="RESTRICT",
            name="fk_interactions_tenant_lead"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_interactions_tenant_customer"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_interactions_tenant_created_by"
        ),
    )
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    lead_id = Column(Integer, index=True, nullable=True)
    customer_id = Column(Integer, index=True, nullable=True)
    interaction_type = Column(String(30), nullable=False)
    direction = Column(String(20), default="OUTBOUND", nullable=False)
    visibility = Column(String(20), default="INTERNAL", nullable=False)
    outcome = Column(String(40), nullable=True)
    content = Column(Text, nullable=False)
    next_follow_up_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True, nullable=False)


class LoanModel(Base):
    __tablename__ = "loans"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    loan_number = Column(String(24), index=True, nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    request_payload_hash = Column(String(64), nullable=False)
    customer_id = Column(Integer, index=True, nullable=False)
    source_lead_id = Column(Integer, index=True, nullable=True)
    status = Column(String(25), default="DRAFT", index=True, nullable=False)
    product_name = Column(String(60), nullable=False)
    product_version = Column(String(20), default="v1.0", nullable=False)
    currency = Column(String(3), default="MXN", nullable=False)
    principal_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    monthly_interest_rate_bps = Column(Integer, nullable=False)
    interest_calculation_method = Column(String(40), default="FLAT_ORIGINAL_PRINCIPAL", nullable=False)
    interest_basis = Column(String(40), default="ORIGINAL_PRINCIPAL", nullable=False)
    interest_accrual_convention = Column(String(40), default="ON_INSTALLMENT_DUE_DATE", nullable=False)
    assumed_interest_months = Column(Integer, nullable=False)
    payment_frequency = Column(String(20), default="WEEKLY", nullable=False)
    term_installments_count = Column(Integer, nullable=False)
    rounding_policy = Column(String(40), default="FLOOR_REGULAR_EXACT_FINAL", nullable=False)
    contract_rules_json = Column(Text, nullable=False)
    monthly_interest_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    total_interest_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    total_repayment_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    regular_installment_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    final_installment_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    start_date = Column(Date, nullable=False)
    first_payment_date = Column(Date, nullable=False)
    maturity_date = Column(Date, nullable=False)
    unpaid_principal_cents = Column(BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False)
    remaining_contractual_interest_cents = Column(BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False)
    remaining_contract_total_cents = Column(BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False)
    amount_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), default=0, nullable=False)
    owner_user_id = Column(Integer, index=True, nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    approved_by_user_id = Column(Integer, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approval_notes = Column(Text, nullable=True)
    activated_by_user_id = Column(Integer, nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    disbursed_at = Column(DateTime(timezone=True), nullable=True)
    disbursement_method = Column(String(40), nullable=True)
    disbursement_reference = Column(String(120), nullable=True)
    disbursement_notes = Column(Text, nullable=True)
    cancelled_by_user_id = Column(Integer, nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancellation_reason = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_loans_tenant_id_id"),
        UniqueConstraint("tenant_id", "customer_id", "id", name="uq_loans_tenant_customer_id"),
        UniqueConstraint("tenant_id", "loan_number", name="uq_loans_tenant_loan_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_loans_tenant_idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_customer"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id", "source_lead_id"],
            ["leads.tenant_id", "leads.converted_customer_id", "leads.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_customer_source_lead"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_owner_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_created_by_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approved_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_approved_by_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "activated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_activated_by_user"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "cancelled_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loans_tenant_cancelled_by_user"
        ),
    )

    customer = relationship("CustomerModel", back_populates="loans")
    installments = relationship("LoanInstallmentModel", back_populates="loan", cascade="all, delete-orphan", order_by="LoanInstallmentModel.installment_number")
    payments = relationship("LoanPaymentModel", back_populates="loan", order_by="LoanPaymentModel.loan_payment_seq")


class LoanInstallmentModel(Base):
    __tablename__ = "loan_installments"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    loan_id = Column(Integer, nullable=False, index=True)
    installment_number = Column(Integer, nullable=False)
    due_date = Column(Date, nullable=False, index=True)
    beginning_principal_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    principal_due_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    interest_due_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    scheduled_payment_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    ending_principal_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    principal_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    interest_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    amount_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0)
    remaining_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("installment_number >= 1", name="ck_inst_number_positive"),
        CheckConstraint("principal_due_cents + interest_due_cents = scheduled_payment_cents", name="ck_inst_components_sum"),
        CheckConstraint("beginning_principal_cents - principal_due_cents = ending_principal_cents", name="ck_inst_principal_roll"),
        CheckConstraint("principal_paid_cents + interest_paid_cents = amount_paid_cents", name="ck_inst_paid_components_sum"),
        CheckConstraint("amount_paid_cents + remaining_amount_cents = scheduled_payment_cents", name="ck_inst_remaining_balance"),
        CheckConstraint(
            "beginning_principal_cents >= 0 AND principal_due_cents >= 0 "
            "AND interest_due_cents >= 0 AND scheduled_payment_cents >= 0 "
            "AND ending_principal_cents >= 0 AND principal_paid_cents >= 0 "
            "AND interest_paid_cents >= 0 AND amount_paid_cents >= 0 "
            "AND remaining_amount_cents >= 0",
            name="ck_inst_non_negative"
        ),
        CheckConstraint(
            "(remaining_amount_cents = 0 AND paid_at IS NOT NULL) "
            "OR (remaining_amount_cents > 0 AND paid_at IS NULL)",
            name="ck_inst_paid_at_coherent"
        ),
        UniqueConstraint("loan_id", "installment_number", name="uq_loan_installments_loan_num"),
        UniqueConstraint("tenant_id", "id", name="uq_loan_installments_tenant_id_id"),
        UniqueConstraint("tenant_id", "loan_id", "id", name="uq_loan_installments_tenant_loan_id"),
        ForeignKeyConstraint(
            ["tenant_id", "loan_id"],
            ["loans.tenant_id", "loans.id"],
            ondelete="RESTRICT",
            name="fk_loan_installments_tenant_loan"
        ),
    )

    loan = relationship("LoanModel", back_populates="installments")
    payment_allocations = relationship(
        "LoanPaymentAllocationModel",
        back_populates="installment",
        order_by="LoanPaymentAllocationModel.id",
        overlaps="allocations,payment"
    )


@event.listens_for(AuditLogModel, "before_update")
def _prevent_audit_update(mapper, connection, target):
    raise RuntimeError("AuditLogModel is append-only: UPDATE is forbidden.")


@event.listens_for(AuditLogModel, "before_delete")
def _prevent_audit_delete(mapper, connection, target):
    raise RuntimeError("AuditLogModel is append-only: DELETE is forbidden.")


class LoanPaymentModel(Base):
    __tablename__ = "loan_payments"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_number = Column(String(24), index=True, nullable=False)
    loan_id = Column(Integer, nullable=False, index=True)
    customer_id = Column(Integer, nullable=False, index=True)
    loan_payment_seq = Column(Integer, nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    request_payload_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="POSTED", index=True)
    currency = Column(String(3), nullable=False, default="MXN")
    amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    interest_applied_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    principal_applied_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_loan_status = Column(String(25), nullable=False)
    prev_unpaid_principal_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_remaining_interest_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_remaining_total_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_amount_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    payment_method = Column(String(40), nullable=False)
    payment_reference = Column(String(120), nullable=True, index=True)
    paid_at = Column(DateTime(timezone=True), nullable=False, index=True)
    notes = Column(Text, nullable=True)
    recorded_by_user_id = Column(Integer, nullable=False)
    reversed_by_user_id = Column(Integer, nullable=True)
    reversed_at = Column(DateTime(timezone=True), nullable=True)
    reversal_reason = Column(String(255), nullable=True)
    # Detalle del rebate por pronto pago (JSON versionado). Se escribe junto con el pago y es inmutable.
    rebate_detail_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # NOTA: el indice unico parcial uq_loan_payments_posted_reference (referencia de pago unica por
    # tenant entre pagos POSTED SPEI/DEPOSITO/CHEQUE) se crea en la migracion 010 y se acota a
    # tenant_id en la migracion 012.
    __table_args__ = (
        CheckConstraint("loan_payment_seq >= 1", name="ck_loan_payments_seq_positive"),
        CheckConstraint("status IN ('POSTED', 'REVERSED')", name="ck_loan_payments_status_valid"),
        CheckConstraint("currency = 'MXN'", name="ck_loan_payments_currency_mxn"),
        CheckConstraint(
            "payment_method IN ('SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK', 'CASH_RECEIPT')",
            name="ck_loan_payments_method_valid"
        ),
        CheckConstraint(
            "amount_cents > 0 AND interest_applied_cents >= 0 AND principal_applied_cents >= 0",
            name="ck_loan_payments_amounts_positive"
        ),
        CheckConstraint(
            "interest_applied_cents + principal_applied_cents = amount_cents",
            name="ck_loan_payments_components_sum"
        ),
        CheckConstraint("prev_loan_status = 'ACTIVE'", name="ck_loan_payments_prev_status_active"),
        CheckConstraint(
            "prev_unpaid_principal_cents >= 0 "
            "AND prev_remaining_interest_cents >= 0 "
            "AND prev_remaining_total_cents > 0 "
            "AND prev_amount_paid_cents >= 0 "
            "AND prev_unpaid_principal_cents + prev_remaining_interest_cents = prev_remaining_total_cents "
            "AND amount_cents <= prev_remaining_total_cents "
            "AND interest_applied_cents <= prev_remaining_interest_cents "
            "AND principal_applied_cents <= prev_unpaid_principal_cents",
            name="ck_loan_payments_prev_balances_valid"
        ),
        CheckConstraint(
            "(status = 'POSTED' AND reversed_by_user_id IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL) "
            "OR (status = 'REVERSED' AND reversed_by_user_id IS NOT NULL AND reversed_at IS NOT NULL "
            "AND length(trim(reversal_reason)) >= 3)",
            name="ck_loan_payments_reversal_metadata"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_loan_payments_tenant_id_id"),
        UniqueConstraint("tenant_id", "loan_id", "id", name="uq_loan_payments_tenant_loan_id"),
        UniqueConstraint("tenant_id", "payment_number", name="uq_loan_payments_tenant_payment_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_loan_payments_tenant_idempotency_key"),
        UniqueConstraint("loan_id", "loan_payment_seq", name="uq_loan_payments_loan_seq"),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id", "loan_id"],
            ["loans.tenant_id", "loans.customer_id", "loans.id"],
            ondelete="RESTRICT",
            name="fk_loan_payments_tenant_customer_loan"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_loan_payments_tenant_customer"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recorded_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loan_payments_tenant_recorded_by"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reversed_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_loan_payments_tenant_reversed_by"
        ),
    )

    loan = relationship("LoanModel", back_populates="payments")
    allocations = relationship(
        "LoanPaymentAllocationModel",
        back_populates="payment",
        order_by="LoanPaymentAllocationModel.installment_number",
        overlaps="installment,payment_allocations"
    )


class LoanPaymentAllocationModel(Base):
    __tablename__ = "loan_payment_allocations"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    payment_id = Column(Integer, nullable=False, index=True)
    loan_id = Column(Integer, nullable=False, index=True)
    installment_id = Column(Integer, nullable=False, index=True)
    installment_number = Column(Integer, nullable=False)
    interest_allocated_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    principal_allocated_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    total_allocated_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_principal_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_interest_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_amount_paid_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_remaining_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    prev_paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        CheckConstraint("installment_number >= 1", name="ck_payment_alloc_inst_num_pos"),
        CheckConstraint(
            "total_allocated_cents > 0 AND interest_allocated_cents >= 0 AND principal_allocated_cents >= 0",
            name="ck_payment_alloc_amounts_valid"
        ),
        CheckConstraint(
            "interest_allocated_cents + principal_allocated_cents = total_allocated_cents",
            name="ck_payment_alloc_components_sum"
        ),
        CheckConstraint(
            "prev_principal_paid_cents >= 0 "
            "AND prev_interest_paid_cents >= 0 "
            "AND prev_amount_paid_cents >= 0 "
            "AND prev_remaining_amount_cents > 0 "
            "AND prev_remaining_amount_cents >= total_allocated_cents "
            "AND prev_principal_paid_cents + prev_interest_paid_cents = prev_amount_paid_cents "
            "AND prev_paid_at IS NULL",
            name="ck_payment_alloc_prev_valid"
        ),
        UniqueConstraint("payment_id", "installment_id", name="uq_payment_alloc_payment_inst"),
        ForeignKeyConstraint(
            ["tenant_id", "loan_id", "payment_id"],
            ["loan_payments.tenant_id", "loan_payments.loan_id", "loan_payments.id"],
            ondelete="RESTRICT",
            name="fk_payment_alloc_tenant_loan_payment"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "loan_id", "installment_id"],
            ["loan_installments.tenant_id", "loan_installments.loan_id", "loan_installments.id"],
            ondelete="RESTRICT",
            name="fk_payment_alloc_tenant_loan_installment"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "loan_id"],
            ["loans.tenant_id", "loans.id"],
            ondelete="RESTRICT",
            name="fk_payment_alloc_tenant_loan"
        ),
    )

    payment = relationship("LoanPaymentModel", back_populates="allocations", overlaps="installment,payment_allocations")
    installment = relationship("LoanInstallmentModel", back_populates="payment_allocations", overlaps="allocations,payment")


@event.listens_for(LoanPaymentAllocationModel, "before_update")
def _prevent_payment_allocation_update(mapper, connection, target):
    raise ValueError("Loan payment allocations are strictly immutable and cannot be updated.")


@event.listens_for(LoanPaymentAllocationModel, "before_delete")
def _prevent_payment_allocation_delete(mapper, connection, target):
    raise ValueError("Loan payment allocations are strictly immutable and cannot be deleted.")


@event.listens_for(LoanPaymentModel, "before_delete")
def _prevent_payment_delete(mapper, connection, target):
    raise ValueError("Loan payments are strictly immutable and cannot be deleted.")


@event.listens_for(LoanPaymentModel, "before_update")
def _enforce_payment_reversal_only_update(mapper, connection, target):
    state = inspect(target)
    immutable_attrs = (
        "id", "payment_number", "loan_id", "customer_id", "loan_payment_seq",
        "idempotency_key", "request_payload_hash", "currency", "amount_cents",
        "interest_applied_cents", "principal_applied_cents", "prev_loan_status",
        "prev_unpaid_principal_cents", "prev_remaining_interest_cents",
        "prev_remaining_total_cents", "prev_amount_paid_cents",
        "payment_method", "payment_reference", "paid_at", "notes",
        "recorded_by_user_id", "created_at", "rebate_detail_json"
    )
    for attr_name in immutable_attrs:
        hist = state.attrs[attr_name].history
        if hist.has_changes():
            raise ValueError(f"Immutable loan payment field '{attr_name}' cannot be modified.")

    status_hist = state.attrs["status"].history
    if status_hist.deleted and status_hist.deleted[0] == "REVERSED":
        raise ValueError("Reversed payments are terminal and cannot be modified.")
    if target.status != "REVERSED" or (status_hist.deleted and status_hist.deleted[0] != "POSTED"):
        raise ValueError("Invalid payment update: only the transition POSTED -> REVERSED is allowed.")
    if (
        target.reversed_by_user_id is None
        or target.reversed_at is None
        or not (target.reversal_reason or "").strip()
        or len((target.reversal_reason or "").strip()) < 3
    ):
        raise ValueError("Payment reversal requires reversed_by_user_id, reversed_at, and a valid reversal_reason.")


# --- MULTI-MODULE CATALOG & TENANT ENTITLEMENTS (ENTREGABLE 5A) ---
class PlatformModuleModel(Base):
    __tablename__ = "platform_modules"
    code = Column(String(40), primary_key=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    is_core_mandatory = Column(Boolean, default=False, server_default=false(), nullable=False)
    is_assignable = Column(Boolean, default=False, server_default=false(), nullable=False)
    is_active = Column(Boolean, default=True, server_default=true(), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        PrimaryKeyConstraint("code", name="pk_platform_modules"),
        CheckConstraint("length(trim(code)) >= 2", name="ck_platform_modules_code_non_empty"),
    )


class PlatformModuleDependencyModel(Base):
    __tablename__ = "platform_module_dependencies"
    module_code = Column(String(40), nullable=False)
    required_module_code = Column(String(40), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint(
            "module_code",
            "required_module_code",
            name="pk_platform_module_dependencies",
        ),
        ForeignKeyConstraint(
            ["module_code"],
            ["platform_modules.code"],
            ondelete="RESTRICT",
            name="fk_pmod_dep_module_code",
        ),
        ForeignKeyConstraint(
            ["required_module_code"],
            ["platform_modules.code"],
            ondelete="RESTRICT",
            name="fk_pmod_dep_required_code",
        ),
        CheckConstraint(
            "module_code <> required_module_code",
            name="ck_platform_module_dependencies_no_self_dep",
        ),
    )


class TenantModuleModel(Base):
    __tablename__ = "tenant_modules"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, nullable=False, index=True)
    module_code = Column(String(40), nullable=False, index=True)
    is_enabled = Column(Boolean, default=True, server_default=true(), nullable=False, index=True)
    config_json = Column(
        JSON().with_variant(postgresql.JSONB(astext_type=Text()), "postgresql"),
        nullable=True,
    )
    enabled_at = Column(DateTime(timezone=True), nullable=True)
    disabled_at = Column(DateTime(timezone=True), nullable=True)
    updated_by_user_id = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "module_code", name="uq_tenant_modules_tenant_module"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_tenant_modules_tenant_id",
        ),
        ForeignKeyConstraint(
            ["module_code"],
            ["platform_modules.code"],
            ondelete="RESTRICT",
            name="fk_tenant_modules_module_code",
        ),
        ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_tenant_modules_updated_by_user_id",
        ),
    )

    tenant = relationship("TenantModel")
    module = relationship("PlatformModuleModel")
    updated_by = relationship("UserModel")


# --- CREDIT BROKER VERTICAL MODELS (ENTREGABLE 5B - BLOQUE 1) ---
class BrokerLenderModel(Base):
    __tablename__ = "broker_lenders"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    code = Column(String(40), nullable=False)
    name = Column(String(160), nullable=False)
    lender_type = Column(String(40), default="BANK", server_default="BANK", nullable=False)
    default_commission_bps = Column(Integer, default=0, server_default="0", nullable=False)
    contact_name = Column(String(120), nullable=True)
    contact_email = Column(String(255), nullable=True)
    contact_phone = Column(String(25), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, server_default=true(), nullable=False, index=True)
    created_by_user_id = Column(Integer, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_broker_lenders_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_broker_lenders_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_lenders_tenant_created_by",
        ),
        CheckConstraint("length(trim(code)) >= 2", name="ck_broker_lenders_code_non_empty"),
        CheckConstraint("length(trim(name)) >= 2", name="ck_broker_lenders_name_non_empty"),
        CheckConstraint(
            "lender_type IN ('BANK', 'SOFOM', 'FINTECH', 'CREDIT_UNION', 'PRIVATE_LENDER', 'OTHER')",
            name="ck_broker_lenders_type_valid",
        ),
        CheckConstraint(
            "default_commission_bps >= 0 AND default_commission_bps <= 5000",
            name="ck_broker_lenders_commission_bps_range",
        ),
    )

    tenant = relationship("TenantModel")
    created_by = relationship("UserModel", foreign_keys=[created_by_user_id], primaryjoin="and_(BrokerLenderModel.tenant_id==UserModel.tenant_id, BrokerLenderModel.created_by_user_id==UserModel.id)")


class BrokerCaseModel(Base):
    __tablename__ = "broker_cases"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    case_number = Column(String(24), index=True, nullable=False)
    idempotency_key = Column(String(64), nullable=False)
    request_payload_hash = Column(String(64), nullable=False)
    customer_id = Column(Integer, index=True, nullable=False)
    source_lead_id = Column(Integer, index=True, nullable=True)
    status = Column(String(30), default="DRAFT", server_default="DRAFT", index=True, nullable=False)
    product_type = Column(String(60), nullable=False)
    currency = Column(String(3), default="MXN", server_default="MXN", nullable=False)
    requested_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    requested_term_months = Column(Integer, nullable=True)
    purpose = Column(String(255), nullable=True)
    funded_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    funded_at = Column(DateTime(timezone=True), nullable=True)
    external_loan_reference = Column(String(120), nullable=True)
    owner_user_id = Column(Integer, index=True, nullable=False)
    created_by_user_id = Column(Integer, nullable=False)
    closed_by_user_id = Column(Integer, nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_reason = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_broker_cases_tenant_id_id"),
        UniqueConstraint("tenant_id", "case_number", name="uq_broker_cases_tenant_case_number"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_broker_cases_tenant_idempotency_key"),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_customer",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "customer_id", "source_lead_id"],
            ["leads.tenant_id", "leads.converted_customer_id", "leads.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_customer_source_lead",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_owner_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_created_by",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "closed_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_closed_by",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'OFFERS_RECEIVED', 'FUNDED', 'CLOSED_LOST', 'CANCELLED')",
            name="ck_broker_cases_status_valid",
        ),
        CheckConstraint("length(trim(product_type)) >= 2", name="ck_broker_cases_product_type_non_empty"),
        CheckConstraint("requested_amount_cents > 0", name="ck_broker_cases_requested_amount_positive"),
        CheckConstraint(
            "requested_term_months IS NULL OR (requested_term_months >= 1 AND requested_term_months <= 360)",
            name="ck_broker_cases_term_months_range",
        ),
        CheckConstraint(
            "funded_amount_cents IS NULL OR funded_amount_cents > 0",
            name="ck_broker_cases_funded_amount_positive",
        ),
    )

    customer = relationship(
        "CustomerModel",
        foreign_keys=[tenant_id, customer_id],
        primaryjoin="and_(BrokerCaseModel.tenant_id==CustomerModel.tenant_id, BrokerCaseModel.customer_id==CustomerModel.id)",
        overlaps="tenant",
    )
    submissions = relationship(
        "BrokerSubmissionModel",
        back_populates="broker_case",
        foreign_keys="[BrokerSubmissionModel.tenant_id, BrokerSubmissionModel.broker_case_id]",
        primaryjoin="and_(BrokerCaseModel.tenant_id==BrokerSubmissionModel.tenant_id, BrokerCaseModel.id==BrokerSubmissionModel.broker_case_id)",
        order_by="BrokerSubmissionModel.id.asc()",
        overlaps="customer,tenant",
    )


class BrokerSubmissionModel(Base):
    __tablename__ = "broker_submissions"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    broker_case_id = Column(Integer, index=True, nullable=False)
    lender_id = Column(Integer, index=True, nullable=False)
    status = Column(String(30), default="DRAFT", server_default="DRAFT", index=True, nullable=False)
    external_reference = Column(String(120), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    offered_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    offered_rate_bps = Column(Integer, nullable=True)
    offered_term_months = Column(Integer, nullable=True)
    offered_monthly_payment_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=True)
    offered_commission_bps = Column(Integer, nullable=True)
    decision_notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, nullable=False)
    updated_by_user_id = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_broker_submissions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "broker_case_id",
            "lender_id",
            name="uq_broker_submissions_tenant_case_lender",
        ),
        UniqueConstraint(
            "tenant_id",
            "broker_case_id",
            "lender_id",
            "id",
            name="uq_broker_submissions_tenant_case_lender_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "broker_case_id"],
            ["broker_cases.tenant_id", "broker_cases.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_case",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lender_id"],
            ["broker_lenders.tenant_id", "broker_lenders.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_lender",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_created_by",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_updated_by",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'IN_UNDERWRITING', 'APPROVED', 'DECLINED', 'WITHDRAWN', 'SELECTED')",
            name="ck_broker_submissions_status_valid",
        ),
        CheckConstraint(
            "offered_amount_cents IS NULL OR offered_amount_cents > 0",
            name="ck_broker_submissions_offered_amount_positive",
        ),
        CheckConstraint(
            "offered_rate_bps IS NULL OR offered_rate_bps >= 0",
            name="ck_broker_submissions_offered_rate_non_negative",
        ),
        CheckConstraint(
            "offered_term_months IS NULL OR (offered_term_months >= 1 AND offered_term_months <= 360)",
            name="ck_broker_submissions_offered_term_range",
        ),
        CheckConstraint(
            "offered_monthly_payment_cents IS NULL OR offered_monthly_payment_cents >= 0",
            name="ck_broker_submissions_monthly_pay_non_negative",
        ),
        CheckConstraint(
            "offered_commission_bps IS NULL OR (offered_commission_bps >= 0 AND offered_commission_bps <= 5000)",
            name="ck_broker_submissions_commission_bps_range",
        ),
        Index(
            "uq_broker_submissions_selected_per_case",
            "tenant_id",
            "broker_case_id",
            unique=True,
            postgresql_where=text("status = 'SELECTED'"),
            sqlite_where=text("status = 'SELECTED'"),
        ),
    )

    broker_case = relationship(
        "BrokerCaseModel",
        back_populates="submissions",
        foreign_keys=[tenant_id, broker_case_id],
        primaryjoin="and_(BrokerSubmissionModel.tenant_id==BrokerCaseModel.tenant_id, BrokerSubmissionModel.broker_case_id==BrokerCaseModel.id)",
        overlaps="lender",
    )
    lender = relationship(
        "BrokerLenderModel",
        foreign_keys=[tenant_id, lender_id],
        primaryjoin="and_(BrokerSubmissionModel.tenant_id==BrokerLenderModel.tenant_id, BrokerSubmissionModel.lender_id==BrokerLenderModel.id)",
        overlaps="broker_case,submissions",
    )


class BrokerCommissionModel(Base):
    __tablename__ = "broker_commissions"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True)
    commission_number = Column(String(24), nullable=False)
    broker_case_id = Column(Integer, nullable=False)
    submission_id = Column(Integer, nullable=False)
    lender_id = Column(Integer, index=True, nullable=False)
    currency = Column(String(3), default="MXN", server_default="MXN", nullable=False)
    funded_amount_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    commission_rate_bps = Column(Integer, nullable=False)
    expected_commission_cents = Column(BigInteger().with_variant(Integer, "sqlite"), nullable=False)
    paid_commission_cents = Column(BigInteger().with_variant(Integer, "sqlite"), default=0, server_default="0", nullable=False)
    status = Column(String(25), default="PENDING", server_default="PENDING", index=True, nullable=False)
    expected_pay_date = Column(Date, nullable=True)
    invoiced_at = Column(DateTime(timezone=True), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    payment_reference = Column(String(120), nullable=True)
    notes = Column(Text, nullable=True)
    recorded_by_user_id = Column(Integer, nullable=False)
    updated_by_user_id = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "commission_number", name="uq_broker_commissions_tenant_number"),
        UniqueConstraint("tenant_id", "broker_case_id", name="uq_broker_commissions_tenant_case"),
        ForeignKeyConstraint(
            ["tenant_id", "broker_case_id"],
            ["broker_cases.tenant_id", "broker_cases.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_case",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lender_id"],
            ["broker_lenders.tenant_id", "broker_lenders.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_lender",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "broker_case_id", "lender_id", "submission_id"],
            [
                "broker_submissions.tenant_id",
                "broker_submissions.broker_case_id",
                "broker_submissions.lender_id",
                "broker_submissions.id",
            ],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_submission_match",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recorded_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_recorded_by",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_updated_by",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'INVOICED', 'PAID', 'CANCELLED')",
            name="ck_broker_commissions_status_valid",
        ),
        CheckConstraint("funded_amount_cents > 0", name="ck_broker_commissions_funded_amount_positive"),
        CheckConstraint(
            "commission_rate_bps >= 0 AND commission_rate_bps <= 5000",
            name="ck_broker_commissions_rate_bps_range",
        ),
        CheckConstraint(
            "expected_commission_cents >= 0",
            name="ck_broker_commissions_expected_non_negative",
        ),
        CheckConstraint(
            "paid_commission_cents >= 0",
            name="ck_broker_commissions_paid_non_negative",
        ),
    )

    broker_case = relationship(
        "BrokerCaseModel",
        foreign_keys=[tenant_id, broker_case_id],
        primaryjoin="and_(BrokerCommissionModel.tenant_id==BrokerCaseModel.tenant_id, BrokerCommissionModel.broker_case_id==BrokerCaseModel.id)",
        overlaps="lender,submission",
    )
    lender = relationship(
        "BrokerLenderModel",
        foreign_keys=[tenant_id, lender_id],
        primaryjoin="and_(BrokerCommissionModel.tenant_id==BrokerLenderModel.tenant_id, BrokerCommissionModel.lender_id==BrokerLenderModel.id)",
        overlaps="broker_case,submission",
    )
    submission = relationship(
        "BrokerSubmissionModel",
        foreign_keys=[tenant_id, broker_case_id, lender_id, submission_id],
        primaryjoin="and_(BrokerCommissionModel.tenant_id==BrokerSubmissionModel.tenant_id, BrokerCommissionModel.broker_case_id==BrokerSubmissionModel.broker_case_id, BrokerCommissionModel.lender_id==BrokerSubmissionModel.lender_id, BrokerCommissionModel.submission_id==BrokerSubmissionModel.id)",
        overlaps="broker_case,lender",
    )
