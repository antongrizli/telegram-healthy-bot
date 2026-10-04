"""Add user blocked_at timestamp for lifecycle cleanup.

Revision ID: 0002_add_user_blocked_at
Revises: 0001_baseline
Create Date: 2026-10-04
"""
import sqlalchemy as sa
from alembic import op
from migrations._util import add_column_if_missing

revision = "0002_add_user_blocked_at"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "users",
        sa.Column("blocked_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "blocked_at")
