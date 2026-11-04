import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.database import crud
from src.database.models import Exercise, Workout, WorkoutEntry, WorkoutPlan
from src.services import workouts as svc
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


async def prepared(db, uid=123):
    user = await make_user(db, uid)
    w = await svc.start(db, user)
    ex = await svc.add_exercise(db, uid, 'Жим ногами')
    e = await svc.open_entry(db, w, ex.id, user)
    await db.commit()
    return user, w, ex, e


async def save_three(db, w, e):
    for action, value in [('weight', '80'), ('reps', 10), ('add', None), ('add', None),
                          ('weight', '70'), ('reps', 8), ('confirm', None), ('save', None)]:
        await svc.mutate_entry(db, w, e.id, action, value)
    await db.commit()


async def test_durable_three_sets_duplicate_save_and_user_isolation(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    await save_three(db, w, e)
    assert [s['weight'] for s in e.actual] == [80, 80, 70]
    assert svc.metrics(e.actual)['confirmed_volume'] == 2160
    await svc.mutate_entry(db, w, e.id, 'save')
    await db.commit()
    assert len(await svc.entries(db, w)) == 1
    workout_id = w.id
    db.expire_all()  # Reload as after a new handler / restart.
    w = await svc.owned(db, Workout, workout_id, 123)
    e = (await svc.entries(db, w))[0]
    assert [s['weight'] for s in e.draft] == [80, 80, 70]
    with pytest.raises(svc.StaleWorkout):
        await svc.owned(db, WorkoutEntry, e.id, 456)


async def test_finish_requires_saving_draft_and_next_copies_actual_without_reps_confirmation(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    with pytest.raises(ValueError):
        await svc.finish(db, w)
    await save_three(db, w, e)
    await svc.mutate_entry(db, w, e.id, 'delta', -1)
    with pytest.raises(ValueError):
        await svc.finish(db, w)
    await svc.mutate_entry(db, w, e.id, 'cancel')
    await svc.finish(db, w)
    await db.commit()
    w2 = await svc.start(db, user)
    e2 = await svc.open_entry(db, w2, ex.id, user)
    assert [s['weight'] for s in e2.draft] == [80, 80, 70]
    assert not any(s['confirmed'] for s in e2.draft)
    assert svc.metrics(e2.draft)['volume_complete'] is False
    await svc.mutate_entry(db, w2, e2.id, 'weight', 65)
    assert e.actual[-1]['weight'] == 70


async def test_unknown_repetitions_never_become_zero(db_session):
    _, w, _, e = await prepared(db_session)
    await svc.mutate_entry(db_session, w, e.id, 'weight', '2,5')
    await svc.mutate_entry(db_session, w, e.id, 'save')
    assert e.actual == [{'weight': 2.5, 'reps': None, 'confirmed': False}]
    assert svc.metrics(e.actual) == dict(sets=1, max_weight=2.5, confirmed_sets=0,
                                       known_reps=0, confirmed_volume=0, volume_complete=False)


async def test_stale_revision_and_entry_from_other_workout_rejected(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    revision = w.revision
    await svc.bump(db, w, revision)
    await db.commit()
    with pytest.raises(svc.StaleWorkout):
        await svc.bump(db, w, revision)
    await db.refresh(w)
    await save_three(db, w, e)
    await svc.finish(db, w)
    await db.commit()
    w2 = await svc.start(db, user)
    with pytest.raises(svc.StaleWorkout):
        await svc.mutate_entry(db, w2, e.id, 'weight', 999)


async def test_only_accepted_plan_with_current_goals_is_used(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    await save_three(db, w, e)
    await svc.finish(db, w)
    await db.commit()
    plan = WorkoutPlan(user_id=123, workout_id=w.id, goals=svc.goals(user),
                       items=[{'exercise_id': ex.id, 'sets': [{'weight': 60, 'reps': 10, 'confirmed': False}]}])
    db.add(plan)
    await db.commit()
    w2 = await svc.start(db, user)
    e2 = await svc.open_entry(db, w2, ex.id, user)
    assert e2.draft[0]['weight'] == 80  # A proposal is not a template yet.
    await db.delete(e2)
    await db.commit()
    await svc.change_plan(db, plan, plan.revision, user, 'accept')
    e2 = await svc.open_entry(db, w2, ex.id, user)
    assert e2.draft[0]['weight'] == 60
    await db.delete(e2)
    await db.commit()
    user.additional_goals = ['gain_muscle']
    await db.commit()
    e2 = await svc.open_entry(db, w2, ex.id, user)
    assert e2.draft[0]['weight'] == 80


async def test_context_is_bounded_and_compares_only_same_exercise(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    await save_three(db, w, e)
    await svc.finish(db, w)
    w.finished_at = svc.now() - timedelta(days=3)
    await db.commit()
    w2 = await svc.start(db, user)
    e2 = await svc.open_entry(db, w2, ex.id, user)
    await svc.mutate_entry(db, w2, e2.id, 'weight', 60)
    await svc.mutate_entry(db, w2, e2.id, 'save')
    await svc.finish(db, w2)
    await db.commit()
    ctx = await svc.context(db, w2, user)
    item = ctx['exercises'][0]
    assert item['comparison']['volume_delta'] is None
    assert item['baseline'][0]['metrics']['sets'] == 3
    assert item['days_since_previous'] == 3
    assert 'telegram_id' not in json.dumps(ctx)
    assert len(item['baseline']) <= 3


async def test_failure_keeps_workout_and_can_retry_and_lease_blocks_duplicate(db_session, monkeypatch):
    from src.services import gemini
    db = db_session
    user, w, ex, e = await prepared(db)
    await save_three(db, w, e)
    await svc.finish(db, w)
    await db.commit()
    exercise_id = ex.id
    provider = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    with pytest.raises(TimeoutError):
        await svc.analyze(db, w, user, 'ru')
    await db.refresh(w)
    await db.refresh(user)
    assert w.status == 'finished' and w.analysis_state == 'failed'
    assert (await svc.entries(db, w))[0].actual
    w.analysis_state, w.analysis_started_at = 'running', svc.now()
    await db.commit()
    assert await svc.analyze(db, w, user, 'ru') is False
    assert provider.await_count == 1
    w.analysis_started_at = svc.now() - timedelta(minutes=5)
    await db.commit()
    provider.side_effect = None
    provider.return_value.text = json.dumps({'summary': 'Итог', 'explanation': 'Без повышения', 'items': [
        {'exercise_id': exercise_id, 'sets': [{'weight': 80, 'reps': 10}]}]})
    assert await svc.analyze(db, w, user, 'ru')
    assert provider.await_count == 2
    assert await svc.analyze(db, w, user, 'ru') is False
    assert provider.await_count == 2


async def test_pain_blocks_model_and_plan(db_session, monkeypatch):
    from src.services import gemini
    db = db_session
    user, w, _, e = await prepared(db)
    await save_three(db, w, e)
    await svc.finish(db, w)
    w.feedback = {'pain': True}
    await db.commit()
    provider = AsyncMock()
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    assert await svc.analyze(db, w, user, 'ru')
    provider.assert_not_called()
    plan = await db.scalar(select(WorkoutPlan).where(WorkoutPlan.workout_id == w.id))
    assert plan.status == 'blocked' and plan.items == []


async def test_deleting_profile_erases_workouts(db_session):
    user, w, ex, e = await prepared(db_session)
    await crud.delete_user(db_session, user.telegram_id)
    for model in (Workout, WorkoutEntry, WorkoutPlan, Exercise):
        assert await db_session.scalar(select(model)) is None


@pytest.mark.parametrize('bad', ['nan', 'inf', '-1', '1001', 'abc'])
async def test_invalid_weight_rejected(bad, db_session):
    _, w, _, e = await prepared(db_session)
    with pytest.raises(ValueError):
        await svc.mutate_entry(db_session, w, e.id, 'weight', bad)


async def test_plan_validation_rejects_foreign_ids_and_unjustified_progress():
    ctx = {'goals': {'primary': 'gain_muscle'}, 'feedback': {'wish': 'lighter'},
           'exercises': [{'exercise_id': 1, 'actual': [{'weight': 80, 'reps': 10}]}]}
    def proposal(ex, weight, reps=10):
        return svc.Analysis(summary='', explanation='', items=[
            svc.ProposedExercise(exercise_id=ex, sets=[svc.ProposedSet(weight=weight, reps=reps)])])
    for output in (proposal(2, 60), proposal(1, 80), proposal(1, 70, 20)):
        with pytest.raises(ValueError):
            svc.safe_plan(output, ctx)
    assert svc.safe_plan(proposal(1, 70), ctx)[0]['sets'][0]['weight'] == 70


async def test_newer_blocked_plan_never_reactivates_older_accepted_plan(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    await save_three(db, w, e)
    await svc.finish(db, w)
    db.add(WorkoutPlan(user_id=user.telegram_id, workout_id=w.id, goals=svc.goals(user), status='accepted',
                       items=[{'exercise_id': ex.id, 'sets': [{'weight': 60, 'reps': 10, 'confirmed': False}]}]))
    await db.commit()
    w2 = await svc.start(db, user)
    e2 = await svc.open_entry(db, w2, ex.id, user)
    await svc.mutate_entry(db, w2, e2.id, 'weight', 20)
    await svc.mutate_entry(db, w2, e2.id, 'save')
    await svc.finish(db, w2)
    db.add(WorkoutPlan(user_id=user.telegram_id, workout_id=w2.id, goals=svc.goals(user),
                       status='blocked', items=[]))
    await db.commit()
    w3 = await svc.start(db, user)
    e3 = await svc.open_entry(db, w3, ex.id, user)
    assert e3.draft[0]['weight'] == 20


async def test_upgrade_from_previous_revision_preserves_primary_goal(monkeypatch):
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.database import init_db
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    monkeypatch.setattr(init_db, 'engine', engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text('CREATE TABLE users (telegram_id BIGINT PRIMARY KEY, goal VARCHAR(20) NOT NULL)'))
            await conn.execute(sa.text("INSERT INTO users VALUES (123, 'gain_muscle')"))
            await conn.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
            await conn.execute(sa.text("INSERT INTO alembic_version VALUES ('0005_admission_settings')"))
        await init_db.init_db()
        await init_db.init_db()
        async with engine.connect() as conn:
            row = (await conn.execute(sa.text('SELECT goal, additional_goals FROM users'))).one()
            assert row == ('gain_muscle', '[]')
            assert (await conn.execute(sa.text('SELECT version_num FROM alembic_version'))).scalar() == '0010_training_v2'
            assert {'workouts', 'workout_entries', 'workout_plans', 'exercises'} <= set(
                await conn.run_sync(lambda c: sa.inspect(c).get_table_names()))
    finally:
        await engine.dispose()


async def test_pain_feedback_invalidates_old_accepted_template_even_without_new_analysis(db_session):
    db = db_session
    user, w, ex, e = await prepared(db)
    await save_three(db, w, e)
    await svc.finish(db, w)
    db.add(WorkoutPlan(user_id=user.telegram_id, workout_id=w.id, goals=svc.goals(user), status='accepted',
                       items=[{'exercise_id': ex.id, 'sets': [{'weight': 90, 'reps': 10, 'confirmed': False}]}]))
    w.feedback = {'pain': True}
    await db.commit()
    next_workout = await svc.start(db, user)
    next_entry = await svc.open_entry(db, next_workout, ex.id, user)
    assert next_entry.draft[0]['weight'] == 80
    assert await svc.previous_pain(db, next_workout)


async def test_cancel_discards_active_entries_and_cannot_delete_finished_workout(db_session):
    db = db_session
    user, workout, exercise, entry = await prepared(db)
    await save_three(db, workout, entry)
    workout_id = workout.id
    revision = workout.revision
    await svc.cancel_workout(db, workout, revision)
    assert await svc.current(db, user.telegram_id) is None
    assert await db.scalar(select(WorkoutEntry).where(WorkoutEntry.workout_id == workout_id)) is None
    await db.refresh(workout)
    assert workout.status == 'cancelled'
    with pytest.raises(svc.StaleWorkout):
        await svc.cancel_workout(db, workout, revision)

    finished = await svc.start(db, user)
    actual = await svc.open_entry(db, finished, exercise.id, user)
    await save_three(db, finished, actual)
    await svc.finish(db, finished)
    await db.commit()
    with pytest.raises(svc.StaleWorkout):
        await svc.cancel_workout(db, finished, finished.revision)
    assert (await svc.entries(db, finished))[0].actual is not None
