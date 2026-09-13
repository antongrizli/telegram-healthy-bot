import copy
from datetime import datetime, timedelta, UTC
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, Update, Chat, User as TelegramUser, ReplyKeyboardMarkup
from sqlalchemy import select

from src.database import crud
from src.config import settings
from src.keyboards import reply
from src.database.models import FoodLog, ProductEvent, WaterLog
from src.services import ux, scheduler, gemini
from src.webapp import server
from src.handlers import common, food, profile
from src.utils.i18n_locales import get_text, LOCALES
from tests.integration.test_security_and_meal_recovery import make_user, signed_data, ANALYSIS
from tests.integration.test_handlers import make_mock_message


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', '', None, -1, 20001])
def test_numeric_rejects_nonfinite_and_out_of_range(value):
    with pytest.raises((ValueError, TypeError)):
        ux.numeric(value, 0, 20000)


def test_portion_and_manual_corrections_keep_totals_consistent():
    original = copy.deepcopy(ANALYSIS)
    half = ux.adjust_locally(original, 'portion', '0,5')
    assert half['total_calories'] == 50
    assert half['total_protein'] == 0.5
    assert original == ANALYSIS
    manual = ux.adjust_locally(original, 'manual', '450 30 15 49')
    assert manual['total_calories'] == 450
    assert manual['food_items'][0]['protein'] == manual['total_protein'] == 30
    kcal_only = ux.adjust_locally(original, 'manual', '200')
    assert kcal_only['total_protein'] == ANALYSIS['total_protein']
    assert kcal_only['food_items'][0]['calories'] == 200
    with pytest.raises(ValueError):
        ux.adjust_locally(original, 'remove', '0')
    two = {**original, 'food_items': original['food_items'] * 2}
    assert len(ux.adjust_locally(two, 'remove', '0')['food_items']) == 1


@pytest.mark.parametrize('language', ['en', 'ru', 'uk', 'pl', 'de', 'tr', 'es'])
def test_personal_actions_use_progress_goal_and_missing_data(language):
    user = SimpleNamespace(language=language, target_calories=2000, target_protein=100, goal='maintain')
    assert ux.next_action(user, dict(count=0, cal=0, protein=0)) == get_text('ux_empty', language)
    protein = ux.next_action(user, dict(count=2, cal=1600, protein=30))
    assert protein == get_text('ux_tip_protein', language, protein_pct=30, cal_pct=80)
    review = ux.next_action(user, dict(count=3, cal=2400, protein=30))
    assert review == get_text('ux_tip_review', language, cal_pct=120)
    user.goal = 'gain_muscle'
    goal_tip = ux.next_action(user, dict(count=3, cal=1800, protein=100))
    assert get_text('goal_gain_m', language) in goal_tip
    sparse = ux.next_action(user, dict(count=2, cal=100, protein=1), days=7)
    assert sparse == get_text('ux_tip_missing_days', language, count=2)
    assert all('{' not in message for message in (protein, review, goal_tip, sparse))


@pytest.mark.parametrize('language', ['en', 'ru', 'uk', 'pl', 'de', 'tr', 'es'])
def test_streak_copy_is_localized_including_empty_state(language):
    user = SimpleNamespace(streak_freezes_left=1)
    assert get_text('ux_streak_empty', language) in ux.streak_text(user, [], language)
    streak = SimpleNamespace(streak_type='food_logging', current_count=3, longest_count=8)
    text = ux.streak_text(user, [streak], language)
    assert get_text('ux_streak_row', language, name=get_text('btn_log_food', language), current=3, best=8) in text
    assert get_text('ux_freezes', language, count=1) in text


