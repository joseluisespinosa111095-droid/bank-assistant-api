"""broker_module

Revision ID: 014
Revises: 013
Create Date: 2026-11-27 00:50:00.000000

Introduce el esquema relacional tenant-scoped del modulo BROKER (Entregable 5B - Bloque 1):
- Crea `broker_lenders`, `broker_cases`, `broker_submissions` y `broker_commissions`
  con FKs compuestas `(tenant_id, ...)` (`ondelete="RESTRICT"`), constraints CHECK/UNIQUE,
  indice unico parcial `uq_broker_submissions_selected_per_case` (maximo 1 oferta SELECTED por caso)
  e indices operativos.
- Habilita `platform_modules.is_assignable = true` para `BROKER` (manteniendo `COMMERCE` y
  `FINANCING` en `false`) y NO realiza backfill automatico en `tenant_modules` para tenants existentes.
"""
from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    money_int = sa.BigInteger().with_variant(sa.Integer(), "sqlite")

    # 1. Catalogo de instituciones financieras / fondeadores por tenant
    op.create_table(
        "broker_lenders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("lender_type", sa.String(length=40), server_default="BANK", nullable=False),
        sa.Column("default_commission_bps", sa.Integer(), server_default="0", nullable=False),
        sa.Column("contact_name", sa.String(length=120), nullable=True),
        sa.Column("contact_email", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=25), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broker_lenders"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_broker_lenders_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_broker_lenders_tenant_code"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_broker_lenders_tenant_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_lenders_tenant_created_by",
        ),
        sa.CheckConstraint("length(trim(code)) >= 2", name="ck_broker_lenders_code_non_empty"),
        sa.CheckConstraint("length(trim(name)) >= 2", name="ck_broker_lenders_name_non_empty"),
        sa.CheckConstraint(
            "lender_type IN ('BANK', 'SOFOM', 'FINTECH', 'CREDIT_UNION', 'PRIVATE_LENDER', 'OTHER')",
            name="ck_broker_lenders_type_valid",
        ),
        sa.CheckConstraint(
            "default_commission_bps >= 0 AND default_commission_bps <= 5000",
            name="ck_broker_lenders_commission_bps_range",
        ),
    )
    op.create_index("ix_broker_lenders_id", "broker_lenders", ["id"], unique=False)
    op.create_index("ix_broker_lenders_tenant_id", "broker_lenders", ["tenant_id"], unique=False)
    op.create_index("ix_broker_lenders_is_active", "broker_lenders", ["is_active"], unique=False)

    # 2. Expedientes de intermediacion crediticia
    op.create_table(
        "broker_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("case_number", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("source_lead_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="DRAFT", nullable=False),
        sa.Column("product_type", sa.String(length=60), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="MXN", nullable=False),
        sa.Column("requested_amount_cents", money_int, nullable=False),
        sa.Column("requested_term_months", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("funded_amount_cents", money_int, nullable=True),
        sa.Column("funded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_loan_reference", sa.String(length=120), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("closed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broker_cases"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_broker_cases_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "case_number", name="uq_broker_cases_tenant_case_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_broker_cases_tenant_idempotency_key"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id"],
            ["customers.tenant_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_customer",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "customer_id", "source_lead_id"],
            ["leads.tenant_id", "leads.converted_customer_id", "leads.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_customer_source_lead",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_owner_user",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "closed_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_cases_tenant_closed_by",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'OFFERS_RECEIVED', 'FUNDED', 'CLOSED_LOST', 'CANCELLED')",
            name="ck_broker_cases_status_valid",
        ),
        sa.CheckConstraint("length(trim(product_type)) >= 2", name="ck_broker_cases_product_type_non_empty"),
        sa.CheckConstraint("requested_amount_cents > 0", name="ck_broker_cases_requested_amount_positive"),
        sa.CheckConstraint(
            "requested_term_months IS NULL OR (requested_term_months >= 1 AND requested_term_months <= 360)",
            name="ck_broker_cases_term_months_range",
        ),
        sa.CheckConstraint(
            "funded_amount_cents IS NULL OR funded_amount_cents > 0",
            name="ck_broker_cases_funded_amount_positive",
        ),
    )
    op.create_index("ix_broker_cases_id", "broker_cases", ["id"], unique=False)
    op.create_index("ix_broker_cases_tenant_id", "broker_cases", ["tenant_id"], unique=False)
    op.create_index("ix_broker_cases_case_number", "broker_cases", ["case_number"], unique=False)
    op.create_index("ix_broker_cases_customer_id", "broker_cases", ["customer_id"], unique=False)
    op.create_index("ix_broker_cases_source_lead_id", "broker_cases", ["source_lead_id"], unique=False)
    op.create_index("ix_broker_cases_status", "broker_cases", ["status"], unique=False)
    op.create_index("ix_broker_cases_owner_user_id", "broker_cases", ["owner_user_id"], unique=False)

    # 3. Envios multi-institucion y ofertas recibidas
    op.create_table(
        "broker_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("broker_case_id", sa.Integer(), nullable=False),
        sa.Column("lender_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="DRAFT", nullable=False),
        sa.Column("external_reference", sa.String(length=120), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("offered_amount_cents", money_int, nullable=True),
        sa.Column("offered_rate_bps", sa.Integer(), nullable=True),
        sa.Column("offered_term_months", sa.Integer(), nullable=True),
        sa.Column("offered_monthly_payment_cents", money_int, nullable=True),
        sa.Column("offered_commission_bps", sa.Integer(), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broker_submissions"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_broker_submissions_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "broker_case_id",
            "lender_id",
            name="uq_broker_submissions_tenant_case_lender",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "broker_case_id",
            "lender_id",
            "id",
            name="uq_broker_submissions_tenant_case_lender_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "broker_case_id"],
            ["broker_cases.tenant_id", "broker_cases.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_case",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "lender_id"],
            ["broker_lenders.tenant_id", "broker_lenders.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_lender",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_created_by",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_submissions_tenant_updated_by",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'IN_UNDERWRITING', 'APPROVED', 'DECLINED', 'WITHDRAWN', 'SELECTED')",
            name="ck_broker_submissions_status_valid",
        ),
        sa.CheckConstraint(
            "offered_amount_cents IS NULL OR offered_amount_cents > 0",
            name="ck_broker_submissions_offered_amount_positive",
        ),
        sa.CheckConstraint(
            "offered_rate_bps IS NULL OR offered_rate_bps >= 0",
            name="ck_broker_submissions_offered_rate_non_negative",
        ),
        sa.CheckConstraint(
            "offered_term_months IS NULL OR (offered_term_months >= 1 AND offered_term_months <= 360)",
            name="ck_broker_submissions_offered_term_range",
        ),
        sa.CheckConstraint(
            "offered_monthly_payment_cents IS NULL OR offered_monthly_payment_cents >= 0",
            name="ck_broker_submissions_monthly_pay_non_negative",
        ),
        sa.CheckConstraint(
            "offered_commission_bps IS NULL OR (offered_commission_bps >= 0 AND offered_commission_bps <= 5000)",
            name="ck_broker_submissions_commission_bps_range",
        ),
    )
    op.create_index("ix_broker_submissions_id", "broker_submissions", ["id"], unique=False)
    op.create_index("ix_broker_submissions_tenant_id", "broker_submissions", ["tenant_id"], unique=False)
    op.create_index("ix_broker_submissions_case_id", "broker_submissions", ["broker_case_id"], unique=False)
    op.create_index("ix_broker_submissions_lender_id", "broker_submissions", ["lender_id"], unique=False)
    op.create_index("ix_broker_submissions_status", "broker_submissions", ["status"], unique=False)
    op.create_index(
        "uq_broker_submissions_selected_per_case",
        "broker_submissions",
        ["tenant_id", "broker_case_id"],
        unique=True,
        postgresql_where=sa.text("status = 'SELECTED'"),
        sqlite_where=sa.text("status = 'SELECTED'"),
    )

    # 4. Comisiones por colocacion / fondeo externo
    op.create_table(
        "broker_commissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("commission_number", sa.String(length=24), nullable=False),
        sa.Column("broker_case_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("lender_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="MXN", nullable=False),
        sa.Column("funded_amount_cents", money_int, nullable=False),
        sa.Column("commission_rate_bps", sa.Integer(), nullable=False),
        sa.Column("expected_commission_cents", money_int, nullable=False),
        sa.Column("paid_commission_cents", money_int, server_default="0", nullable=False),
        sa.Column("status", sa.String(length=25), server_default="PENDING", nullable=False),
        sa.Column("expected_pay_date", sa.Date(), nullable=True),
        sa.Column("invoiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_reference", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broker_commissions"),
        sa.UniqueConstraint("tenant_id", "commission_number", name="uq_broker_commissions_tenant_number"),
        sa.UniqueConstraint("tenant_id", "broker_case_id", name="uq_broker_commissions_tenant_case"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "broker_case_id"],
            ["broker_cases.tenant_id", "broker_cases.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_case",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "lender_id"],
            ["broker_lenders.tenant_id", "broker_lenders.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_lender",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "recorded_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_recorded_by",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_broker_commissions_tenant_updated_by",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'INVOICED', 'PAID', 'CANCELLED')",
            name="ck_broker_commissions_status_valid",
        ),
        sa.CheckConstraint("funded_amount_cents > 0", name="ck_broker_commissions_funded_amount_positive"),
        sa.CheckConstraint(
            "commission_rate_bps >= 0 AND commission_rate_bps <= 5000",
            name="ck_broker_commissions_rate_bps_range",
        ),
        sa.CheckConstraint(
            "expected_commission_cents >= 0",
            name="ck_broker_commissions_expected_non_negative",
        ),
        sa.CheckConstraint(
            "paid_commission_cents >= 0",
            name="ck_broker_commissions_paid_non_negative",
        ),
    )
    op.create_index("ix_broker_commissions_id", "broker_commissions", ["id"], unique=False)
    op.create_index("ix_broker_commissions_tenant_id", "broker_commissions", ["tenant_id"], unique=False)
    op.create_index("ix_broker_commissions_status", "broker_commissions", ["status"], unique=False)
    op.create_index("ix_broker_commissions_lender_id", "broker_commissions", ["lender_id"], unique=False)

    # 5. Habilitar BROKER como modulo asignable (sin backfill automatico a tenants existentes)
    op.execute("UPDATE platform_modules SET is_assignable = true WHERE code = 'BROKER'")


def downgrade() -> None:
    op.execute("UPDATE platform_modules SET is_assignable = false WHERE code = 'BROKER'")

    op.drop_index("ix_broker_commissions_lender_id", table_name="broker_commissions")
    op.drop_index("ix_broker_commissions_status", table_name="broker_commissions")
    op.drop_index("ix_broker_commissions_tenant_id", table_name="broker_commissions")
    op.drop_index("ix_broker_commissions_id", table_name="broker_commissions")
    op.drop_table("broker_commissions")

    op.drop_index("uq_broker_submissions_selected_per_case", table_name="broker_submissions")
    op.drop_index("ix_broker_submissions_status", table_name="broker_submissions")
    op.drop_index("ix_broker_submissions_lender_id", table_name="broker_submissions")
    op.drop_index("ix_broker_submissions_case_id", table_name="broker_submissions")
    op.drop_index("ix_broker_submissions_tenant_id", table_name="broker_submissions")
    op.drop_index("ix_broker_submissions_id", table_name="broker_submissions")
    op.drop_table("broker_submissions")

    op.drop_index("ix_broker_cases_owner_user_id", table_name="broker_cases")
    op.drop_index("ix_broker_cases_status", table_name="broker_cases")
    op.drop_index("ix_broker_cases_source_lead_id", table_name="broker_cases")
    op.drop_index("ix_broker_cases_customer_id", table_name="broker_cases")
    op.drop_index("ix_broker_cases_case_number", table_name="broker_cases")
    op.drop_index("ix_broker_cases_tenant_id", table_name="broker_cases")
    op.drop_index("ix_broker_cases_id", table_name="broker_cases")
    op.drop_table("broker_cases")

    op.drop_index("ix_broker_lenders_is_active", table_name="broker_lenders")
    op.drop_index("ix_broker_lenders_tenant_id", table_name="broker_lenders")
    op.drop_index("ix_broker_lenders_id", table_name="broker_lenders")
    op.drop_table("broker_lenders")
