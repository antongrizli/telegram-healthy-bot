import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import func, select

from src.database import crud
from src.database.models import Achievement, DailyWorkoutPlan, Workout, WorkoutEntry
from src.handlers import workout_daily as chat
from src.handlers import workouts as workout_chat
from src.services import gemini
from src.services import workout_progress as progress
from src.services import workout_today as daily
from src.services import workouts as svc
from src.utils.workout_locales import LANGS, tr
from src.webapp import server
from tests.integration.test_security_and_meal_recovery import make_user, signed_data
from tests.integration.test_workout_cards import callback, message

pytestmark = pytest.mark.asyncio


async def manual(db, user):
    w = await svc.start(db, user)
    bench = await svc.add_exercise(db, user.telegram_id, 'Жим лёжа')
    run = await svc.add_exercise(db, user.telegram_id, 'Бег', kind='cardio')
    pull = await svc.add_exercise(db, user.telegram_id, 'Подтягивания на турнике', kind='bodyweight')
    for exercise in (bench, run, pull):
        e = await svc.open_entry(db, w, exercise.id, user)
        if exercise == bench:
            for op, value in [('weight', 20), ('reps', 10), ('add', None), ('confirm', None)]:
                await svc.mutate_entry(db, w, e.id, op, value)
        elif exercise == run:
            await svc.mutate_entry(db, w, e.id, 'minutes', 20)
            await svc.mutate_entry(db, w, e.id, 'intensity', 'moderate')
        await svc.mutate_entry(db, w, e.id, 'save')
    await svc.finish(db, w)
    await db.commit()
    return w, bench, run, pull


async def configured(db, user):
    if not (user.training_preferences or {}).get('sessions_per_week'):
        user.training_preferences = {**(user.training_preferences or {}), 'sessions_per_week': 3}
        await db.commit()
    plan = await daily.today(db, user)
    await daily.configure(db, plan, user, plan.revision,
                          {'focus': 'combined', 'minutes': 40, 'effort': 'normal', 'wishes': 'Без лишней усталости'})
    return plan


def valid_output(bench, run, pull):
    return SimpleNamespace(text=json.dumps({'summary': 'Сегодня знакомые упражнения', 'explanation': 'Без повышения нагрузки',
        'items': [{'exercise_id': bench.id, 'sets': [{'weight': 20, 'reps': 10}, {'weight': 20, 'reps': 10}]},
                  {'exercise_id': run.id, 'sets': [{'minutes': 20, 'intensity': 'moderate'}]},
                  {'exercise_id': pull.id, 'sets': [{'weight': None, 'reps': None}]}]}))


async def test_manual_examples_and_bodyweight_never_become_zero_kg_progress(db_session):
    user = await make_user(db_session)
    w, bench, run, pull = await manual(db_session, user)
    rows = await svc.entries(db_session, w)
    assert rows[0].actual == [{'weight': 20, 'reps': 10, 'confirmed': True}] * 2
    assert rows[1].actual == [{'kind': 'cardio', 'minutes': 20, 'intensity': 'moderate'}]
    own = rows[2].actual[0]
    assert own['load_type'] == 'bodyweight' and own['reps'] is None
    assert svc.metrics(rows[2].actual)['confirmed_volume'] is None
    assert svc.metrics(rows[2].actual)['max_weight'] is None
    text = await workout_chat.numeric_summary(db_session, w, 'ru')
    assert 'Собственный вес' in text
    with pytest.raises(ValueError):
        w2 = await svc.start(db_session, user)
        e = await svc.open_entry(db_session, w2, pull.id, user)
        await svc.mutate_entry(db_session, w2, e.id, 'weight', 80)


