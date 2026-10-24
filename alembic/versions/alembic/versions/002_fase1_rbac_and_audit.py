"""fase1_rbac_and_audit

Revision ID: 002
Revises: 001
Create Date: 2026-10-24 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Agregar columnas a users con server_default compatible con datos existentes
    op.add_column(
        'users',
        sa.Column('full_name', sa.String(length=120), nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('role', sa.String(length=30), server_default='SALES_AGENT', nullable=False)
    )
    op.add_column(
        'users',
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False)
    )
    op.create_index(op.f('ix_users_role'), 'users', ['role'], unique=False)
    op.create_index(op.f('ix_users_is_active'), 'users', ['is_active'], unique=False)

    # 2. Crear tabla audit_logs (BigInteger en Postgres / Integer autoincremental en SQLite)
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('actor_role', sa.String(length=30), nullable=True),
        sa.Column('event_type', sa.String(length=60), nullable=False),
        sa.Column('resource_type', sa.String(length=40), nullable=False),
        sa.Column('resource_id', sa.String(length=60), nullable=True),
        sa.Column('outcome', sa.String(length=20), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_event_type'), 'audit_logs', ['event_type'], unique=False)
    op.create_index(op.f('ix_audit_logs_actor_user_id'), 'audit_logs', ['actor_user_id'], unique=False)

    # 3. Triggers de protección contra UPDATE, DELETE y TRUNCATE ordinarios en PostgreSQL
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("""
            CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()
            RETURNS TRIGGER AS $$
            BEGIN
                RAISE EXCEPTION 'audit_logs is append-only. UPDATE, DELETE, and TRUNCATE are forbidden.';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_protect_audit_logs_row
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW EXECUTE FUNCTION prevent_audit_log_mutation();

            CREATE TRIGGER trg_protect_audit_logs_stmt
            BEFORE TRUNCATE ON audit_logs
            FOR EACH STATEMENT EXECUTE FUNCTION prevent_audit_log_mutation();
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("DROP TRIGGER IF EXISTS trg_protect_audit_logs_stmt ON audit_logs;")
        op.execute("DROP TRIGGER IF EXISTS trg_protect_audit_logs_row ON audit_logs;")
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_log_mutation();")

    op.drop_index(op.f('ix_audit_logs_actor_user_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_event_type'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_table('audit_logs')

    op.drop_index(op.f('ix_users_is_active'), table_name='users')
    op.drop_index(op.f('ix_users_role'), table_name='users')
    op.drop_column('users', 'is_active')
    op.drop_column('users', 'role')
    op.drop_column('users', 'full_name')
