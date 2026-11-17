"""customer name parts (first / middle / last name)

Revision ID: 011
Revises: 010
Create Date: 2026-11-16

Agrega nombre, segundo nombre y apellidos a customers. Son nullable: las personas morales
no los usan y los clientes anteriores al cambio quedan sin partes (full_legal_name sigue siendo
la fuente para mostrar, buscar, imprimir y exportar).
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("customers", sa.Column("first_name", sa.String(length=50), nullable=True))
    op.add_column("customers", sa.Column("middle_name", sa.String(length=50), nullable=True))
    op.add_column("customers", sa.Column("last_name", sa.String(length=75), nullable=True))


def downgrade():
    with op.batch_alter_table("customers") as batch_op:
        batch_op.drop_column("last_name")
        batch_op.drop_column("middle_name")
        batch_op.drop_column("first_name")
