from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.database.models import WorkoutEntry
from src.handlers import workouts as handler
from src.keyboards import reply
from src.services import workouts as svc
from src.utils.i18n_locales import get_text
from src.utils.workout_locales import COPY, LANGS, tr
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


def message(uid=123, text=''):
    return SimpleNamespace(chat=SimpleNamespace(id=uid), from_user=SimpleNamespace(id=uid), text=text,
                           answer=AsyncMock(), edit_text=AsyncMock())


def callback(data, msg, uid=123):
    return SimpleNamespace(data=data, from_user=SimpleNamespace(id=uid), message=msg, answer=AsyncMock())


def find_button(msg, op):
    markup = msg.edit_text.call_args.kwargs['reply_markup']
    return next(b.callback_data for row in markup.inline_keyboard for b in row
                if b.callback_data.startswith('wo:') and b.callback_data.split(':')[3] == op)


async def test_cards_record_save_reject_stale_finish_and_preserve_results_on_llm_failure(db_session, monkeypatch):
    from src.services import gemini
    db = db_session
    user = await make_user(db)
    msg, state = message(), SimpleNamespace(clear=AsyncMock())
    await handler.new_workout(callback('woh:new', msg), state, 'ru', user)
    w = await svc.current(db, user.telegram_id)
    assert w is not None
    await handler.action(callback(find_button(msg, 'newexercise'), msg), state, 'ru', user)
    assert w.pending['mode'] == 'name_prompt'
    # A new message after restart finds its durable prompt without any FSM state.
    assert await handler.PendingInput().__call__(message(text='Жим ногами')) == {'workout_input_id': w.id}
    await handler.text_input(message(text='Жим ногами'), w.id, 'ru')
    e = await db.scalar(select(WorkoutEntry).where(WorkoutEntry.workout_id == w.id))
    await handler.card(db, msg, w, e, 'ru', edit=True)
    weight_button = find_button(msg, 'inputweight')
    await handler.action(callback(weight_button, msg), state, 'ru', user)
    await handler.text_input(message(text='80'), w.id, 'ru')
    # Stale button cannot apply a different write after the numeric input.
    prior = w.revision
    await handler.action(callback(weight_button, msg), state, 'ru', user)
    assert w.revision == prior
    assert msg.answer.call_args.args[0] == tr('stale', 'ru')
    for action, value in [('reps', 10), ('add', None), ('add', None), ('weight', 70), ('reps', 8)]:
        await svc.mutate_entry(db, w, e.id, action, value)
    await db.commit()
    await handler.card(db, msg, w, e, 'ru', edit=True)
    save_button = find_button(msg, 'save')
    await handler.action(callback(save_button, msg), state, 'ru', user)
    assert [s['weight'] for s in e.actual] == [80, 80, 70]
    await handler.action(callback(save_button, msg), state, 'ru', user)
    assert len(await svc.entries(db, w)) == 1
    await handler.library(db, msg, w, 'ru', edit=True)
    await handler.action(callback(find_button(msg, 'finish'), msg), state, 'ru', user)
    assert w.status == 'finished'
    assert 'Тренировка сохранена' in msg.edit_text.call_args.kwargs['text']
    await handler.action(callback(find_button(msg, 'effort'), msg), state, 'ru', user)
    assert w.feedback['effort'] == 'easy'
    status = message()
    msg.answer.return_value = status
    monkeypatch.setattr(gemini, 'call_gemini_with_retry', AsyncMock(side_effect=TimeoutError()))
    await handler.action(callback(find_button(msg, 'wish'), msg), state, 'ru', user)
    await db.refresh(w)
    assert w.status == 'finished' and w.analysis_state == 'failed'
    assert status.edit_text.call_args.args[0] == tr('error', 'ru')
    assert 'wo:' in status.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data


async def test_foreign_callback_cannot_write_or_reveal_name(db_session):
    db = db_session
    user = await make_user(db)
    await make_user(db, 456)
    w = await svc.start(db, user)
    msg = message(456)
    state = SimpleNamespace(clear=AsyncMock())
    await handler.action(callback(f'wo:{w.id}:0:newexercise:0:0', msg, 456), state, 'ru', None)
    assert w.pending == {}
    assert msg.answer.call_args.args[0] == tr('stale', 'ru')


async def test_cancel_new_unsaved_entry_allows_finishing_saved_ones(db_session):
    db = db_session
    user = await make_user(db)
    w = await svc.start(db, user)
    a = await svc.add_exercise(db, user.telegram_id, 'A')
    b = await svc.add_exercise(db, user.telegram_id, 'B')
    e = await svc.open_entry(db, w, a.id, user)
    await svc.mutate_entry(db, w, e.id, 'weight', 0)
    await svc.mutate_entry(db, w, e.id, 'save')
    other = await svc.open_entry(db, w, b.id, user)
    await db.commit()
    msg = message()
    await handler.action(callback(f'wo:{w.id}:{w.revision}:cancel:{other.id}:0', msg),
                         SimpleNamespace(clear=AsyncMock()), 'ru', user)
    await svc.finish(db, w)
    assert len(await svc.entries(db, w)) == 1


@pytest.mark.parametrize('lang', LANGS)
async def test_localized_menu_card_callback_lengths_and_goals(lang, db_session):
    assert all(len(values) == 7 and values[LANGS.index(lang)] for values in COPY.values())
    menu = reply.get_main_menu(lang)
    assert [b.text for b in menu.keyboard[-1]] == [get_text('btn_workout', lang)]
    profile = reply.get_setup_profile_keyboard(lang, registered=True)
    assert profile.keyboard[1][0].text == get_text('btn_workout_goals', lang)
    db = db_session
    user = await make_user(db)
    w = await svc.start(db, user)
    ex = await svc.add_exercise(db, user.telegram_id, 'X' * 100)
    e = await svc.open_entry(db, w, ex.id, user)
    e.draft = [{'weight': 80, 'reps': 10, 'confirmed': False} for _ in range(12)]
    await db.commit()
    msg = message()
    await handler.card(db, msg, w, e, lang, edit=True)
    data = msg.edit_text.call_args.kwargs
    assert len(data['text']) <= 4096
    assert all(len(b.callback_data.encode()) <= 64 for row in data['reply_markup'].inline_keyboard for b in row)
    await handler.goals_card(msg, user, lang, edit=True)
    goal_button = msg.edit_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
    await handler.toggle_goal(callback(goal_button.callback_data, msg), lang)
    assert len(user.additional_goals) == 1
    # Redelivered callback sets the same desired state, it does not toggle it back.
    await handler.toggle_goal(callback(goal_button.callback_data, msg), lang)
    assert len(user.additional_goals) == 1


async def test_comment_reporting_pain_blocks_standard_plan(db_session):
    user = await make_user(db_session)
    w = await svc.start(db_session, user)
    w.status, w.finished_at = 'finished', svc.now()
    w.pending = {'mode': 'comment_prompt'}
    await db_session.commit()
    await handler.text_input(message(text='После жима болит колено'), w.id, 'ru')
    assert w.feedback['pain'] is True
