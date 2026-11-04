"""Training coach tables: canonical exercises, aliases, sessions, and session entries."""
import sqlalchemy as sa
from alembic import op

from migrations._util import create_index_if_missing

revision = '0009_training_coach'
down_revision = '0008_daily_workout_plans'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('canonical_exercises'):
        op.create_table(
            'canonical_exercises',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
            sa.Column('canonical_slug', sa.String(100), nullable=False),
            sa.Column('display_name', sa.String(150), nullable=False),
            sa.Column('exercise_type', sa.String(20), nullable=False, server_default='strength'),
            sa.Column('muscle_group', sa.String(50), nullable=True),
            sa.Column('equipment', sa.String(50), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint('user_id', 'canonical_slug', name='uq_canonical_exercise_owner_slug'),
        )
    create_index_if_missing('ix_canonical_exercises_user_id', 'canonical_exercises', ['user_id'])

    if not inspector.has_table('exercise_aliases'):
        op.create_table(
            'exercise_aliases',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
            sa.Column('alias_normalized', sa.String(150), nullable=False),
            sa.Column('canonical_exercise_id', sa.Integer(), sa.ForeignKey('canonical_exercises.id', ondelete='CASCADE'), nullable=False),
            sa.UniqueConstraint('user_id', 'alias_normalized', name='uq_exercise_alias_owner'),
        )
    create_index_if_missing('ix_exercise_aliases_user_id', 'exercise_aliases', ['user_id'])
    create_index_if_missing('ix_exercise_aliases_canonical_id', 'exercise_aliases', ['canonical_exercise_id'])

    if not inspector.has_table('workout_sessions'):
        op.create_table(
            'workout_sessions',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
            sa.Column('performed_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column('ai_summary', sa.Text(), nullable=True),
        )
    create_index_if_missing('ix_workout_sessions_user_id', 'workout_sessions', ['user_id'])

    if not inspector.has_table('workout_session_entries'):
        op.create_table(
            'workout_session_entries',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('workout_sessions.id', ondelete='CASCADE'), nullable=False),
            sa.Column('canonical_exercise_id', sa.Integer(), sa.ForeignKey('canonical_exercises.id', ondelete='CASCADE'), nullable=False),
            sa.Column('raw_input', sa.Text(), nullable=False),
            sa.Column('sets', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('reps', sa.Integer(), nullable=True),
            sa.Column('weight_kg', sa.Numeric(6, 2), nullable=True),
            sa.Column('duration_sec', sa.Integer(), nullable=True),
            sa.Column('distance_m', sa.Integer(), nullable=True),
            sa.Column('feeling', sa.String(16), nullable=False),
            sa.Column('next_recommended_load', sa.String(100), nullable=True),
            sa.Column('ai_note', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    create_index_if_missing('ix_workout_session_entries_session_id', 'workout_session_entries', ['session_id'])
    create_index_if_missing('ix_workout_session_entries_canonical_id', 'workout_session_entries', ['canonical_exercise_id'])


def downgrade():
    op.drop_table('workout_session_entries')
    op.drop_table('workout_sessions')
    op.drop_table('exercise_aliases')
    op.drop_table('canonical_exercises')
