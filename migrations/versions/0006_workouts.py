"""Durable workout drafts, actuals, proposals and additional profile goals."""
import sqlalchemy as sa
from alembic import op

from migrations._util import add_column_if_missing, has_index

revision = '0006_workouts'
down_revision = '0005_admission_settings'
branch_labels = None
depends_on = None



def create_table(name, *columns):
    if not sa.inspect(op.get_bind()).has_table(name):
        op.create_table(name, *columns)


def create_index(name, table, columns, **kwargs):
    if not has_index(table, name):
        op.create_index(name, table, columns, **kwargs)


def upgrade():
    add_column_if_missing('users', sa.Column('additional_goals', sa.JSON(), nullable=False, server_default='[]'))
    create_table('exercises',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('weight_step', sa.Float(), nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_exercise_owner_name'))
    create_index('ix_exercises_user_id', 'exercises', ['user_id'])
    create_table('workouts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime()),
        sa.Column('pending', sa.JSON(), nullable=False),
        sa.Column('feedback', sa.JSON(), nullable=False),
        sa.Column('goals', sa.JSON(), nullable=False),
        sa.Column('analysis_state', sa.String(20), nullable=False),
        sa.Column('analysis_started_at', sa.DateTime()),
        sa.Column('analysis', sa.JSON()))
    create_index('ix_workouts_user_id', 'workouts', ['user_id'])
    create_index('uq_workout_active_owner', 'workouts', ['user_id'], unique=True,
                    postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    create_table('workout_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
        sa.Column('workout_id', sa.Integer(), sa.ForeignKey('workouts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('exercise_id', sa.Integer(), sa.ForeignKey('exercises.id', ondelete='CASCADE'), nullable=False),
        sa.Column('draft', sa.JSON(), nullable=False), sa.Column('actual', sa.JSON()),
        sa.Column('selected', sa.Integer(), nullable=False),
        sa.UniqueConstraint('workout_id', 'exercise_id', name='uq_workout_exercise'))
    create_index('ix_workout_entries_user_id', 'workout_entries', ['user_id'])
    create_index('ix_workout_entries_workout_id', 'workout_entries', ['workout_id'])
    create_table('workout_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
        sa.Column('workout_id', sa.Integer(), sa.ForeignKey('workouts.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('goals', sa.JSON(), nullable=False), sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('explanation', sa.String(2000), nullable=False))
    create_index('ix_workout_plans_user_id', 'workout_plans', ['user_id'])


def downgrade():
    for table in ('workout_plans', 'workout_entries', 'workouts', 'exercises'):
        op.drop_table(table)
    op.drop_column('users', 'additional_goals')
