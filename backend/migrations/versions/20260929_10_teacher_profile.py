"""Add teacher contact profile and last-login activity.

Revision ID: 20260929_10
Revises: 20260929_09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260929_10"
down_revision = "20260929_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teachers", sa.Column("full_name", sa.Text(), nullable=False, server_default=""))
    op.add_column("teachers", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column("teachers", sa.Column("last_login_at", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("teachers", "last_login_at")
    op.drop_column("teachers", "phone")
    op.drop_column("teachers", "full_name")
