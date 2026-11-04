"""Training v2: canonical exercises extensions, workout sets, training profiles, and plans."""
import json
import sqlalchemy as sa
from alembic import op

from migrations._util import add_column_if_missing, create_index_if_missing

revision = '0010_training_v2'
down_revision = '0009_training_coach'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 1. canonical_exercises extensions
    if inspector.has_table('canonical_exercises'):
        add_column_if_missing('canonical_exercises', sa.Column('kind', sa.String(16), nullable=False, server_default='strength'))
        add_column_if_missing('canonical_exercises', sa.Column('muscle_primary', sa.String(24), nullable=True))
        add_column_if_missing('canonical_exercises', sa.Column('muscles_secondary', sa.JSON(), nullable=False, server_default='[]'))
        add_column_if_missing('canonical_exercises', sa.Column('pattern', sa.String(24), nullable=True))
        add_column_if_missing('canonical_exercises', sa.Column('weight_step', sa.Numeric(4, 2), nullable=False, server_default='2.5'))
        add_column_if_missing('canonical_exercises', sa.Column('min_weight', sa.Numeric(6, 2), nullable=True))
        add_column_if_missing('canonical_exercises', sa.Column('per_side', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        add_column_if_missing('canonical_exercises', sa.Column('machine_settings', sa.String(200), nullable=True))
        add_column_if_missing('canonical_exercises', sa.Column('catalog_key', sa.String(64), nullable=True))
        add_column_if_missing('canonical_exercises', sa.Column('is_favorite', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        add_column_if_missing('canonical_exercises', sa.Column('is_hidden', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        add_column_if_missing('canonical_exercises', sa.Column('updated_at', sa.DateTime(), nullable=True))

    # 2. training_plans
    if not inspector.has_table('training_plans'):
        op.create_table(
            'training_plans',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), nullable=False),
            sa.Column('local_date', sa.Date(), nullable=False),
            sa.Column('status', sa.String(20), nullable=False, server_default='proposed'),
            sa.Column('focus', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('constraints', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('items', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('rationale', sa.String(1000), nullable=False, server_default=''),
            sa.Column('generator', sa.String(20), nullable=False, server_default='rules'),
            sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    create_index_if_missing('ix_training_plans_user_id', 'training_plans', ['user_id'])
    create_index_if_missing('ix_training_plans_date', 'training_plans', ['local_date'])

    # 3. training_profiles
    if not inspector.has_table('training_profiles'):
        op.create_table(
            'training_profiles',
            sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('users.telegram_id', ondelete='CASCADE'), primary_key=True),
            sa.Column('goal', sa.String(20), nullable=False, server_default='hypertrophy'),
            sa.Column('experience', sa.String(20), nullable=False, server_default='intermediate'),
            sa.Column('days_per_week', sa.Integer(), nullable=False, server_default='3'),
            sa.Column('session_minutes', sa.Integer(), nullable=False, server_default='45'),
            sa.Column('split', sa.String(20), nullable=False, server_default='auto'),
            sa.Column('limitations', sa.JSON(), nullable=False, server_default='[]'),
            sa.Column('rest_timer', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('weight_unit', sa.String(10), nullable=False, server_default='kg'),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )

    # 4. workout_sessions extensions
    if inspector.has_table('workout_sessions'):
        add_column_if_missing('workout_sessions', sa.Column('status', sa.String(12), nullable=False, server_default='draft'))
        add_column_if_missing('workout_sessions', sa.Column('source', sa.String(12), nullable=False, server_default='text'))
        add_column_if_missing('workout_sessions', sa.Column('plan_id', sa.Integer(), nullable=True))
        add_column_if_missing('workout_sessions', sa.Column('started_at', sa.DateTime(), nullable=True))
        add_column_if_missing('workout_sessions', sa.Column('finished_at', sa.DateTime(), nullable=True))
        add_column_if_missing('workout_sessions', sa.Column('focus', sa.JSON(), nullable=False, server_default='[]'))
        add_column_if_missing('workout_sessions', sa.Column('feeling', sa.String(8), nullable=True))
        add_column_if_missing('workout_sessions', sa.Column('energy', sa.String(8), nullable=True))
        add_column_if_missing('workout_sessions', sa.Column('note', sa.Text(), nullable=True))
        add_column_if_missing('workout_sessions', sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
        add_column_if_missing('workout_sessions', sa.Column('anchor_message_id', sa.BigInteger(), nullable=True))

    # 5. workout_session_entries extensions
    if inspector.has_table('workout_session_entries'):
        add_column_if_missing('workout_session_entries', sa.Column('position', sa.Integer(), nullable=False, server_default='0'))
        add_column_if_missing('workout_session_entries', sa.Column('status', sa.String(10), nullable=False, server_default='done'))
        add_column_if_missing('workout_session_entries', sa.Column('note', sa.Text(), nullable=True))

    # 6. workout_sets
    if not inspector.has_table('workout_sets'):
        op.create_table(
            'workout_sets',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('entry_id', sa.Integer(), sa.ForeignKey('workout_session_entries.id', ondelete='CASCADE'), nullable=False),
            sa.Column('idx', sa.Integer(), nullable=False, server_default='1'),
            sa.Column('set_type', sa.String(8), nullable=False, server_default='working'),
            sa.Column('weight_kg', sa.Numeric(6, 2), nullable=True),
            sa.Column('added_weight_kg', sa.Numeric(6, 2), nullable=True),
            sa.Column('reps', sa.Integer(), nullable=True),
            sa.Column('duration_sec', sa.Integer(), nullable=True),
            sa.Column('distance_m', sa.Integer(), nullable=True),
            sa.Column('rpe', sa.Numeric(3, 1), nullable=True),
            sa.Column('target_weight_kg', sa.Numeric(6, 2), nullable=True),
            sa.Column('target_reps', sa.Integer(), nullable=True),
            sa.Column('target_duration_sec', sa.Integer(), nullable=True),
            sa.Column('completed', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
    create_index_if_missing('ix_workout_sets_entry_id', 'workout_sets', ['entry_id'])

    # 7. Data migration: workout_session_entries -> workout_sets
    conn = op.get_bind()
    try:
        entries = conn.execute(sa.text("SELECT id, sets, weight_kg, reps, duration_sec, distance_m, created_at FROM workout_session_entries")).fetchall()
        for entry in entries:
            entry_id = entry[0]
            num_sets = entry[1] or 1
            weight_kg = entry[2]
            reps = entry[3]
            duration_sec = entry[4]
            distance_m = entry[5]
            created_at = entry[6]
            # Check if sets already exist for this entry
            existing_sets = conn.execute(sa.text("SELECT COUNT(*) FROM workout_sets WHERE entry_id = :eid"), {"eid": entry_id}).scalar()
            if existing_sets == 0:
                for idx in range(1, num_sets + 1):
                    conn.execute(
                        sa.text(
                            "INSERT INTO workout_sets (entry_id, idx, set_type, weight_kg, reps, duration_sec, distance_m, completed, created_at) "
                            "VALUES (:entry_id, :idx, 'working', :weight, :reps, :duration, :dist, :completed, :created_at)"
                        ),
                        {
                            "entry_id": entry_id,
                            "idx": idx,
                            "weight": weight_kg,
                            "reps": reps,
                            "duration": duration_sec,
                            "dist": distance_m,
                            "completed": True,
                            "created_at": created_at,
                        }
                    )
    except Exception:
        pass

    # 8. Data migration: users.training_preferences -> training_profiles
    try:
        users = conn.execute(sa.text("SELECT telegram_id, training_preferences FROM users WHERE training_preferences IS NOT NULL")).fetchall()
        for u in users:
            uid = u[0]
            prefs = u[1]
            if isinstance(prefs, str):
                try:
                    prefs = json.loads(prefs)
                except Exception:
                    prefs = {}
            if isinstance(prefs, dict) and prefs:
                days = prefs.get('sessions_per_week') or 3
                mins = prefs.get('minutes') or 45
                goal_map = {
                    'muscle': 'hypertrophy',
                    'strength': 'strength',
                    'endurance': 'endurance',
                    'weight_loss': 'fat_loss',
                    'health': 'health',
                }
                raw_goal = prefs.get('primary') or prefs.get('goal') or 'hypertrophy'
                goal = goal_map.get(raw_goal, 'hypertrophy')
                exists = conn.execute(sa.text("SELECT user_id FROM training_profiles WHERE user_id = :uid"), {"uid": uid}).scalar()
                if not exists:
                    conn.execute(
                        sa.text(
                            "INSERT INTO training_profiles (user_id, goal, experience, days_per_week, session_minutes, split, limitations, rest_timer, weight_unit) "
                            "VALUES (:uid, :goal, 'intermediate', :days, :mins, 'auto', '[]', :rest_timer, 'kg')"
                        ),
                        {"uid": uid, "goal": goal, "days": days, "mins": mins, "rest_timer": False}
                    )
    except Exception:
        pass


def downgrade():
    op.drop_table('workout_sets')
    op.drop_table('training_profiles')
    op.drop_table('training_plans')