async def test_today_generation_is_scoped_idempotent_and_not_a_fact(db_session, monkeypatch):
    db = db_session
    user = await make_user(db)
    user.training_preferences = {'sessions_per_week': 3}
    await db.commit()
    w, bench, run, pull = await manual(db, user)
    p = await configured(db, user)
    provider = AsyncMock(return_value=valid_output(bench, run, pull))
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    assert await daily.generate(db, p, user, p.revision, 'ru')
    assert p.status == 'proposed' and len(p.items) == 3
    assert await daily.generate(db, p, user, p.revision, 'ru') is False
    provider.assert_awaited_once()
    assert await db.scalar(select(func.count()).select_from(Workout)) == 1
    assert await db.scalar(select(func.count()).select_from(WorkoutEntry)) == 3
    context = provider.call_args.args[0][0]
    assert 'sessions_per_week' in context and 'telegram_id' not in context
    draft = await daily.accept(db, p, user, p.revision, 'ru')
    assert draft.status == 'active' and (await svc.entries(db, draft)) == []
    assert (await daily.accept(db, p, user, p.revision - 1, 'ru')).id == draft.id
    assert await db.scalar(select(func.count()).select_from(Workout).where(Workout.status == 'finished')) == 1
    pull_entry = await svc.open_entry(db, draft, pull.id, user)
    assert pull_entry.actual is None and pull_entry.draft[0]['load_type'] == 'bodyweight'


async def test_daily_lease_failure_retry_stale_profile_and_foreign_equipment(db_session, monkeypatch):
    db = db_session
    user = await make_user(db)
    _, bench, run, pull = await manual(db, user)
    p = await configured(db, user)
    successful_response = valid_output(bench, run, pull)
    provider = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    with pytest.raises(TimeoutError):
        await daily.generate(db, p, user, p.revision, 'ru')
    await db.refresh(p)
    await db.refresh(user)
    assert p.status == 'failed'
    p.status, p.generation_started_at = 'running', svc.now()
    await db.commit()
    assert await daily.generate(db, p, user, p.revision, 'ru') is False
    assert provider.await_count == 1
    p.generation_started_at = svc.now() - timedelta(minutes=5)
    await db.commit()
    provider.side_effect = None
    provider.return_value = successful_response
    assert await daily.generate(db, p, user, p.revision, 'ru')
    user.training_preferences = {'sessions_per_week': 4}
    await db.commit()
    with pytest.raises(svc.StaleWorkout):
        await daily.accept(db, p, user, p.revision, 'ru')
    await daily.configure(db, p, user, p.revision, {'focus': 'legs'})
    invalid = json.loads(provider.return_value.text)
    invalid['items'][0]['exercise_id'] = 99999
    provider.return_value = SimpleNamespace(text=json.dumps(invalid))
    with pytest.raises(ValueError):
        await daily.generate(db, p, user, p.revision, 'ru')


async def test_empty_library_and_pain_do_not_call_model(db_session, monkeypatch):
    user = await make_user(db_session)
    p = await configured(db_session, user)
    provider = AsyncMock()
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    assert await daily.generate(db_session, p, user, p.revision, 'ru')
    assert p.items == [] and p.summary == tr('empty_library', 'ru')
    await daily.configure(db_session, p, user, p.revision, {'wishes': 'Болит спина'})
    assert await daily.generate(db_session, p, user, p.revision, 'ru')
    assert p.summary == tr('safety', 'ru') and p.items == []
    provider.assert_not_called()


@pytest.mark.parametrize('lang', LANGS)
async def test_chat_asks_focus_time_effort_and_frequency_persists(db_session, lang):
    db = db_session
    user = await make_user(db)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    await chat.frequency(callback('wf:3', msg), state, lang)
    assert user.training_preferences['sessions_per_week'] == 3
    await chat.daily_action(callback('wd:open', msg), state, lang)
    p = await daily.today(db, user)
    assert tr('focus_prompt', lang) in msg.edit_text.call_args.kwargs['text']
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:focus:legs', msg), state, lang)
    assert tr('budget_prompt', lang) in msg.edit_text.call_args.kwargs['text']
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:minutes:30', msg), state, lang)
    assert tr('wish', lang) in msg.edit_text.call_args.kwargs['text']
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:effort:easy', msg), state, lang)
    assert daily.ready(p.preferences)
    for row in msg.edit_text.call_args.kwargs['reply_markup'].inline_keyboard:
        for b in row:
            assert len(b.callback_data.encode()) <= 64


