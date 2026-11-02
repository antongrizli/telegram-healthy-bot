import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from src.database import crud
from src.database.models import Exercise, Workout, WorkoutEntry, WorkoutPlan, WorkoutTemplate
from src.handlers import workouts as handler
from src.services import workout_templates as wt
from src.services import workouts as svc
from src.utils.workout_locales import LANGS, tr
from tests.integration.test_security_and_meal_recovery import make_user
from tests.integration.test_workout_cards import callback, message

pytestmark = pytest.mark.asyncio


async def sample(db, uid=123):
    user = await make_user(db, uid)
    await wt.import_example(db, uid)
    await db.commit()
    a, b = await wt.templates(db, uid)
    return user, a, b


async def record(db, user, template):
    workout = await svc.start(db, user, template.id)
    for item in template.items:
        entry = await svc.open_entry(db, workout, item['exercise_id'], user)
        if item['kind'] == 'cardio':
            await svc.mutate_entry(db, workout, entry.id, 'minutes', 12)
        else:
            for index in range(len(entry.draft)):
                await svc.mutate_entry(db, workout, entry.id, 'select', index)
                if entry.draft[index]['weight'] is None:
                    await svc.mutate_entry(db, workout, entry.id, 'weight', entry.draft[index]['weight_min'])
        await svc.mutate_entry(db, workout, entry.id, 'save')
    await svc.finish(db, workout)
    await db.commit()
    return workout


async def test_opt_in_import_is_idempotent_owned_and_not_history(db_session):
    db = db_session
    user, a, b = await sample(db)
    before = copy.deepcopy(a.items)
    await wt.import_example(db, user.telegram_id)
    await db.commit()
    assert len(await wt.templates(db, user.telegram_id)) == 2
    assert (await db.scalar(select(func.count()).select_from(Exercise))) == 9
    assert (await db.scalar(select(func.count()).select_from(Workout))) == 0
    assert (await db.scalar(select(func.count()).select_from(WorkoutEntry))) == 0
    assert a.items == before and user.training_preferences == {}
    assert await wt.templates(db, 456) == []
    assert a.minutes_min == 40 and a.minutes_max == 50
    latzug = a.items[2]['sets'][0]
    assert latzug == dict(weight=None, weight_min=30, weight_max=35, reps=12, confirmed=False)
    assert a.items[0]['sets'][0]['minutes'] is None
    assert [item['exercise_id'] for item in b.items] != [item['exercise_id'] for item in a.items]


async def test_drafts_do_not_advance_cycle_completed_workout_does(db_session):
    db = db_session
    user, a, b = await sample(db)
    assert (await wt.recommended(db, user.telegram_id)).id == a.id
    w = await svc.start(db, user, a.id)
    await svc.open_entry(db, w, a.items[0]['exercise_id'], user)
    await db.commit()
    assert (await wt.recommended(db, user.telegram_id)).id == a.id
    with pytest.raises(svc.StaleWorkout):
        await svc.start(db, user, b.id)
    await record(db, user, a)
    assert (await wt.recommended(db, user.telegram_id)).id == b.id
    await record(db, user, b)
    assert (await wt.recommended(db, user.telegram_id)).id == a.id


async def test_cardio_and_weight_ranges_require_concrete_actuals_and_keep_types_separate(db_session):
    db = db_session
    user, a, _ = await sample(db)
    w = await svc.start(db, user, a.id)
    cardio = await svc.open_entry(db, w, a.items[0]['exercise_id'], user)
    with pytest.raises(ValueError):
        await svc.mutate_entry(db, w, cardio.id, 'save')
    with pytest.raises(ValueError):
        await svc.mutate_entry(db, w, cardio.id, 'weight', 30)
    await svc.mutate_entry(db, w, cardio.id, 'minutes', '12,5')
    await svc.mutate_entry(db, w, cardio.id, 'intensity', 'moderate')
    await svc.mutate_entry(db, w, cardio.id, 'save')
    assert svc.metrics(cardio.actual) == {'kind': 'cardio', 'minutes': 12.5, 'intensity': 'moderate'}
    assert cardio.actual == cardio.draft
    lift = await svc.open_entry(db, w, a.items[2]['exercise_id'], user)
    with pytest.raises(ValueError):
        await svc.mutate_entry(db, w, lift.id, 'save')
    for i in (0, 1):
        await svc.mutate_entry(db, w, lift.id, 'select', i)
        await svc.mutate_entry(db, w, lift.id, 'weight', 32.5)
    await svc.mutate_entry(db, w, lift.id, 'save')
    assert all(s['weight'] == 32.5 and not s['confirmed'] for s in lift.actual)
    await svc.finish(db, w)
    await db.commit()
    text = await handler.numeric_summary(db, w, 'ru')
    assert '12.5 мин' in text and 'Latzug' in text
    assert 'Ellipsentrainer' in text
    assert 'confirmed_volume' not in svc.metrics(cardio.actual)


