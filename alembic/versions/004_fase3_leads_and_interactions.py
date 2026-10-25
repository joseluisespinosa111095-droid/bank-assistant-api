"""fase3_leads_stage_history_and_interactions

Revision ID: 004
Revises: 003
Create Date: 2026-10-25 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '004'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'leads',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_number', sa.String(length=24), nullable=False),
        sa.Column('full_name', sa.String(length=180), nullable=False),
        sa.Column('entity_type', sa.String(length=20), server_default='INDIVIDUAL', nullable=False),
        sa.Column('trade_name', sa.String(length=160), nullable=True),
        sa.Column('legal_representative_name', sa.String(length=160), nullable=True),
        sa.Column('tax_id_rfc', sa.String(length=20), nullable=True),
        sa.Column('phone_primary', sa.String(length=25), nullable=False),
        sa.Column('phone_primary_normalized', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('source', sa.String(length=40), server_default='DIRECT_OUTREACH', nullable=False),
        sa.Column('product_interest', sa.String(length=60), server_default='Personal Loan', nullable=False),
        sa.Column('requested_amount_cents', sa.BigInteger(), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='MXN', nullable=False),
        sa.Column('stage', sa.String(length=35), server_default='NEW', nullable=False),
        sa.Column('priority', sa.String(length=15), server_default='MEDIUM', nullable=False),
        sa.Column('lost_reason', sa.String(length=160), nullable=True),
        sa.Column('last_contacted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_interaction_outcome', sa.String(length=40), nullable=True),
        sa.Column('next_follow_up_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('summary_notes', sa.Text(), nullable=True),
        sa.Column('owner_user_id', sa.Integer(), nullable=False),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('converted_customer_id', sa.Integer(), nullable=True),
        sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('converted_by_user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('requested_amount_cents >= 0', name='ck_leads_amount_non_negative'),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['converted_customer_id'], ['customers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['converted_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_leads_id'), 'leads', ['id'], unique=False)
    op.create_index(op.f('ix_leads_lead_number'), 'leads', ['lead_number'], unique=True)
    op.create_index(op.f('ix_leads_full_name'), 'leads', ['full_name'], unique=False)
    op.create_index(op.f('ix_leads_phone_primary_normalized'), 'leads', ['phone_primary_normalized'], unique=False)
    op.create_index(op.f('ix_leads_email'), 'leads', ['email'], unique=False)
    op.create_index(op.f('ix_leads_stage'), 'leads', ['stage'], unique=False)
    op.create_index(op.f('ix_leads_next_follow_up_at'), 'leads', ['next_follow_up_at'], unique=False)
    op.create_index(op.f('ix_leads_owner_user_id'), 'leads', ['owner_user_id'], unique=False)
    op.create_index(op.f('ix_leads_converted_customer_id'), 'leads', ['converted_customer_id'], unique=False)

    op.create_table(
        'lead_stage_history',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('from_stage', sa.String(length=35), nullable=True),
        sa.Column('to_stage', sa.String(length=35), nullable=False),
        sa.Column('changed_by_user_id', sa.Integer(), nullable=False),
        sa.Column('reason_or_note', sa.Text(), nullable=True),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['changed_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lead_stage_history_id'), 'lead_stage_history', ['id'], unique=False)
    op.create_index(op.f('ix_lead_stage_history_lead_id'), 'lead_stage_history', ['lead_id'], unique=False)
    op.create_index(op.f('ix_lead_stage_history_changed_at'), 'lead_stage_history', ['changed_at'], unique=False)

    op.create_table(
        'interactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('interaction_type', sa.String(length=30), nullable=False),
        sa.Column('direction', sa.String(length=20), server_default='OUTBOUND', nullable=False),
        sa.Column('visibility', sa.String(length=20), server_default='INTERNAL', nullable=False),
        sa.Column('outcome', sa.String(length=40), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('next_follow_up_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.CheckConstraint('lead_id IS NOT NULL OR customer_id IS NOT NULL', name='ck_interactions_target_present'),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_interactions_id'), 'interactions', ['id'], unique=False)
    op.create_index(op.f('ix_interactions_lead_id'), 'interactions', ['lead_id'], unique=False)
    op.create_index(op.f('ix_interactions_customer_id'), 'interactions', ['customer_id'], unique=False)
    op.create_index(op.f('ix_interactions_created_at'), 'interactions', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_interactions_created_at'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_customer_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_lead_id'), table_name='interactions')
    op.drop_index(op.f('ix_interactions_id'), table_name='interactions')
    op.drop_table('interactions')

    op.drop_index(op.f('ix_lead_stage_history_changed_at'), table_name='lead_stage_history')
    op.drop_index(op.f('ix_lead_stage_history_lead_id'), table_name='lead_stage_history')
    op.drop_index(op.f('ix_lead_stage_history_id'), table_name='lead_stage_history')
    op.drop_table('lead_stage_history')

    op.drop_index(op.f('ix_leads_converted_customer_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_owner_user_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_next_follow_up_at'), table_name='leads')
    op.drop_index(op.f('ix_leads_stage'), table_name='leads')
    op.drop_index(op.f('ix_leads_email'), table_name='leads')
    op.drop_index(op.f('ix_leads_phone_primary_normalized'), table_name='leads')
    op.drop_index(op.f('ix_leads_full_name'), table_name='leads')
    op.drop_index(op.f('ix_leads_lead_number'), table_name='leads')
    op.drop_index(op.f('ix_leads_id'), table_name='leads')
    op.drop_table('leads')
