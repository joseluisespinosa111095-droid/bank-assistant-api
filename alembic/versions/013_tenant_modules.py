"""tenant_modules

Revision ID: 013
Revises: 012
Create Date: 2026-11-26 19:05:00.000000

Introduce la infraestructura multi-modulo y control de entitlements (Entregable 5A - Bloque 1):
- Crea catalogo `platform_modules` (CORE, LENDING, BROKER, COMMERCE, FINANCING),
  distinguiendo disponibilidad comercial (`is_assignable=TRUE` solo para CORE y LENDING).
- Crea grafo de dependencias `platform_module_dependencies` con PK compuesta y
  CHECK (module_code <> required_module_code).
- Crea asignacion por tenant `tenant_modules` con `config_json` (JSONB en PostgreSQL),
  `UNIQUE (tenant_id, module_code)` y FKs hacia `tenants`, `platform_modules` y `users`.
- Backfill determinista e idempotente: asigna CORE + LENDING (`is_enabled=TRUE`) a
  `default-org` y a todos los tenants comerciales existentes (`slug <> 'platform'`);
  el tenant `platform` recibe 0 filas en `tenant_modules`.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    config_json_type = sa.JSON().with_variant(
        postgresql.JSONB(astext_type=sa.Text()), "postgresql"
    )

    # 1. Catalogo de modulos de plataforma
    op.create_table(
        "platform_modules",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_core_mandatory", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_assignable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("code", name="pk_platform_modules"),
        sa.CheckConstraint("length(trim(code)) >= 2", name="ck_platform_modules_code_non_empty"),
    )

    # 2. Grafo de dependencias entre modulos
    op.create_table(
        "platform_module_dependencies",
        sa.Column("module_code", sa.String(length=40), nullable=False),
        sa.Column("required_module_code", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint(
            "module_code",
            "required_module_code",
            name="pk_platform_module_dependencies",
        ),
        sa.ForeignKeyConstraint(
            ["module_code"],
            ["platform_modules.code"],
            ondelete="RESTRICT",
            name="fk_pmod_dep_module_code",
        ),
        sa.ForeignKeyConstraint(
            ["required_module_code"],
            ["platform_modules.code"],
            ondelete="RESTRICT",
            name="fk_pmod_dep_required_code",
        ),
        sa.CheckConstraint(
            "module_code <> required_module_code",
            name="ck_platform_module_dependencies_no_self_dep",
        ),
    )

    # 3. Modulos asignados por tenant (entitlements)
    op.create_table(
        "tenant_modules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("module_code", sa.String(length=40), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("config_json", config_json_type, nullable=True),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_tenant_modules"),
        sa.UniqueConstraint("tenant_id", "module_code", name="uq_tenant_modules_tenant_module"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
            name="fk_tenant_modules_tenant_id",
        ),
        sa.ForeignKeyConstraint(
            ["module_code"],
            ["platform_modules.code"],
            ondelete="RESTRICT",
            name="fk_tenant_modules_module_code",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_tenant_modules_updated_by_user_id",
        ),
    )
    op.create_index("ix_tenant_modules_id", "tenant_modules", ["id"], unique=False)
    op.create_index("ix_tenant_modules_tenant_id", "tenant_modules", ["tenant_id"], unique=False)
    op.create_index("ix_tenant_modules_module_code", "tenant_modules", ["module_code"], unique=False)
    op.create_index("ix_tenant_modules_is_enabled", "tenant_modules", ["is_enabled"], unique=False)

    # 4. Seed de los 5 modulos del catalogo (solo CORE y LENDING son asignables en 5A)
    op.execute(
        """
        INSERT INTO platform_modules (code, name, description, is_core_mandatory, is_assignable, is_active)
        VALUES
            ('CORE', 'NURA Core CRM', 'Core multi-tenant CRM: leads, customers, KYC documents, team RBAC, audit logs, and assistant.', true, true, true),
            ('LENDING', 'NURA Lending', 'Direct credit origination, amortization schedules, loan servicing, payments, and collections.', false, true, true),
            ('BROKER', 'NURA Credit Broker', 'Credit brokerage intermediation: financial institutions, multi-lender submissions, offers, and commissions.', false, false, true),
            ('COMMERCE', 'NURA Commerce', 'Commercial catalog, quotes, and sales order management.', false, false, true),
            ('FINANCING', 'NURA Financing', 'Point-of-sale and embedded commercial financing workflows.', false, false, true)
        ON CONFLICT (code) DO NOTHING
        """
    )

    # 5. Seed de dependencias entre modulos
    op.execute(
        """
        INSERT INTO platform_module_dependencies (module_code, required_module_code)
        VALUES
            ('LENDING', 'CORE'),
            ('BROKER', 'CORE'),
            ('COMMERCE', 'CORE'),
            ('FINANCING', 'CORE'),
            ('FINANCING', 'COMMERCE')
        ON CONFLICT (module_code, required_module_code) DO NOTHING
        """
    )

    # 6. Backfill determinista e idempotente: CORE + LENDING a todos los tenants comerciales existentes
    #    (incluyendo default-org). El tenant 'platform' NO recibe modulos comerciales.
    op.execute(
        """
        INSERT INTO tenant_modules (
            tenant_id,
            module_code,
            is_enabled,
            config_json,
            enabled_at,
            disabled_at,
            updated_by_user_id,
            created_at,
            updated_at
        )
        SELECT
            t.id,
            m.code,
            true,
            NULL,
            CURRENT_TIMESTAMP,
            NULL,
            NULL,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM tenants t
        CROSS JOIN platform_modules m
        WHERE t.slug <> 'platform'
          AND m.code IN ('CORE', 'LENDING')
        ON CONFLICT (tenant_id, module_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_modules_is_enabled", table_name="tenant_modules")
    op.drop_index("ix_tenant_modules_module_code", table_name="tenant_modules")
    op.drop_index("ix_tenant_modules_tenant_id", table_name="tenant_modules")
    op.drop_index("ix_tenant_modules_id", table_name="tenant_modules")
    op.drop_table("tenant_modules")
    op.drop_table("platform_module_dependencies")
    op.drop_table("platform_modules")
