"""Idempotent helpers for revisions after the baseline."""
import sqlalchemy as sa
from alembic import op


def has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {col["name"] for col in inspector.get_columns(table)}


def add_column_if_missing(table: str, column: sa.Column) -> None:
    if not has_column(table, column.name):
        op.add_column(table, column)


def has_index(table: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return index_name in {idx["name"] for idx in inspector.get_indexes(table)}


def create_index_if_missing(index_name: str, table: str, columns: list[str]) -> None:
    if not has_index(table, index_name):
        op.create_index(index_name, table, columns)