async def test_mixed_context_next_template_timing_and_safe_model_response(db_session):
    db = db_session
    user, a, b = await sample(db)
    user.training_preferences = {'primary': 'maintain_strength', 'additional': ['endurance', 'avoid_fatigue'], 'minutes': 40}
    await db.commit()
    w = await record(db, user, a)
    w.feedback = {'wish': 'shorter', 'effort': 'normal'}
    await db.commit()
    ctx = await svc.context(db, w, user)
    assert ctx['next_template']['id'] == b.id
    assert ctx['next_time_budget'] < 40
    assert ctx['goals']['training']['primary'] == 'maintain_strength'
    basis = ctx['planning_basis']
    assert wt.estimate_minutes([{'kind': i['kind'], 'sets': i['actual']} for i in basis]) <= ctx['next_time_budget']
    output = svc.Analysis(summary='Итог', explanation='Короткий план B', items=[
        svc.ProposedExercise(exercise_id=i['exercise_id'], sets=[
            svc.ProposedSet(minutes=s.get('minutes'), intensity=s.get('intensity')) if i['kind'] == 'cardio'
            else svc.ProposedSet(weight=s['weight'], reps=s['reps']) for s in i['actual']]) for i in basis])
    plan = svc.safe_plan(output, ctx)
    assert wt.estimate_minutes(plan) <= ctx['next_time_budget']
    assert {i['exercise_id'] for i in plan} <= {i['exercise_id'] for i in b.items}
    # The new machine keeps its unknown range rather than receiving an invented working weight.
    unknown = next(i for i in plan if i['sets'][0].get('weight_min') is not None)
    assert unknown['sets'][0]['weight'] is None
    invalid = output.model_copy(deep=True)
    for i in invalid.items:
        if i.exercise_id == unknown['exercise_id']:
            i.sets[0].weight = 32.5
    with pytest.raises(ValueError):
        svc.safe_plan(invalid, ctx)


async def test_cardio_plan_rejects_increases_and_lighter_plan_reduces_time(db_session):
    db = db_session
    user, a, _ = await sample(db)
    w = await record(db, user, a)
    cardio_id = a.items[0]['exercise_id']
    ctx = {'goals': svc.goals(user), 'feedback': {}, 'exercises': [
        {'exercise_id': cardio_id, 'kind': 'cardio', 'actual': [{'kind': 'cardio', 'minutes': 12, 'intensity': 'easy'}]}]}
    for minutes, intensity in [(13, 'easy'), (12, 'hard')]:
        output = svc.Analysis(summary='', explanation='', items=[svc.ProposedExercise(exercise_id=cardio_id,
                            sets=[svc.ProposedSet(minutes=minutes, intensity=intensity)])])
        with pytest.raises(ValueError):
            svc.safe_plan(output, ctx)
    plan = WorkoutPlan(user_id=user.telegram_id, workout_id=w.id, goals=svc.goals(user),
                       items=[{'exercise_id': cardio_id, 'kind': 'cardio',
                               'sets': [{'kind': 'cardio', 'minutes': 12, 'intensity': 'easy'}]}])
    db.add(plan)
    await db.commit()
    await svc.change_plan(db, plan, plan.revision, user, 'lighter')
    assert plan.items[0]['sets'][0]['minutes'] == 10.8


async def test_template_editor_keeps_ranges_and_never_records_performance(db_session):
    db = db_session
    user, a, _ = await sample(db)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    await handler.template_action(callback(f'wot:edit:{a.id}', msg), state, 'ru', user)
    w = await svc.current(db, user.telegram_id)
    assert w.feedback['template_edit']
    for e in await svc.entries(db, w):
        await svc.mutate_entry(db, w, e.id, 'save')
    with pytest.raises(ValueError):
        await svc.finish(db, w)
    snapshot_before = copy.deepcopy(a.items)
    cardio = (await svc.entries(db, w))[0]
    await svc.mutate_entry(db, w, cardio.id, 'minutes', '8–12')
    await svc.mutate_entry(db, w, cardio.id, 'save')
    w.pending = {'mode': 'template_name'}
    await db.commit()
    await handler.text_input(message(text='A'), w.id, 'ru')
    assert w.status == 'template'
    assert a.items != snapshot_before
    assert a.items[0]['sets'][0]['minutes_min'] == 8
    assert a.revision == 1
    assert (await wt.recommended(db, user.telegram_id)).id == a.id
    assert (await db.scalar(select(func.count()).select_from(Workout).where(Workout.status == 'finished'))) == 0


