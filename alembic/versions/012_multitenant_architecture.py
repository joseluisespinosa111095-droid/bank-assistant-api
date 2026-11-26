"""multitenant_architecture

Revision ID: 012
Revises: 011
Create Date: 2026-11-25 23:30:00.000000

Introduce la arquitectura SaaS multi-tenant:
- Crea tablas tenants y tenant_sequences.
- Inserta tenants iniciales 'platform' y 'default-org'.
- Agrega tenant_id (NOT NULL) a las 12 tablas operativas, is_superadmin a users,
  y tenant_id (NULLABLE) + scope (NOT NULL, CHECK) a audit_logs.
- Backfill seguro del 100% de registros comerciales existentes a 'default-org'
  (0 registros comerciales a 'platform') e inicializacion de tenant_sequences.
- Constraints UNIQUE compuestos por tenant_id, integridad referencial compuesta
  (2 y 3 columnas) e indice parcial uq_loan_payments_posted_reference con tenant_id.
"""
from alembic import op
import sqlalchemy as sa


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


OPERATIONAL_TABLES = (
    "users",
    "conversations",
    "chat_messages",
    "customers",
    "customer_documents",
    "leads",
    "lead_stage_history",
    "interactions",
    "loans",
    "loan_installments",
    "loan_payments",
    "loan_payment_allocations",
)

POSTED_REF_INDEX_NAME = "uq_loan_payments_posted_reference"
POSTED_REF_PREDICATE = (
    "status = 'POSTED' "
    "AND payment_method IN ('SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK') "
    "AND payment_reference IS NOT NULL "
    "AND length(trim(payment_reference)) > 0"
)