async def test_progress_medals_exclude_drafts_and_count_local_training_days(db_session):
    db = db_session
    user = await make_user(db, timezone='Europe/Berlin')
    w, bench, run, pull = await manual(db, user)
    # Several sessions in one local day cannot farm the 10-day medal.
    for _ in range(10):
        db.add(Workout(user_id=user.telegram_id, status='finished', finished_at=w.finished_at))
    db.add(Workout(user_id=user.telegram_id, status='active'))
    db.add(Workout(user_id=user.telegram_id, status='template'))
    await db.commit()
    data = await progress.dashboard(db, user, exercise_id=pull.id)
    assert data['totals']['sessions'] == 11 and data['totals']['days'] == 1
    assert len(data['history']) == 5 and data['active']['status'] == 'active'
    assert data['progress'][0]['metrics']['max_weight'] is None
    assert next(m for m in data['medals'] if m['key'] == 'workout_first')['unlocked']
    assert not next(m for m in data['medals'] if m['key'] == 'workout_10')['unlocked']
    await progress.award(db, user)
    assert await db.scalar(select(func.count()).select_from(Achievement).where(Achievement.achievement_key == 'workout_first')) == 1


async def test_api_signed_access_ownership_generation_and_preferences(db_session, monkeypatch):
    db = db_session
    user = await make_user(db)
    _, bench, run, pull = await manual(db, user)
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', AsyncMock(return_value=valid_output(bench, run, pull)))
    headers = {'X-Telegram-Init-Data': signed_data()}
    async with TestClient(TestServer(server.create_app(None))) as client:
        assert (await client.get('/api/workouts')).status == 401
        res = await client.get('/api/workouts', headers=headers)
        assert res.status == 200
        data = await res.json()
        assert data['totals']['sessions'] == 1 and len(data['library']) == 3
        assert (await client.post('/api/workouts/preferences', headers=headers, json={'sessions_per_week': 8})).status == 400
        assert (await client.post('/api/workouts/preferences', headers=headers, json={'sessions_per_week': 3})).status == 200
        res = await client.post('/api/workouts/configure', headers=headers, json={'revision':0, 'preferences':
            {'focus':'combined','minutes':40,'effort':'normal','wishes':''}})
        assert res.status == 200
        p = await res.json()
        res = await client.post(f"/api/workouts/today/{p['id']}/generate", headers=headers, json={'revision':p['revision']})
        assert res.status == 200 and (await res.json())['status'] == 'proposed'
        assert (await client.post(f"/api/workouts/today/{p['id']}/accept", headers=headers, json={'revision':p['revision']})).status == 200
        await make_user(db, 456)
        foreign_headers = {'X-Telegram-Init-Data': signed_data(456)}
        assert (await client.get('/api/workouts/history/1', headers=foreign_headers)).status == 409
        assert (await client.post(f"/api/workouts/today/{p['id']}/accept", headers=foreign_headers, json={'revision':p['revision']})).status == 409
        assert (await client.get('/api/workouts?exercise_id=1', headers=foreign_headers)).status == 409
        user.is_blocked = True
        await db.commit()
        assert (await client.get('/api/workouts', headers=headers)).status == 403


async def test_profile_erasure_removes_daily_proposal(db_session):
    user = await make_user(db_session)
    await daily.today(db_session, user)
    await crud.delete_user(db_session, user.telegram_id)
    assert await db_session.scalar(select(DailyWorkoutPlan)) is None