async def test_training_priorities_and_durable_budget_do_not_change_nutrition(db_session):
    db = db_session
    user = await make_user(db)
    original = (user.goal, user.target_calories, user.target_protein, user.target_fat, user.target_carb)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    for data in ['wotr:primary:maintain_strength', 'wotr:extra:endurance:add', 'wotr:extra:avoid_fatigue:add', 'wotr:time']:
        await handler.training_profile(callback(data, msg), state, 'ru')
    assert await handler.PendingInput().__call__(message(text='45')) == {'workout_input_id': 0}
    await handler.text_input(message(text='45'), 0, 'ru')
    assert user.training_preferences == {'primary': 'maintain_strength', 'additional': ['endurance', 'avoid_fatigue'], 'minutes': 45}
    assert (user.goal, user.target_calories, user.target_protein, user.target_fat, user.target_carb) == original
    assert not user.ux_preferences['workout_budget_input']
    with pytest.raises(ValueError):
        handler.parse_budget('4')
    assert handler.parse_budget('0') is None


@pytest.mark.parametrize('lang', LANGS)
async def test_cardio_and_template_screens_all_languages_and_short_callbacks(db_session, lang):
    db = db_session
    user, a, _ = await sample(db)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    await handler.choose_workout(callback('woh:choose', msg), state, lang, user)
    assert 'A' in msg.edit_text.call_args.kwargs['text']
    w = await svc.start(db, user, a.id)
    e = await svc.open_entry(db, w, a.items[0]['exercise_id'], user)
    await handler.card(db, msg, w, e, lang, edit=True)
    result = msg.edit_text.call_args.kwargs
    assert tr('minute_unit', lang) in result['text']
    for row in result['reply_markup'].inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64
    data = next(b.callback_data for row in result['reply_markup'].inline_keyboard for b in row
                if b.callback_data.split(':')[3] == 'minutes')
    await handler.action(callback(data, msg), state, lang, user)
    assert e.draft[0]['minutes'] == 10


async def test_templates_owned_deletion_stale_revision_and_profile_erasure(db_session):
    db = db_session
    user, a, b = await sample(db)
    other = await make_user(db, 456)
    msg, state = message(456), SimpleNamespace(clear=AsyncMock())
    await handler.template_action(callback(f'wot:details:{a.id}', msg, 456), state, 'ru', other)
    assert msg.answer.call_args.args[0] == tr('stale', 'ru')
    msg = message()
    await handler.template_action(callback(f'wot:remove:{b.id}:999', msg), state, 'ru', user)
    assert len(await wt.templates(db, user.telegram_id)) == 2
    await handler.template_action(callback(f'wot:remove:{b.id}:0', msg), state, 'ru', user)
    assert len(await wt.templates(db, user.telegram_id)) == 1
    await crud.delete_user(db, user.telegram_id)
    assert await db.scalar(select(WorkoutTemplate).where(WorkoutTemplate.user_id == 123)) is None


async def test_analyze_persists_named_next_proposal_without_creating_actuals(db_session, monkeypatch):
    from src.services import gemini
    db = db_session
    user, a, b = await sample(db)
    w = await record(db, user, a)
    ctx = await svc.context(db, w, user)
    output = {'summary': 'Итог A', 'explanation': 'Далее B', 'items': [
        {'exercise_id': i['exercise_id'], 'sets': [
            {'minutes': s.get('minutes'), 'intensity': s['intensity']} if i['kind'] == 'cardio'
            else {'weight': s['weight'], 'reps': s['reps']} for s in i['actual']]}
        for i in ctx['planning_basis']]}
    provider = AsyncMock(return_value=SimpleNamespace(text=json.dumps(output)))
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    count = await db.scalar(select(func.count()).select_from(WorkoutEntry))
    assert await svc.analyze(db, w, user, 'ru')
    plan = await db.scalar(select(WorkoutPlan).where(WorkoutPlan.workout_id == w.id))
    assert plan.target_template['id'] == b.id and plan.status == 'proposed'
    assert await db.scalar(select(func.count()).select_from(WorkoutEntry)) == count
    await svc.change_plan(db, plan, plan.revision, user, 'accept')
    next_workout = await svc.start(db, user, b.id)
    assert next_workout.template_snapshot['plan_id'] == plan.id
    accepted_items = copy.deepcopy(plan.items)
    plan.items = []  # A later edit cannot change the schedule already copied at start.
    await db.commit()
    for item in accepted_items:
        entry = await svc.open_entry(db, next_workout, item['exercise_id'], user)
        assert entry.actual is None
        for draft, proposed in zip(entry.draft, item['sets'], strict=True):
            assert draft == proposed


