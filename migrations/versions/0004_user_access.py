"""Require admission for new users; retain access for existing profiles."""
from alembic import op
import sqlalchemy as sa

revision = '0004_user_access'
down_revision = '0003_hot_path_indexes'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table = sa.Table('user_access', sa.MetaData(),
        sa.Column('telegram_id', sa.BigInteger, primary_key=True),
        sa.Column('username', sa.String),
        sa.Column('display_name', sa.String, nullable=False),
        sa.Column('language', sa.String(5), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('decided_at', sa.DateTime),
        sa.Column('decided_by', sa.BigInteger))
    table.create(bind, checkfirst=True)
    from migrations._util import create_index_if_missing
    create_index_if_missing('ix_user_access_status_created', 'user_access', ['status', 'created_at'])
    columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    username = "username" if "username" in columns else "NULL"
    name = "COALESCE(name, 'Existing user')" if "name" in columns else "'Existing user'"
    language = "COALESCE(language, 'en')" if "language" in columns else "'en'"
    bind.execute(sa.text(f"""
        INSERT INTO user_access (telegram_id, username, display_name, language, status, created_at, decided_at)
        SELECT telegram_id, {username}, {name}, {language}, 'approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM users WHERE NOT EXISTS (
            SELECT 1 FROM user_access WHERE user_access.telegram_id = users.telegram_id)
    """))


def downgrade():
    op.drop_table('user_access')
