"""auth_security_rate_limits_and_password_reset

Revision ID: 008
Revises: 007
Create Date: 2026-11-09 18:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Persistent atomic rate limiting table
    op.create_table(
        'rate_limits',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bucket_key', sa.String(length=140), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('window_started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('window_expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_rate_limits'),
        sa.UniqueConstraint('bucket_key', name='uq_rate_limits_bucket_key'),
        sa.CheckConstraint(
            'length(trim(bucket_key)) >= 4',
            name='ck_rate_limits_bucket_key_non_empty'
        ),
        sa.CheckConstraint(
            'attempt_count >= 1',
            name='ck_rate_limits_attempts_positive'
        ),
        sa.CheckConstraint(
            'window_expires_at > window_started_at',
            name='ck_rate_limits_window_valid'
        )
    )
    op.create_index(
        'ix_rate_limits_window_expires_at',
        'rate_limits',
        ['window_expires_at'],
        unique=False
    )

    # 2. Single-use hashed password reset tokens table
    # Note: Prior unconsumed tokens (including expired ones with used_at IS NULL)
    # are updated with used_at = :now_utc before inserting a new token,
    # keeping uq_password_reset_tokens_active_user conflict-free.
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('request_ip', sa.String(length=45), nullable=True),
        sa.PrimaryKeyConstraint('id', name='pk_password_reset_tokens'),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            ondelete='CASCADE',
            name='fk_password_reset_tokens_user_id_users'
        ),
        sa.UniqueConstraint('token_hash', name='uq_password_reset_tokens_token_hash'),
        sa.CheckConstraint(
            'length(token_hash) = 64',
            name='ck_password_reset_tokens_hash_len'
        ),
        sa.CheckConstraint(
            'expires_at > created_at',
            name='ck_password_reset_tokens_expiry_valid'
        )
    )
    op.create_index(
        'ix_password_reset_tokens_user_id',
        'password_reset_tokens',
        ['user_id'],
        unique=False
    )
    op.create_index(
        'ix_password_reset_tokens_expires_at',
        'password_reset_tokens',
        ['expires_at'],
        unique=False
    )
    op.create_index(
        'uq_password_reset_tokens_active_user',
        'password_reset_tokens',
        ['user_id'],
        unique=True,
        postgresql_where=sa.text('used_at IS NULL'),
        sqlite_where=sa.text('used_at IS NULL')
    )


def downgrade() -> None:
    op.drop_index('uq_password_reset_tokens_active_user', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_expires_at', table_name='password_reset_tokens')
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')

    op.drop_index('ix_rate_limits_window_expires_at', table_name='rate_limits')
    op.drop_table('rate_limits')