async def test_today_first_asks_frequency_and_custom_preferences_survive_restart(db_session, monkeypatch):
    db = db_session
    user = await make_user(db)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    await chat.daily_action(callback('wd:open', msg), state, 'ru')
    p = await daily.today(db, user)
    assert tr('frequency_prompt', 'ru') in msg.edit_text.call_args.kwargs['text']
    await daily.configure(db, p, user, p.revision, {'focus':'combined','minutes':30,'effort':'normal'})
    provider = AsyncMock()
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    with pytest.raises(svc.StaleWorkout):
        await daily.generate(db, p, user, p.revision, 'ru')
    provider.assert_not_called()
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:frequency:2', msg), state, 'ru')
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:focus:custom', msg), state, 'ru')
    pending = user.ux_preferences['daily_workout_input']
    assert await chat.DailyInput().__call__(message(text='Ноги и пресс')) == {'daily_input':pending}
    await chat.daily_input(message(text='Ноги и пресс'), pending, 'ru')
    assert p.preferences['wishes'] == 'Ноги и пресс'
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:inputminutes:0', msg), state, 'ru')
    await chat.daily_input(message(text='17'), user.ux_preferences['daily_workout_input'], 'ru')
    assert p.preferences['minutes'] == 17
    assert user.training_preferences['sessions_per_week'] == 2


async def test_expired_plan_and_other_active_draft_are_protected(db_session, monkeypatch):
    db = db_session
    user = await make_user(db)
    _, bench, run, pull = await manual(db, user)
    p = await configured(db, user)
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', AsyncMock(return_value=valid_output(bench, run, pull)))
    await daily.generate(db, p, user, p.revision, 'ru')
    active = await svc.start(db, user)
    entry = await svc.open_entry(db, active, bench.id, user)
    await svc.mutate_entry(db, active, entry.id, 'weight', 17.5)
    await db.commit()
    with pytest.raises(svc.StaleWorkout):
        await daily.accept(db, p, user, p.revision, 'ru')
    assert entry.draft[0]['weight'] == 17.5 and entry.actual is None
    p.local_date -= timedelta(days=1)
    await db.commit()
    with pytest.raises(svc.StaleWorkout):
        await daily.accept(db, p, user, p.revision, 'ru')


async def test_medal_days_use_user_timezone_at_midnight(db_session):
    from datetime import datetime
    user = await make_user(db_session, timezone='Europe/Berlin')
    for date in (datetime(2026,11,1,23,30), datetime(2026,11,2,0,30)):
        db_session.add(Workout(user_id=user.telegram_id,status='finished',finished_at=date))
    await db_session.commit()
    data = await progress.dashboard(db_session, user)
    assert data['totals']['sessions'] == 2 and data['totals']['days'] == 1


async def test_stale_generate_clears_busy_message_and_expired_lease_is_retryable(db_session, monkeypatch):
    from src.webapp.workouts import serialize_plan
    user = await make_user(db_session)
    p = await configured(db_session, user)
    p.status = 'running'
    p.generation_started_at = svc.now() - timedelta(minutes=4)
    await db_session.commit()
    assert serialize_plan(p, user)['retry_allowed']
    p.status = 'draft'
    user.training_preferences = {**user.training_preferences, 'sessions_per_week':4}
    await db_session.commit()
    msg, status, state = message(), message(), SimpleNamespace(clear=AsyncMock())
    msg.answer.return_value = status
    provider = AsyncMock()
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', provider)
    await chat.daily_action(callback(f'wd:{p.id}:{p.revision}:generate:0', msg), state, 'ru')
    provider.assert_not_called()
    assert status.edit_text.call_args.args[0] == tr('stale', 'ru')


async def test_daily_summary_fits_postgres_column_at_schema_limits(db_session, monkeypatch):
    user = await make_user(db_session)
    _, bench, run, pull = await manual(db_session, user)
    p = await configured(db_session, user)
    value = json.loads(valid_output(bench, run, pull).text)
    value.update(summary='A' * 1800, explanation='B' * 1200)
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', AsyncMock(return_value=SimpleNamespace(text=json.dumps(value))))
    assert await daily.generate(db_session, p, user, p.revision, 'ru')
    assert len(p.summary) <= 3000
