"""Baseline: bring any pre-Alembic schema to the first versioned state.

Production databases were created by ``Base.metadata.create_all`` plus idempotent
``ALTER TABLE ... ADD COLUMN`` statements at startup. This revision performs the same
work exactly once, so it is safe for a fresh database, a legacy database without an
``alembic_version`` table, and a partially upgraded one.

RULE FOR FUTURE REVISIONS: later revisions must not rely on ``create_all`` (it would
already contain their columns on fresh databases). Use ``migrations/_util`` helpers,
which skip work that is already present.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-03
"""
import sqlalchemy as sa
from alembic import op

from src.database.models import Base

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

# Columns added after the first public schema; legacy databases may lack them.
LEGACY_COLUMNS = {
    "users": {
        "ux_preferences": "JSON NOT NULL DEFAULT '{}'",
        "timezone": "VARCHAR(50) NOT NULL DEFAULT 'UTC'",
        "current_streak": "INTEGER NOT NULL DEFAULT 0",
        "streak_freezes_left": "INTEGER NOT NULL DEFAULT 1",
        "last_freeze_used_at": "TIMESTAMP",
        "weekly_report_day": "INTEGER NOT NULL DEFAULT 6",
        "monthly_report_day": "INTEGER NOT NULL DEFAULT 1",
    },
    "food_logs": {"meal_type": "VARCHAR(20) NOT NULL DEFAULT 'food'"},
    "ai_request_queue": {
        "retry_count": "INTEGER NOT NULL DEFAULT 0",
        "next_retry_at": "TIMESTAMP",
        "last_error": "TEXT",
    },
}


def upgrade() -> None:
    bind = op.get_bind()
    # Creates only the tables that are missing; existing tables are left untouched.
    Base.metadata.create_all(bind, checkfirst=True)
    inspector = sa.inspect(bind)
    for table, definitions in LEGACY_COLUMNS.items():
        existing = {col["name"] for col in inspector.get_columns(table)}
        for column, definition in definitions.items():
            if column not in existing:
                # Identifiers and definitions come only from the static map above.
                op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def downgrade() -> None:
    raise NotImplementedError("The baseline revision cannot be downgraded; restore a backup instead.")
