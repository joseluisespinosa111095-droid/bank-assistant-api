"""add_user_preferred_language

Revision ID: 009
Revises: 008
Create Date: 2026-11-11 21:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '009'
down_revision = '008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "postgresql"

    if dialect_name == "sqlite":
        # SQLite does not support ALTER TABLE ADD CONSTRAINT separately;
        # inline CheckConstraint on ADD COLUMN is natively supported in SQLite without table recreation.
        op.add_column(
            'users',
            sa.Column(
                'preferred_language',
                sa.String(length=10),
                sa.CheckConstraint(
                    "preferred_language IN ('en', 'es')",
                    name='ck_users_preferred_language_valid'
                ),
                server_default=sa.text("'en'"),
                nullable=False
            )
        )
    else:
        op.add_column(
            'users',
            sa.Column(
                'preferred_language',
                sa.String(length=10),
                server_default=sa.text("'en'"),
                nullable=False
            )
        )
        op.create_check_constraint(
            'ck_users_preferred_language_valid',
            'users',
            "preferred_language IN ('en', 'es')"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "postgresql"

    if dialect_name == "sqlite":
        with op.batch_alter_table('users') as batch_op:
            batch_op.drop_column('preferred_language')
    else:
        op.drop_constraint('ck_users_preferred_language_valid', 'users', type_='check')
        op.drop_column('users', 'preferred_language')
