"""payment rebate detail column + unique posted payment reference

Revision ID: 010
Revises: 009
Create Date: 2026-11-16

- loan_payments.rebate_detail_json: detalle del rebate por pronto pago (fuente de verdad para la reversa).
- uq_loan_payments_posted_reference: impide dos pagos POSTED con la misma referencia
  (mismo metodo SPEI/DEPOSITO/CHEQUE, comparando upper(trim(referencia))), incluso con requests simultaneos.
"""
from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None

INDEX_NAME = "uq_loan_payments_posted_reference"
INDEX_PREDICATE = (
    "status = 'POSTED' "
    "AND payment_method IN ('SPEI_TRANSFER', 'BANK_DEPOSIT', 'CHECK') "
    "AND payment_reference IS NOT NULL "
    "AND length(trim(payment_reference)) > 0"
)


def upgrade():
    bind = op.get_bind()

    # Si ya existieran referencias duplicadas, el indice unico fallaria con un error poco claro.
    # Se revisa antes y se aborta con un mensaje explicito (corregir los datos y reintentar).
    duplicates = bind.execute(sa.text(
        "SELECT payment_method, upper(trim(payment_reference)) AS ref, count(*) AS n "
        "FROM loan_payments "
        "WHERE " + INDEX_PREDICATE + " "
        "GROUP BY payment_method, upper(trim(payment_reference)) "
        "HAVING count(*) > 1"
    )).fetchall()
    if duplicates:
        detail = "; ".join(f"{row[0]} / {row[1]} x{row[2]}" for row in duplicates[:10])
        raise RuntimeError(
            "No se puede crear el indice unico: hay referencias de pago duplicadas entre pagos POSTED "
            f"({detail}). Revisalas y vuelve a ejecutar la migracion."
        )

    op.add_column("loan_payments", sa.Column("rebate_detail_json", sa.Text(), nullable=True))

    op.create_index(
        INDEX_NAME,
        "loan_payments",
        ["payment_method", sa.text("upper(trim(payment_reference))")],
        unique=True,
        postgresql_where=sa.text(INDEX_PREDICATE),
        sqlite_where=sa.text(INDEX_PREDICATE),
    )


def downgrade():
    op.drop_index(INDEX_NAME, table_name="loan_payments")
    op.drop_column("loan_payments", "rebate_detail_json")
