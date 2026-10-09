"""Add composite indexes for hot-path lookups.

Revision ID: 0003_hot_path_indexes
Revises: 0002_add_user_blocked_at
Create Date: 2026-10-09
"""
from alembic import op
from migrations._util import create_index_if_missing, has_index

revision = "0003_hot_path_indexes"
down_revision = "0002_add_user_blocked_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    create_index_if_missing("ix_food_logs_user_logged", "food_logs", ["user_id", "logged_at"])
    create_index_if_missing("ix_weight_logs_user_logged", "weight_logs", ["user_id", "logged_at"])
    create_index_if_missing("ix_water_logs_user_logged", "water_logs", ["user_id", "logged_at"])
    create_index_if_missing("ix_queue_status_retry", "ai_request_queue", ["status", "next_retry_at"])


def downgrade() -> None:
    if has_index("ai_request_queue", "ix_queue_status_retry"):
        op.drop_index("ix_queue_status_retry", table_name="ai_request_queue")
    if has_index("water_logs", "ix_water_logs_user_logged"):
        op.drop_index("ix_water_logs_user_logged", table_name="water_logs")
    if has_index("weight_logs", "ix_weight_logs_user_logged"):
        op.drop_index("ix_weight_logs_user_logged", table_name="weight_logs")
    if has_index("food_logs", "ix_food_logs_user_logged"):
        op.drop_index("ix_food_logs_user_logged", table_name="food_logs")
