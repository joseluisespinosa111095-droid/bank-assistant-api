"""leads_curp_name_and_followup_type

Revision ID: 015
Revises: 014
Create Date: 2026-11-27 21:15:00.000000

Agrega de forma aditiva y 100% compatible con datos existentes:
- `leads.first_name`, `leads.middle_name`, `leads.last_name` (nombre separado para Persona Fisica)
- `leads.curp` y `customers.curp` (con indice operativo)
- `leads.next_follow_up_type` y `interactions.next_follow_up_type` ('CALL' | 'MEETING')
  para diferenciar llamadas vs citas en persona en el calendario visual.
"""
from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("first_name", sa.String(length=50), nullable=True))
    op.add_column("leads", sa.Column("middle_name", sa.String(length=50), nullable=True))
    op.add_column("leads", sa.Column("last_name", sa.String(length=75), nullable=True))
    op.add_column("leads", sa.Column("curp", sa.String(length=18), nullable=True))
    op.add_column("leads", sa.Column("next_follow_up_type", sa.String(length=20), nullable=True))
    op.create_index("ix_leads_curp", "leads", ["curp"], unique=False)

    op.add_column("customers", sa.Column("curp", sa.String(length=18), nullable=True))
    op.create_index("ix_customers_curp", "customers", ["curp"], unique=False)

    op.add_column("interactions", sa.Column("next_follow_up_type", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interactions") as batch_op:
        batch_op.drop_column("next_follow_up_type")

    op.drop_index("ix_customers_curp", table_name="customers")
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_column("curp")

    op.drop_index("ix_leads_curp", table_name="leads")
    with op.batch_alter_table("leads") as batch_op:
        batch_op.drop_column("next_follow_up_type")
        batch_op.drop_column("curp")
        batch_op.drop_column("last_name")
        batch_op.drop_column("middle_name")
        batch_op.drop_column("first_name")