@pytest.mark.asyncio
async def test_today_uses_local_day_and_excludes_drafts(db_session):
    user = await make_user(db_session, timezone='Europe/Berlin')
    # The autumn DST day contains two different 02:30 entries.
    now = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)
    for when in [datetime(2026, 10, 24, 21, tzinfo=UTC), datetime(2026, 10, 25, 0, 30, tzinfo=UTC), datetime(2026, 10, 25, 1, 30, tzinfo=UTC)]:
        await crud.add_food_log(db_session, 123, ANALYSIS['food_items'], 100, 1, 2, 20, logged_at=when)
    await crud.save_meal_draft(db_session, 123, dict(analysis=ANALYSIS, logged_at=now.isoformat()))
    db_session.add(WaterLog(user_id=123, milliliters=250, logged_at=now.replace(tzinfo=None)))
    await db_session.commit()
    today = await ux.today_data(db_session, user, now)
    assert today['values']['date'] == '2026-10-25'
    assert today['values']['count'] == 2
    assert today['values']['cal'] == 200
    assert today['values']['water'] == 250
    assert [m['time'] for m in today['meals']] == ['02:30', '02:30']


@pytest.mark.asyncio
async def test_preferences_quiet_hours_and_weekly_mode(db_session):
    user = await make_user(db_session, timezone='Europe/Berlin', notifications_enabled=True,
        ux_preferences={'frequency': 'weekly', 'quiet_start': '22:00', 'quiet_end': '08:00'})
    daytime = datetime(2026, 9, 12, 12, tzinfo=UTC)
    assert not ux.coaching_allowed(user, 'daily', daytime)
    assert ux.coaching_allowed(user, 'weekly', daytime)
    assert not ux.coaching_allowed(user, 'weekly', daytime.replace(hour=21))
    user.ux_preferences = {'frequency': 'daily', 'quiet_start': '08:00', 'quiet_end': '18:00'}
    assert not ux.coaching_allowed(user, now=daytime)
    user.ux_preferences = {'frequency': 'daily', 'quiet_start': '08:00', 'quiet_end': '08:00'}
    assert ux.coaching_allowed(user, now=daytime)
    user.notifications_enabled = False
    assert not ux.coaching_allowed(user, now=daytime)


@pytest.mark.asyncio
async def test_onboarding_completes_after_seven_inputs(db_session, monkeypatch):
    monkeypatch.setattr(profile, 'reschedule_user_jobs', lambda *args: None)
    monkeypatch.setattr('src.middlewares.i18n.update_user_menu_button', AsyncMock())
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=12345, user_id=12345))
    await profile.start_profile_setup(make_mock_message('/start'), state, 'en')
    for text, handler in [('English 🇺🇸', profile.process_language), ('Male ♂️', profile.process_sex), ('30', profile.process_age), ('180', profile.process_height), ('80', profile.process_weight), (get_text('act_light'), profile.process_activity), (get_text('goal_maintain'), profile.process_goal)]:
        if handler == profile.process_sex:
            text = get_text('sex_male')
        await handler(make_mock_message(text), state, 'en')
    user = await crud.get_user(db_session, 12345)
    assert user is not None and user.name == 'Test'
    assert not user.notifications_enabled and user.timezone == 'UTC'
    assert await state.get_state() is None
    metrics = await crud.get_product_metrics(db_session)
    assert metrics['onboarding_started'] == metrics['onboarding_completed'] == 1
    await storage.close()


@pytest.mark.asyncio
async def test_local_draft_correction_survives_state_reset_and_cannot_cross_owners(db_session):
    await make_user(db_session)
    draft_id = await crud.save_meal_draft(db_session, 123, dict(analysis=ANALYSIS, logged_at=datetime.now(UTC).isoformat()))
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))
    callback = SimpleNamespace(data=f'uxdraft:portion:{draft_id}', from_user=SimpleNamespace(id=456), answer=AsyncMock(), message=SimpleNamespace(answer=AsyncMock()))
    await food.quick_correction(callback, state, 'en')
    assert callback.answer.call_args.kwargs['show_alert']
    assert await state.get_state() is None
    callback.from_user.id = 123
    await food.quick_correction(callback, state, 'en')
    await food.local_correction(make_mock_message('1.5', user_id=123), state, 'en')
    await state.clear()
    meal = await crud.finish_meal_draft(db_session, draft_id, 123)
    assert meal.calories == 150
    assert await crud.finish_meal_draft(db_session, draft_id, 123) is None
    await storage.close()


