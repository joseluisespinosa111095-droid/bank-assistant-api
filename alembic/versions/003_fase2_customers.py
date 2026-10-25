"""fase2_customers

Revision ID: 003
Revises: 002
Create Date: 2026-10-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_number', sa.String(length=24), nullable=False),
        sa.Column('entity_type', sa.String(length=20), server_default='INDIVIDUAL', nullable=False),
        sa.Column('status', sa.String(length=30), server_default='ACTIVE', nullable=False),
        sa.Column('verification_status', sa.String(length=30), server_default='PENDING_VERIFICATION', nullable=False),
        sa.Column('full_legal_name', sa.String(length=180), nullable=False),
        sa.Column('trade_name', sa.String(length=160), nullable=True),
        sa.Column('legal_representative_name', sa.String(length=160), nullable=True),
        sa.Column('tax_id_rfc', sa.String(length=20), nullable=True),
        sa.Column('date_of_birth', sa.Date(), nullable=True),
        sa.Column('phone_primary', sa.String(length=25), nullable=False),
        sa.Column('phone_primary_normalized', sa.String(length=20), nullable=False),
        sa.Column('phone_secondary', sa.String(length=25), nullable=True),
        sa.Column('phone_secondary_normalized', sa.String(length=20), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('address_full', sa.Text(), nullable=True),
        sa.Column('employment_status', sa.String(length=60), nullable=True),
        sa.Column('employer_or_business_name', sa.String(length=160), nullable=True),
        sa.Column('occupation_or_industry', sa.String(length=120), nullable=True),
        sa.Column('employment_address', sa.Text(), nullable=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('customer_since', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_customers_id'), 'customers', ['id'], unique=False)
    op.create_index(op.f('ix_customers_customer_number'), 'customers', ['customer_number'], unique=True)
    op.create_index(op.f('ix_customers_phone_primary_normalized'), 'customers', ['phone_primary_normalized'], unique=True)
    op.create_index(op.f('ix_customers_email'), 'customers', ['email'], unique=False)
    op.create_index(op.f('ix_customers_tax_id_rfc'), 'customers', ['tax_id_rfc'], unique=False)
    op.create_index(op.f('ix_customers_full_legal_name'), 'customers', ['full_legal_name'], unique=False)
    op.create_index(op.f('ix_customers_status'), 'customers', ['status'], unique=False)
    op.create_index(op.f('ix_customers_owner_user_id'), 'customers', ['owner_user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_customers_owner_user_id'), table_name='customers')
    op.drop_index(op.f('ix_customers_status'), table_name='customers')
    op.drop_index(op.f('ix_customers_full_legal_name'), table_name='customers')
    op.drop_index(op.f('ix_customers_tax_id_rfc'), table_name='customers')
    op.drop_index(op.f('ix_customers_email'), table_name='customers')
    op.drop_index(op.f('ix_customers_phone_primary_normalized'), table_name='customers')
    op.drop_index(op.f('ix_customers_customer_number'), table_name='customers')
    op.drop_index(op.f('ix_customers_id'), table_name='customers')
    op.drop_table('customers')
