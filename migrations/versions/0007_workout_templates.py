"""Named mixed workout templates, cardio and profile training preferences."""
import sqlalchemy as sa
from alembic import op

from migrations._util import add_column_if_missing, create_index_if_missing

revision = '0007_workout_templates'
down_revision = '0006_workouts'
branch_labels = None
depends_on = None


def upgrade():
    add_column_if_missing('users', sa.Column('training_preferences', sa.JSON(), nullable=False, server_default='{}'))
    add_column_if_missing('exercises', sa.Column('kind', sa.String(12), nullable=False, server_default='strength'))
    add_column_if_missing('workout_plans', sa.Column('target_template', sa.JSON()))
    add_column_if_missing('workouts', sa.Column('template_snapshot', sa.JSON()))
    add_column_if_missing('workouts', sa.Column('available_minutes', sa.Integer()))
    if not sa.inspect(op.get_bind()).has_table('workout_templates'):
        op.create_table('workout_templates',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
            sa.Column('name', sa.String(80), nullable=False),
            sa.Column('revision', sa.Integer(), nullable=False),
            sa.Column('items', sa.JSON(), nullable=False),
            sa.Column('minutes_min', sa.Integer()), sa.Column('minutes_max', sa.Integer()),
            sa.UniqueConstraint('user_id', 'name', name='uq_workout_template_name'))
    create_index_if_missing('ix_workout_templates_user_id', 'workout_templates', ['user_id'])


def downgrade():
    op.drop_table('workout_templates')
    op.drop_column('workout_plans', 'target_template')
    op.drop_column('workouts', 'available_minutes')
    op.drop_column('workouts', 'template_snapshot')
    op.drop_column('exercises', 'kind')
    op.drop_column('users', 'training_preferences')