@pytest.mark.asyncio
async def test_webapp_daily_actions_auth_ownership_validation_and_settings(db_session, monkeypatch):
    await make_user(db_session)
    await make_user(db_session, 456)
    monkeypatch.setattr(scheduler, 'reschedule_user_jobs', lambda *args: None)
    headers = {'X-Telegram-Init-Data': signed_data()}
    async with TestClient(TestServer(server.create_app(AsyncMock()))) as client:
        assert (await client.get('/api/today')).status == 401
        assert (await client.post('/api/ux/water', json={'amount': 250})).status == 401
        values = dict(name='<script>meal</script>', calories=450, protein=30, fat=15, carb=49)
        assert (await client.post('/api/ux/meals', json=values, headers=headers)).status == 200
        data = await (await client.get('/api/today', headers=headers)).json()
        assert data['labels']['err_analysis_failed'] == get_text('err_analysis_failed')
        assert data['values']['cal'] == 450 and data['values']['count'] == 1
        meal_id = data['meals'][0]['id']
        other = {'X-Telegram-Init-Data': signed_data(456)}
        assert (await client.patch(f'/api/ux/meals/{meal_id}', json=values, headers=other)).status == 404
        assert (await client.patch(f'/api/ux/meals/{meal_id}', json={**values, 'calories': 300}, headers=headers)).status == 200
        assert (await client.post('/api/ux/weight', json={'weight': 'nan'}, headers=headers)).status == 400
        assert (await client.post('/api/ux/water', json={'amount': 250}, headers=headers)).status == 200
        assert (await client.post('/api/ux/weight', json={'weight': '78,5'}, headers=headers)).status == 200
        assert (await crud.get_user(db_session, 123)).weight_kg == 78.5
        prefs = dict(timezone='Europe/Berlin', notifications_enabled=True, frequency='weekly', quiet_start='22:00', quiet_end='08:00')
        assert (await client.post('/api/ux/settings', json={**prefs, 'timezone': 'not/a/zone'}, headers=headers)).status == 400
        assert (await client.post('/api/ux/settings', json=prefs, headers=headers)).status == 200
        assert (await crud.get_user(db_session, 123)).ux_preferences['frequency'] == 'weekly'
        draft_id = await crud.save_meal_draft(db_session, 123, dict(analysis=ANALYSIS, logged_at=datetime.now(UTC).isoformat()))
        path = f'/api/ux/drafts/{draft_id}'
        assert (await client.post(path, json={'action': 'accept'}, headers=other)).status == 404
        assert (await client.post(path, json={'action': 'accept'}, headers=headers)).status == 200
        assert (await client.post(path, json={'action': 'accept'}, headers=headers)).status == 404
        await crud.block_user(db_session, 123)
        assert (await client.get('/api/today', headers=headers)).status == 403
        assert (await client.post('/api/ux/water', json={'amount': 250}, headers=headers)).status == 403


@pytest.mark.asyncio
async def test_report_summary_is_short_details_are_owned_and_empty_report_skips_ai(db_session, monkeypatch):
    user = await make_user(db_session)
    bot = SimpleNamespace(send_message=AsyncMock())
    generate = AsyncMock(return_value='Long detail\n' * 150)
    monkeypatch.setattr(gemini, 'generate_report', generate)
    await scheduler.generate_and_send_report_direct(bot, db_session, user, 'daily')
    generate.assert_not_awaited()
    await crud.add_food_log(db_session, 123, ANALYSIS['food_items'], 100, 1, 2, 20)
    await scheduler.generate_and_send_report_direct(bot, db_session, user, 'daily')
    text = bot.send_message.call_args.args[1]
    assert len(text) < 600 and '100/2000' in text
    button = bot.send_message.call_args.kwargs['reply_markup'].keyboard[0][0]
    report_id = common.report_details_id(button.text)
    assert report_id is not None
    assert await crud.get_saved_report(db_session, 456, report_id) is None
    assert (await crud.get_saved_report(db_session, 123, report_id)).payload['text'] == generate.return_value


