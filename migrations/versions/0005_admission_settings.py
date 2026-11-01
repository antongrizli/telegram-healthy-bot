"""Persist opt-in automatic admission."""
from alembic import op
import sqlalchemy as sa

revision = '0005_admission_settings'
down_revision = '0004_user_access'
branch_labels = None
depends_on = None


def upgrade():
    sa.Table('admission_settings', sa.MetaData(),
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('auto_approve', sa.Boolean, nullable=False, server_default=sa.false())
    ).create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('admission_settings')
