"""Owned daily workout proposals and explicit bodyweight exercise type."""
import sqlalchemy as sa
from alembic import op

from migrations._util import add_column_if_missing, create_index_if_missing

revision = '0008_daily_workout_plans'
down_revision = '0007_workout_templates'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('exercises', sa.Column('load_type', sa.String(12), nullable=False, server_default='external'))
    if not sa.inspect(op.get_bind()).has_table('daily_workout_plans'):
        op.create_table('daily_workout_plans',
            sa.Column('id', sa.Integer, primary_key=True),
            sa.Column('user_id', sa.BigInteger, sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
            sa.Column('local_date', sa.Date, nullable=False),
            sa.Column('revision', sa.Integer, nullable=False),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('preferences', sa.JSON, nullable=False), sa.Column('goals', sa.JSON, nullable=False),
            sa.Column('items', sa.JSON, nullable=False), sa.Column('summary', sa.String(3000), nullable=False),
            sa.Column('generation_started_at', sa.DateTime),
            sa.UniqueConstraint('user_id', 'local_date', name='uq_daily_workout_owner_date'))
    create_index_if_missing('ix_daily_workout_plans_user_id', 'daily_workout_plans', ['user_id'])


def downgrade():
    op.drop_table('daily_workout_plans')
    op.drop_column('exercises', 'load_type')