@pytest.mark.asyncio
async def test_metrics_mature_cohorts_and_profile_deletion(db_session):
    await make_user(db_session)
    now = datetime.now(UTC).replace(tzinfo=None)
    start = (now - timedelta(days=9)).replace(hour=10)
    for name, when in [('onboarding_started', start), ('onboarding_completed', start + timedelta(minutes=1)), ('meal_confirmed', start + timedelta(minutes=3)), ('active', start + timedelta(days=1)), ('active', start + timedelta(days=7))]:
        db_session.add(ProductEvent(user_id=123, name=name, occurred_at=when))
    db_session.add(ProductEvent(user_id=456, name='onboarding_started', occurred_at=now))
    await db_session.commit()
    data = await crud.get_product_metrics(db_session, now)
    assert data['first_meal_median_seconds'] == 180
    assert data['retention']['D7'] == {'returned': 1, 'eligible': 1}
    assert data['retention']['D1'] == {'returned': 1, 'eligible': 1}
    await crud.add_water_log(db_session, 123, 250)
    await crud.delete_user(db_session, 123)
    assert not (await db_session.execute(select(ProductEvent).where(ProductEvent.user_id == 123))).scalars().all()
    assert not (await db_session.execute(select(WaterLog).where(WaterLog.user_id == 123))).scalars().all()


@pytest.mark.asyncio
async def test_dispatcher_quick_input_menus_cancel_and_help_in_all_languages(db_session, monkeypatch):
    from src.handlers import common, ux as ux_handlers, weight, callbacks
    user = await make_user(db_session)
    dp = Dispatcher(storage=MemoryStorage())
    routers = [common.recovery_router, ux_handlers.router, profile.router, food.router, weight.router, callbacks.router, common.router]
    parents = [r.parent_router for r in routers]
    for router in routers:
        router._parent_router = None
        dp.include_router(router)
    language = 'en'
    async def context(handler, event, data):
        data.update(db_user=user, user_language=language)
        return await handler(event, data)
    dp.message.outer_middleware(context)
    bot = Bot('123456:test'); bot.session = AsyncMock()
    process = AsyncMock()
    monkeypatch.setattr(food, 'process_food_input', process)
    async def send(text, index=1):
        bot.session.reset_mock()
        await dp.feed_update(bot, Update(update_id=index, message=Message(message_id=index, date=datetime.now(UTC),
            chat=Chat(id=123, type='private'), from_user=TelegramUser(id=123, is_bot=False, first_name='Test'), text=text)))
    try:
        for language in LOCALES:
            progress_keyboard = reply.get_progress_keyboard(language)
            assert isinstance(progress_keyboard, ReplyKeyboardMarkup)
            assert [[button.text for button in row] for row in progress_keyboard.keyboard] == [
                [get_text('ux_progress', language)],
                [get_text('btn_all_achievements', language), get_text('btn_view_card', language)],
                [get_text('btn_weekly_report', language)],
                [get_text('ux_back', language)],
            ]
            assert progress_keyboard.keyboard[0][0].web_app.url == f'{settings.WEBAPP_URL}?tab=charts'
            await send(get_text('ux_progress', language))
            assert isinstance(bot.session.call_args.args[1].reply_markup, ReplyKeyboardMarkup)
            await send(get_text('ux_today', language))
            keyboard = bot.session.call_args.args[1].reply_markup
            assert isinstance(keyboard, ReplyKeyboardMarkup)
            assert keyboard.resize_keyboard is True
            assert [[button.text for button in row] for row in keyboard.keyboard] == [
                [get_text('btn_log_food', language)],
                [get_text('btn_daily_report', language), get_text('btn_my_meals', language)],
                [get_text('btn_pending_meals', language)],
                [get_text('ux_back', language)],
            ]
            assert keyboard.keyboard[1][1].web_app.url == settings.WEBAPP_URL
            await send(get_text('ux_back', language))
            assert bot.session.call_args.args[1].reply_markup == reply.get_main_menu(
                language, user.is_admin or user.telegram_id in settings.ADMIN_USER_IDS)
            await send(get_text('ux_add', language))
            await send(get_text('btn_help', language))
            assert bot.session.call_args.args[1].text == get_text('help_text', language)
            await send(get_text('btn_log_food', language))
            await send(get_text('btn_cancel', language))
            assert await dp.fsm.get_context(bot=bot, chat_id=123, user_id=123).get_state() is None
        await send('Rice and salmon')
        process.assert_awaited_once()
    finally:
        for router, parent in zip(routers, parents):
            router._parent_router = parent
        await dp.storage.close()