async def test_time_budget_fits_start_template_and_new_profile_time_applies_to_next_plan(db_session):
    db = db_session
    user, a, _ = await sample(db)
    user.training_preferences = {'primary': 'maintain_strength', 'minutes': 20}
    await db.commit()
    w = await svc.start(db, user, a.id)
    assert w.template_snapshot['budget_adjusted']
    assert wt.estimate_minutes(w.template_snapshot['items']) <= 20
    assert len(a.items) == 6  # The reusable original is unchanged.
    # Save only the planned items, not extra library exercises.
    for item in w.template_snapshot['items']:
        e = await svc.open_entry(db, w, item['exercise_id'], user)
        if item['kind'] == 'cardio':
            await svc.mutate_entry(db, w, e.id, 'minutes', e.draft[0]['minutes_max'])
        else:
            for index, row in enumerate(e.draft):
                await svc.mutate_entry(db, w, e.id, 'select', index)
                if row['weight'] is None:
                    await svc.mutate_entry(db, w, e.id, 'weight', row['weight_min'])
        await svc.mutate_entry(db, w, e.id, 'save')
    await svc.finish(db, w)
    await db.commit()
    user.training_preferences = {**user.training_preferences, 'minutes': 10}
    await db.commit()
    ctx = await svc.context(db, w, user)
    assert ctx['next_time_budget'] == 10
    w.feedback = {'time_override': True}
    w.available_minutes = 15
    ctx = await svc.context(db, w, user)
    assert ctx['next_time_budget'] == 15


async def test_upgrade_0006_keeps_existing_facts_and_nutrition(monkeypatch):
    import sqlalchemy as sa
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.database import init_db
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    monkeypatch.setattr(init_db, 'engine', engine)
    try:
        async with engine.begin() as conn:
            statements = [
                'CREATE TABLE users (telegram_id BIGINT PRIMARY KEY, goal VARCHAR(20))',
                "INSERT INTO users VALUES (123, 'maintain')",
                'CREATE TABLE exercises (id INTEGER PRIMARY KEY, name VARCHAR, weight_step FLOAT)',
                "INSERT INTO exercises VALUES (1, 'Brustpresse', 2.5)",
                'CREATE TABLE workouts (id INTEGER PRIMARY KEY, status VARCHAR)',
                "INSERT INTO workouts VALUES (1, 'finished')",
                'CREATE TABLE workout_plans (id INTEGER PRIMARY KEY)',
                'CREATE TABLE workout_entries (id INTEGER PRIMARY KEY, actual JSON)',
                "INSERT INTO workout_entries VALUES (1, '[{\"weight\":30,\"reps\":12,\"confirmed\":true}]')",
                'CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)',
                "INSERT INTO alembic_version VALUES ('0006_workouts')",
            ]
            for statement in statements:
                # exec_driver_sql keeps literal JSON colon syntax out of bind parsing.
                await conn.exec_driver_sql(statement)
        await init_db.init_db()
        await init_db.init_db()
        async with engine.connect() as conn:
            assert (await conn.execute(sa.text('SELECT goal, training_preferences FROM users'))).one() == ('maintain', '{}')
            assert (await conn.execute(sa.text('SELECT name, kind FROM exercises'))).one() == ('Brustpresse', 'strength')
            actual = (await conn.execute(sa.text('SELECT actual FROM workout_entries'))).scalar()
            assert json.loads(actual) == [{'weight': 30, 'reps': 12, 'confirmed': True}]
            assert (await conn.execute(sa.text('SELECT COUNT(*) FROM workout_templates'))).scalar() == 0
    finally:
        await engine.dispose()


async def test_malformed_editor_callback_does_not_create_a_workout(db_session):
    user = await make_user(db_session)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    await handler.template_action(callback('wot:edit:0', msg), state, 'ru', user)
    assert await svc.current(db_session, user.telegram_id) is None
    assert msg.answer.call_args.args[0] == tr('stale', 'ru')
