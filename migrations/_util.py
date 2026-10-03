"""Idempotent helpers for revisions after the baseline."""
import sqlalchemy as sa
from alembic import op


def has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {col["name"] for col in inspector.get_columns(table)}


def add_column_if_missing(table: str, column: sa.Column) -> None:
    if not has_column(table, column.name):
        op.add_column(table, column)