LEGACY_FK_NAMING_CONVENTION = {
    "fk": "fk_legacy_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _seed_tenant_sequences_sql(dialect_name: str) -> list[str]:
    """Genera SQL puro (compatible con modo online y offline --sql) para inicializar tenant_sequences."""
    specs = (
        ("customers", "customers", "customer_number", "CUST-", 6),
        ("leads", "leads", "lead_number", "LD-", 4),
        ("loans", "loans", "loan_number", "LN-", 4),
        ("loan_payments", "loan_payments", "payment_number", "PAY-", 5),
    )
    statements = []
    for entity_type, table_name, num_col, prefix, start_pos in specs:
        if dialect_name == "postgresql":
            parsed_num = (
                f"CASE WHEN {num_col} ~ '^{prefix}[0-9]+$' "
                f"THEN CAST(substring({num_col} FROM {start_pos}) AS INTEGER) "
                f"ELSE 0 END"
            )
        else:
            parsed_num = (
                f"CASE WHEN {num_col} LIKE '{prefix}%' "
                f"AND length(substr({num_col}, {start_pos})) > 0 "
                f"AND substr({num_col}, {start_pos}) NOT GLOB '*[^0-9]*' "
                f"THEN CAST(substr({num_col}, {start_pos}) AS INTEGER) "
                f"ELSE 0 END"
            )

        statements.append(
            f"""
            INSERT INTO tenant_sequences (tenant_id, entity_type, last_value)
            SELECT
                (SELECT id FROM tenants WHERE slug = 'default-org'),
                '{entity_type}',
                CASE
                    WHEN COALESCE(MAX(id), 0) >= COALESCE(MAX({parsed_num}), 0)
                    THEN COALESCE(MAX(id), 0)
                    ELSE COALESCE(MAX({parsed_num}), 0)
                END
            FROM {table_name}
            """
        )
        statements.append(
            f"""
            INSERT INTO tenant_sequences (tenant_id, entity_type, last_value)
            SELECT id, '{entity_type}', 0
            FROM tenants
            WHERE slug = 'platform'
            """
        )
    return statements


def _upgrade_postgresql() -> None:
    # 1. Deshabilitar temporalmente triggers de inmutabilidad/conciliacion para el backfill seguro
    op.execute("ALTER TABLE audit_logs DISABLE TRIGGER trg_protect_audit_logs_row")
    op.execute("ALTER TABLE loans DISABLE TRIGGER trg_protect_loans_immutability")
    op.execute("ALTER TABLE loans DISABLE TRIGGER trg_verify_loans_ledger_consistency")
    op.execute("ALTER TABLE loan_installments DISABLE TRIGGER trg_protect_loan_installments_immutability")
    op.execute("ALTER TABLE loan_installments DISABLE TRIGGER trg_verify_installments_ledger_consistency")
    op.execute("ALTER TABLE loan_payments DISABLE TRIGGER trg_protect_loan_payments_immutability")
    op.execute("ALTER TABLE loan_payments DISABLE TRIGGER trg_verify_payments_ledger_consistency")
    op.execute("ALTER TABLE loan_payment_allocations DISABLE TRIGGER trg_protect_loan_payment_allocations_immutability")

    # 2. Backfill del 100% de datos comerciales a default-org
    for table_name in OPERATIONAL_TABLES:
        op.execute(
            f"UPDATE {table_name} "
            f"SET tenant_id = (SELECT id FROM tenants WHERE slug = 'default-org') "
            f"WHERE tenant_id IS NULL"
        )

    op.execute(
        "UPDATE audit_logs "
        "SET tenant_id = (SELECT id FROM tenants WHERE slug = 'default-org'), "
        "    scope = 'TENANT' "
        "WHERE actor_user_id IS NOT NULL"
    )
    op.execute(
        "UPDATE audit_logs "
        "SET tenant_id = NULL, "
        "    scope = 'ANONYMOUS' "
        "WHERE actor_user_id IS NULL"
    )

    # 3. Inicializar tenant_sequences para default-org y platform
    for stmt in _seed_tenant_sequences_sql("postgresql"):
        op.execute(stmt)

    # 4. Rehabilitar inmediatamente los triggers de proteccion
    op.execute("ALTER TABLE loan_payment_allocations ENABLE TRIGGER trg_protect_loan_payment_allocations_immutability")
    op.execute("ALTER TABLE loan_payments ENABLE TRIGGER trg_verify_payments_ledger_consistency")
    op.execute("ALTER TABLE loan_payments ENABLE TRIGGER trg_protect_loan_payments_immutability")
    op.execute("ALTER TABLE loan_installments ENABLE TRIGGER trg_verify_installments_ledger_consistency")
    op.execute("ALTER TABLE loan_installments ENABLE TRIGGER trg_protect_loan_installments_immutability")
    op.execute("ALTER TABLE loans ENABLE TRIGGER trg_verify_loans_ledger_consistency")
    op.execute("ALTER TABLE loans ENABLE TRIGGER trg_protect_loans_immutability")
    op.execute("ALTER TABLE audit_logs ENABLE TRIGGER trg_protect_audit_logs_row")

    # 5. Aplicar NOT NULL, indices y FK a tenants.id en las 12 tablas operativas + audit_logs
    for table_name in OPERATIONAL_TABLES:
        op.alter_column(table_name, "tenant_id", existing_type=sa.Integer(), nullable=False)
        op.create_index(f"ix_{table_name}_tenant_id", table_name, ["tenant_id"], unique=False)
        op.create_foreign_key(
            f"fk_{table_name}_tenant_id",
            table_name,
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="RESTRICT",
        )

    op.alter_column("audit_logs", "scope", existing_type=sa.String(length=20), nullable=False)
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"], unique=False)
    op.create_index("ix_audit_logs_scope", "audit_logs", ["scope"], unique=False)
    op.create_foreign_key(
        "fk_audit_logs_tenant_id",
        "audit_logs",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_audit_logs_scope_valid",
        "audit_logs",
        "scope IN ('ANONYMOUS', 'TENANT', 'PLATFORM')",
    )

    # 6. UniqueConstraints (tenant_id, id) en las 7 tablas padre + UniqueConstraints de 3 columnas
    op.create_unique_constraint("uq_users_tenant_id_id", "users", ["tenant_id", "id"])
    op.create_unique_constraint("uq_conversations_tenant_id_id", "conversations", ["tenant_id", "id"])
    op.create_unique_constraint("uq_customers_tenant_id_id", "customers", ["tenant_id", "id"])
    op.create_unique_constraint("uq_leads_tenant_id_id", "leads", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_leads_tenant_converted_customer_id",
        "leads",
        ["tenant_id", "converted_customer_id", "id"],
    )
    op.create_unique_constraint("uq_loans_tenant_id_id", "loans", ["tenant_id", "id"])
    op.create_unique_constraint("uq_loans_tenant_customer_id", "loans", ["tenant_id", "customer_id", "id"])
    op.create_unique_constraint("uq_loan_installments_tenant_id_id", "loan_installments", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_loan_installments_tenant_loan_id",
        "loan_installments",
        ["tenant_id", "loan_id", "id"],
    )
    op.create_unique_constraint("uq_loan_payments_tenant_id_id", "loan_payments", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_loan_payments_tenant_loan_id",
        "loan_payments",
        ["tenant_id", "loan_id", "id"],
    )

    # 7. Convertir constraints/indices UNIQUE globales en compuestos por tenant_id
    op.drop_index("ix_customers_customer_number", table_name="customers")
    op.drop_index("ix_customers_phone_primary_normalized", table_name="customers")
    op.create_index("ix_customers_customer_number", "customers", ["customer_number"], unique=False)
    op.create_index("ix_customers_phone_primary_normalized", "customers", ["phone_primary_normalized"], unique=False)
    op.create_unique_constraint("uq_customers_tenant_customer_number", "customers", ["tenant_id", "customer_number"])
    op.create_unique_constraint("uq_customers_tenant_phone_primary", "customers", ["tenant_id", "phone_primary_normalized"])

    op.drop_index("ix_leads_lead_number", table_name="leads")
    op.create_index("ix_leads_lead_number", "leads", ["lead_number"], unique=False)
    op.create_unique_constraint("uq_leads_tenant_lead_number", "leads", ["tenant_id", "lead_number"])

    op.drop_index("ix_loans_loan_number", table_name="loans")
    op.create_index("ix_loans_loan_number", "loans", ["loan_number"], unique=False)
    op.drop_constraint("uq_loans_idempotency_key", "loans", type_="unique")
    op.create_unique_constraint("uq_loans_tenant_loan_number", "loans", ["tenant_id", "loan_number"])
    op.create_unique_constraint("uq_loans_tenant_idempotency_key", "loans", ["tenant_id", "idempotency_key"])
    op.execute("DROP INDEX IF EXISTS uq_loans_disbursement_method_ref")
    op.execute("""
        CREATE UNIQUE INDEX uq_loans_disbursement_method_ref
        ON loans (tenant_id, disbursement_method, disbursement_reference)
        WHERE disbursement_method IS NOT NULL AND disbursement_reference IS NOT NULL
    """)

    op.drop_index("ix_loan_payments_payment_number", table_name="loan_payments")
    op.create_index("ix_loan_payments_payment_number", "loan_payments", ["payment_number"], unique=False)
    op.drop_constraint("uq_loan_payments_idempotency_key", "loan_payments", type_="unique")
    op.create_unique_constraint("uq_loan_payments_tenant_payment_number", "loan_payments", ["tenant_id", "payment_number"])
    op.create_unique_constraint("uq_loan_payments_tenant_idempotency_key", "loan_payments", ["tenant_id", "idempotency_key"])

    op.drop_index(POSTED_REF_INDEX_NAME, table_name="loan_payments")
    op.create_index(
        POSTED_REF_INDEX_NAME,
        "loan_payments",
        ["tenant_id", "payment_method", sa.text("upper(trim(payment_reference))")],
        unique=True,
        postgresql_where=sa.text(POSTED_REF_PREDICATE),
        sqlite_where=sa.text(POSTED_REF_PREDICATE),
    )
    op.execute("DROP INDEX IF EXISTS uq_loan_payments_posted_method_ref")
    op.execute("""
        CREATE UNIQUE INDEX uq_loan_payments_posted_method_ref
        ON loan_payments (tenant_id, payment_method, upper(btrim(payment_reference)))
        WHERE status = 'POSTED'
          AND payment_reference IS NOT NULL
          AND payment_method IN ('SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK')
    """)

    # 8. Reemplazar FKs simples por FKs compuestas (2 y 3 columnas) en las 12 tablas operativas
    legacy_pg_fks = (
        ("conversations", "conversations_user_id_fkey"),
        ("chat_messages", "chat_messages_conversation_id_fkey"),
        ("customers", "customers_owner_user_id_fkey"),
        ("customers", "customers_created_by_user_id_fkey"),
        ("customer_documents", "customer_documents_customer_id_fkey"),
        ("customer_documents", "customer_documents_uploaded_by_user_id_fkey"),
        ("leads", "leads_owner_user_id_fkey"),
        ("leads", "leads_created_by_user_id_fkey"),
        ("leads", "leads_converted_customer_id_fkey"),
        ("leads", "leads_converted_by_user_id_fkey"),
        ("lead_stage_history", "lead_stage_history_lead_id_fkey"),
        ("lead_stage_history", "lead_stage_history_changed_by_user_id_fkey"),
        ("interactions", "interactions_lead_id_fkey"),
        ("interactions", "interactions_customer_id_fkey"),
        ("interactions", "interactions_created_by_user_id_fkey"),
        ("loans", "loans_customer_id_fkey"),
        ("loans", "loans_source_lead_id_fkey"),
        ("loans", "loans_owner_user_id_fkey"),
        ("loans", "loans_created_by_user_id_fkey"),
        ("loans", "loans_approved_by_user_id_fkey"),
        ("loans", "loans_activated_by_user_id_fkey"),
        ("loans", "loans_cancelled_by_user_id_fkey"),
        ("loan_installments", "loan_installments_loan_id_fkey"),
        ("loan_payments", "loan_payments_loan_id_fkey"),
        ("loan_payments", "loan_payments_customer_id_fkey"),
        ("loan_payments", "loan_payments_recorded_by_user_id_fkey"),
        ("loan_payments", "loan_payments_reversed_by_user_id_fkey"),
        ("loan_payment_allocations", "loan_payment_allocations_payment_id_fkey"),
        ("loan_payment_allocations", "loan_payment_allocations_loan_id_fkey"),
        ("loan_payment_allocations", "loan_payment_allocations_installment_id_fkey"),
    )
    for tbl, cname in legacy_pg_fks:
        op.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {cname}")

    # 2-col FKs (26)
    op.create_foreign_key("fk_conversations_tenant_user", "conversations", "users", ["tenant_id", "user_id"], ["tenant_id", "id"], ondelete="CASCADE")
    op.create_foreign_key("fk_chat_messages_tenant_conversation", "chat_messages", "conversations", ["tenant_id", "conversation_id"], ["tenant_id", "id"], ondelete="CASCADE")

    op.create_foreign_key("fk_customers_tenant_owner_user", "customers", "users", ["tenant_id", "owner_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_customers_tenant_created_by_user", "customers", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_customer_documents_tenant_customer", "customer_documents", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_customer_documents_tenant_uploaded_by", "customer_documents", "users", ["tenant_id", "uploaded_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_leads_tenant_owner_user", "leads", "users", ["tenant_id", "owner_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_leads_tenant_created_by_user", "leads", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_leads_tenant_converted_customer", "leads", "customers", ["tenant_id", "converted_customer_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_leads_tenant_converted_by_user", "leads", "users", ["tenant_id", "converted_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_lead_stage_history_tenant_lead", "lead_stage_history", "leads", ["tenant_id", "lead_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_lead_stage_history_tenant_changed_by", "lead_stage_history", "users", ["tenant_id", "changed_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_interactions_tenant_lead", "interactions", "leads", ["tenant_id", "lead_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_interactions_tenant_customer", "interactions", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_interactions_tenant_created_by", "interactions", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_loans_tenant_customer", "loans", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loans_tenant_owner_user", "loans", "users", ["tenant_id", "owner_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loans_tenant_created_by_user", "loans", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loans_tenant_approved_by_user", "loans", "users", ["tenant_id", "approved_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loans_tenant_activated_by_user", "loans", "users", ["tenant_id", "activated_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loans_tenant_cancelled_by_user", "loans", "users", ["tenant_id", "cancelled_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_loan_installments_tenant_loan", "loan_installments", "loans", ["tenant_id", "loan_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_loan_payments_tenant_customer", "loan_payments", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loan_payments_tenant_recorded_by", "loan_payments", "users", ["tenant_id", "recorded_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_loan_payments_tenant_reversed_by", "loan_payments", "users", ["tenant_id", "reversed_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    op.create_foreign_key("fk_payment_alloc_tenant_loan", "loan_payment_allocations", "loans", ["tenant_id", "loan_id"], ["tenant_id", "id"], ondelete="RESTRICT")

    # 3-col FKs (4)
    op.create_foreign_key(
        "fk_loans_tenant_customer_source_lead",
        "loans",
        "leads",
        ["tenant_id", "customer_id", "source_lead_id"],
        ["tenant_id", "converted_customer_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_loan_payments_tenant_customer_loan",
        "loan_payments",
        "loans",
        ["tenant_id", "customer_id", "loan_id"],
        ["tenant_id", "customer_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_payment_alloc_tenant_loan_payment",
        "loan_payment_allocations",
        "loan_payments",
        ["tenant_id", "loan_id", "payment_id"],
        ["tenant_id", "loan_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_payment_alloc_tenant_loan_installment",
        "loan_payment_allocations",
        "loan_installments",
        ["tenant_id", "loan_id", "installment_id"],
        ["tenant_id", "loan_id", "id"],
        ondelete="RESTRICT",
    )


def _upgrade_sqlite() -> None:
    # 1. Backfill del 100% de datos comerciales a default-org
    for table_name in OPERATIONAL_TABLES:
        op.execute(
            f"UPDATE {table_name} "
            f"SET tenant_id = (SELECT id FROM tenants WHERE slug = 'default-org') "
            f"WHERE tenant_id IS NULL"
        )

    op.execute(
        "UPDATE audit_logs "
        "SET tenant_id = (SELECT id FROM tenants WHERE slug = 'default-org'), "
        "    scope = 'TENANT' "
        "WHERE actor_user_id IS NOT NULL"
    )
    op.execute(
        "UPDATE audit_logs "
        "SET tenant_id = NULL, "
        "    scope = 'ANONYMOUS' "
        "WHERE actor_user_id IS NULL"
    )

    # 2. Inicializar tenant_sequences para default-org y platform
    for stmt in _seed_tenant_sequences_sql("sqlite"):
        op.execute(stmt)

    # 3. Eliminar indices UNIQUE globales que pasan a compuestos por tenant_id
    op.drop_index("ix_customers_customer_number", table_name="customers")
    op.drop_index("ix_customers_phone_primary_normalized", table_name="customers")
    op.drop_index("ix_leads_lead_number", table_name="leads")
    op.drop_index("ix_loans_loan_number", table_name="loans")
    op.drop_index("ix_loan_payments_payment_number", table_name="loan_payments")
    op.drop_index(POSTED_REF_INDEX_NAME, table_name="loan_payments")

    # 4. Recreacion por lotes (batch_alter_table) en orden padre -> hijo
    with op.batch_alter_table("users", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_users_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_users_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_users_tenant_id_id", ["tenant_id", "id"])

    with op.batch_alter_table("conversations", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_conversations_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_conversations_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_conversations_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_conversations_tenant_id_id", ["tenant_id", "id"])
        batch_op.create_foreign_key(
            "fk_conversations_tenant_user", "users", ["tenant_id", "user_id"], ["tenant_id", "id"], ondelete="CASCADE"
        )

    with op.batch_alter_table("chat_messages", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_chat_messages_conversation_id_conversations", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_chat_messages_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_chat_messages_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_foreign_key(
            "fk_chat_messages_tenant_conversation",
            "conversations",
            ["tenant_id", "conversation_id"],
            ["tenant_id", "id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("audit_logs", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.alter_column("scope", existing_type=sa.String(length=20), nullable=False)
        batch_op.create_index("ix_audit_logs_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_audit_logs_scope", ["scope"], unique=False)
        batch_op.create_foreign_key("fk_audit_logs_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_check_constraint("ck_audit_logs_scope_valid", "scope IN ('ANONYMOUS', 'TENANT', 'PLATFORM')")

    with op.batch_alter_table("customers", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_customers_owner_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_customers_created_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_customers_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_customers_customer_number", ["customer_number"], unique=False)
        batch_op.create_index("ix_customers_phone_primary_normalized", ["phone_primary_normalized"], unique=False)
        batch_op.create_foreign_key("fk_customers_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_customers_tenant_id_id", ["tenant_id", "id"])
        batch_op.create_unique_constraint("uq_customers_tenant_customer_number", ["tenant_id", "customer_number"])
        batch_op.create_unique_constraint("uq_customers_tenant_phone_primary", ["tenant_id", "phone_primary_normalized"])
        batch_op.create_foreign_key(
            "fk_customers_tenant_owner_user", "users", ["tenant_id", "owner_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_customers_tenant_created_by_user", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("customer_documents", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_customer_documents_customer_id_customers", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_customer_documents_uploaded_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_customer_documents_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_customer_documents_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_foreign_key(
            "fk_customer_documents_tenant_customer", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_customer_documents_tenant_uploaded_by", "users", ["tenant_id", "uploaded_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("leads", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_leads_owner_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_leads_created_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_leads_converted_customer_id_customers", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_leads_converted_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_leads_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_leads_lead_number", ["lead_number"], unique=False)
        batch_op.create_foreign_key("fk_leads_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_leads_tenant_id_id", ["tenant_id", "id"])
        batch_op.create_unique_constraint("uq_leads_tenant_lead_number", ["tenant_id", "lead_number"])
        batch_op.create_unique_constraint(
            "uq_leads_tenant_converted_customer_id", ["tenant_id", "converted_customer_id", "id"]
        )
        batch_op.create_foreign_key(
            "fk_leads_tenant_owner_user", "users", ["tenant_id", "owner_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_leads_tenant_created_by_user", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_leads_tenant_converted_customer", "customers", ["tenant_id", "converted_customer_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_leads_tenant_converted_by_user", "users", ["tenant_id", "converted_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("lead_stage_history", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_lead_stage_history_lead_id_leads", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_lead_stage_history_changed_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_lead_stage_history_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_lead_stage_history_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_foreign_key(
            "fk_lead_stage_history_tenant_lead", "leads", ["tenant_id", "lead_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_lead_stage_history_tenant_changed_by", "users", ["tenant_id", "changed_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("interactions", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_interactions_lead_id_leads", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_interactions_customer_id_customers", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_interactions_created_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_interactions_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_interactions_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_foreign_key(
            "fk_interactions_tenant_lead", "leads", ["tenant_id", "lead_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_interactions_tenant_customer", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_interactions_tenant_created_by", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("loans", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("ck_loans_stage_balance_invariants", type_="check")
        batch_op.drop_constraint("uq_loans_idempotency_key", type_="unique")
        batch_op.drop_constraint("fk_legacy_loans_customer_id_customers", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loans_source_lead_id_leads", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loans_owner_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loans_created_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loans_approved_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loans_activated_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loans_cancelled_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_check_constraint(
            "ck_loans_stage_balance_invariants",
            "(status IN ('DRAFT', 'APPROVED', 'CANCELLED') "
            "AND unpaid_principal_cents = 0 "
            "AND remaining_contractual_interest_cents = 0 "
            "AND remaining_contract_total_cents = 0 "
            "AND amount_paid_cents = 0) "
            "OR (status = 'ACTIVE' "
            "AND unpaid_principal_cents >= 0 "
            "AND unpaid_principal_cents <= principal_cents "
            "AND remaining_contractual_interest_cents >= 0 "
            "AND remaining_contractual_interest_cents <= total_interest_cents "
            "AND remaining_contract_total_cents > 0 "
            "AND remaining_contract_total_cents <= total_repayment_cents "
            "AND amount_paid_cents >= 0 "
            "AND amount_paid_cents < total_repayment_cents "
            "AND amount_paid_cents + remaining_contract_total_cents = total_repayment_cents) "
            "OR (status = 'PAID_OFF' "
            "AND unpaid_principal_cents = 0 "
            "AND remaining_contractual_interest_cents = 0 "
            "AND remaining_contract_total_cents = 0 "
            "AND amount_paid_cents = total_repayment_cents) "
            "OR (status = 'DEFAULTED' "
            "AND remaining_contract_total_cents > 0 "
            "AND amount_paid_cents + remaining_contract_total_cents = total_repayment_cents)",
        )
        batch_op.create_index("ix_loans_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_loans_loan_number", ["loan_number"], unique=False)
        batch_op.create_foreign_key("fk_loans_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_loans_tenant_id_id", ["tenant_id", "id"])
        batch_op.create_unique_constraint("uq_loans_tenant_customer_id", ["tenant_id", "customer_id", "id"])
        batch_op.create_unique_constraint("uq_loans_tenant_loan_number", ["tenant_id", "loan_number"])
        batch_op.create_unique_constraint("uq_loans_tenant_idempotency_key", ["tenant_id", "idempotency_key"])
        batch_op.create_foreign_key(
            "fk_loans_tenant_customer", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loans_tenant_customer_source_lead",
            "leads",
            ["tenant_id", "customer_id", "source_lead_id"],
            ["tenant_id", "converted_customer_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_loans_tenant_owner_user", "users", ["tenant_id", "owner_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loans_tenant_created_by_user", "users", ["tenant_id", "created_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loans_tenant_approved_by_user", "users", ["tenant_id", "approved_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loans_tenant_activated_by_user", "users", ["tenant_id", "activated_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loans_tenant_cancelled_by_user", "users", ["tenant_id", "cancelled_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("loan_installments", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_loan_installments_loan_id_loans", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_loan_installments_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_loan_installments_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_loan_installments_tenant_id_id", ["tenant_id", "id"])
        batch_op.create_unique_constraint("uq_loan_installments_tenant_loan_id", ["tenant_id", "loan_id", "id"])
        batch_op.create_foreign_key(
            "fk_loan_installments_tenant_loan", "loans", ["tenant_id", "loan_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    with op.batch_alter_table("loan_payments", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("uq_loan_payments_idempotency_key", type_="unique")
        batch_op.drop_constraint("fk_legacy_loan_payments_loan_id_loans", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loan_payments_customer_id_customers", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loan_payments_recorded_by_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loan_payments_reversed_by_user_id_users", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_loan_payments_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_index("ix_loan_payments_payment_number", ["payment_number"], unique=False)
        batch_op.create_foreign_key("fk_loan_payments_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_unique_constraint("uq_loan_payments_tenant_id_id", ["tenant_id", "id"])
        batch_op.create_unique_constraint("uq_loan_payments_tenant_loan_id", ["tenant_id", "loan_id", "id"])
        batch_op.create_unique_constraint("uq_loan_payments_tenant_payment_number", ["tenant_id", "payment_number"])
        batch_op.create_unique_constraint("uq_loan_payments_tenant_idempotency_key", ["tenant_id", "idempotency_key"])
        batch_op.create_foreign_key(
            "fk_loan_payments_tenant_customer_loan",
            "loans",
            ["tenant_id", "customer_id", "loan_id"],
            ["tenant_id", "customer_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_loan_payments_tenant_customer", "customers", ["tenant_id", "customer_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loan_payments_tenant_recorded_by", "users", ["tenant_id", "recorded_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )
        batch_op.create_foreign_key(
            "fk_loan_payments_tenant_reversed_by", "users", ["tenant_id", "reversed_by_user_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )

    op.create_index(
        POSTED_REF_INDEX_NAME,
        "loan_payments",
        ["tenant_id", "payment_method", sa.text("upper(trim(payment_reference))")],
        unique=True,
        postgresql_where=sa.text(POSTED_REF_PREDICATE),
        sqlite_where=sa.text(POSTED_REF_PREDICATE),
    )

    with op.batch_alter_table("loan_payment_allocations", naming_convention=LEGACY_FK_NAMING_CONVENTION, recreate="always") as batch_op:
        batch_op.drop_constraint("fk_legacy_loan_payment_allocations_payment_id_loan_payments", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loan_payment_allocations_loan_id_loans", type_="foreignkey")
        batch_op.drop_constraint("fk_legacy_loan_payment_allocations_installment_id_loan_installments", type_="foreignkey")
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_loan_payment_allocations_tenant_id", ["tenant_id"], unique=False)
        batch_op.create_foreign_key("fk_loan_payment_allocations_tenant_id", "tenants", ["tenant_id"], ["id"], ondelete="RESTRICT")
        batch_op.create_foreign_key(
            "fk_payment_alloc_tenant_loan_payment",
            "loan_payments",
            ["tenant_id", "loan_id", "payment_id"],
            ["tenant_id", "loan_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_payment_alloc_tenant_loan_installment",
            "loan_installments",
            ["tenant_id", "loan_id", "installment_id"],
            ["tenant_id", "loan_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_payment_alloc_tenant_loan", "loans", ["tenant_id", "loan_id"], ["tenant_id", "id"], ondelete="RESTRICT"
        )


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "postgresql"

    # 1. Crear tablas tenants y tenant_sequences
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenants_id", "tenants", ["id"], unique=False)
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_index("ix_tenants_is_active", "tenants", ["is_active"], unique=False)

    op.create_table(
        "tenant_sequences",
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("last_value", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT", name="fk_tenant_sequences_tenant_id"),
        sa.PrimaryKeyConstraint("tenant_id", "entity_type", name="pk_tenant_sequences"),
    )

    # 2. Insertar los 2 tenants iniciales (platform y default-org)
    op.execute(
        "INSERT INTO tenants (name, slug, legal_name, is_active, created_at) "
        "VALUES ('NURA Platform', 'platform', NULL, true, CURRENT_TIMESTAMP)"
    )
    op.execute(
        "INSERT INTO tenants (name, slug, legal_name, is_active, created_at) "
        "VALUES ('Default Organization', 'default-org', NULL, true, CURRENT_TIMESTAMP)"
    )

    # 3. Agregar columnas inicialmente nullable (y is_superadmin con default false)
    for table_name in OPERATIONAL_TABLES:
        op.add_column(table_name, sa.Column("tenant_id", sa.Integer(), nullable=True))

    op.add_column(
        "users",
        sa.Column("is_superadmin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_index("ix_users_is_superadmin", "users", ["is_superadmin"], unique=False)

    op.add_column("audit_logs", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("audit_logs", sa.Column("scope", sa.String(length=20), nullable=True))

    # 4. Backfill + constraints + FKs compuestas por dialecto
    if dialect_name == "sqlite":
        _upgrade_sqlite()
    else:
        _upgrade_postgresql()


def downgrade() -> None:
    raise RuntimeError(
        "ABORTING DOWNGRADE 012 -> 011: Multi-tenant schema extraction cannot be safely downgraded "
        "automatically without risking cross-tenant data loss or constraint collisions."
    )
