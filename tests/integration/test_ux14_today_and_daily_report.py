import pytest
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, UTC

from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Update, Message, User, Chat, MessageEntity
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from src.database import crud
from src.handlers import common, food, admin, profile, weight, callbacks, ux, medications
from src.keyboards import reply
from src.services import gemini, scheduler
from src.services import rate_limiter
from src.utils import i18n_locales
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('period, age, expected', [('daily', 0, 0), ('daily', 2, 1), ('weekly', 0, 1)])
async def test_legacy_queued_report_dedup_uses_original_day(db_session, test_setup, monkeypatch, period, age, expected):
    await make_user(db_session, 4321)
    await crud.add_food_log(db_session, 4321, [{'name': 'Toast'}],
        calories=100, proteins=5, fats=2, carbs=15)
    await rate_limiter.add_to_queue(db_session, 4321, 4321, 'generate_report',
        {'report_type': period, 'report_at': (datetime.now(UTC) - timedelta(days=age)).isoformat()})
    generate = AsyncMock(return_value='Report')
    monkeypatch.setattr(gemini, 'generate_report', generate)
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    await scheduler.send_daily_report(SimpleNamespace(send_message=AsyncMock()), 4321)
    assert generate.await_count == expected
    assert 4321 not in scheduler._in_flight_daily_reports


@pytest.mark.parametrize('change', ['calories', 'items', 'weight', 'goal', 'language', 'water', 'medications', 'intake'])
async def test_daily_cache_invalidated_by_report_inputs(db_session, test_setup, monkeypatch, change):
    user = await make_user(db_session, 9876)
    await crud.add_food_log(db_session, 9876, [{'name': 'Toast'}],
                            calories=100, proteins=5, fats=2, carbs=15)
    generate = AsyncMock(return_value='Report')
    monkeypatch.setattr(gemini, 'generate_report', generate)
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_report(bot, 9876)
    if change in ('calories', 'items'):
        from sqlalchemy import select
        from src.database.models import FoodLog
        row = (await db_session.execute(select(FoodLog).where(FoodLog.user_id == 9876))).scalar_one()
        if change == 'calories': row.calories = 600
        else: row.items_json = [{'name': 'Rice'}]
    elif change == 'weight':
        await crud.add_weight_log(db_session, 9876, 79)
    elif change == 'goal': user.goal = 'gain_weight'
    elif change == 'language': user.language = 'de'
    elif change == 'water': await crud.add_water_log(db_session, 9876, 250)
    elif change == 'medications':
        from src.services import medications
        monkeypatch.setattr(medications, 'report_context', AsyncMock(return_value=[{'name': 'New schedule'}]))
    else:
        monkeypatch.setattr(crud, 'daily_report_intake_state', AsyncMock(return_value=[(1, 1, 'taken')]))
    await db_session.commit()
    await scheduler.send_daily_report(bot, 9876)
    assert generate.await_count == 2


async def test_daily_claim_precedes_first_await_and_releases_on_cancellation(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def body(*args):
        entered.set()
        await release.wait()
    mocked = AsyncMock(side_effect=body)
    monkeypatch.setattr(scheduler, '_send_daily_report', mocked)
    first = asyncio.create_task(scheduler.send_daily_report(None, 6543))
    await entered.wait()
    await scheduler.send_daily_report(None, 6543)
    assert mocked.await_count == 1
    first.cancel()
    with pytest.raises(asyncio.CancelledError): await first
    assert 6543 not in scheduler._in_flight_daily_reports
    release.set()
    await scheduler.send_daily_report(None, 6543)
    assert mocked.await_count == 2


async def test_cached_delivery_cooldown_and_weekly_queue_independence(db_session, test_setup, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(scheduler, 'monotonic', lambda: clock[0])
    await make_user(db_session, 7654)
    await crud.add_food_log(db_session, 7654, [{'name': 'Toast'}],
                            calories=100, proteins=5, fats=2, carbs=15)
    await rate_limiter.add_to_queue(db_session, 7654, 7654, 'generate_report', {'report_type': 'weekly'})
    generate = AsyncMock(return_value='Report')
    monkeypatch.setattr(gemini, 'generate_report', generate)
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_report(bot, 7654)
    generate.assert_awaited_once()
    delivered = bot.send_message.await_count
    await scheduler.send_daily_report(bot, 7654)
    assert bot.send_message.await_count == delivered
    clock[0] += 31
    await scheduler.send_daily_report(bot, 7654)
    assert bot.send_message.await_count == delivered + 1


async def test_snapshot_fingerprint_captured_before_ai(db_session, test_setup, monkeypatch):
    user = await make_user(db_session, 8765)
    await crud.add_food_log(db_session, 8765, [{'name': 'Toast'}],
                            calories=100, proteins=5, fats=2, carbs=15)
    async def generate(*args, **kwargs):
        user.goal = 'gain_weight'
        await db_session.commit()
        return 'Report from previous inputs'
    mocked = AsyncMock(side_effect=generate)
    monkeypatch.setattr(gemini, 'generate_report', mocked)
    monkeypatch.setattr(rate_limiter, 'check_rate_limit', AsyncMock(return_value=(False, '')))
    bot = SimpleNamespace(send_message=AsyncMock())
    await scheduler.send_daily_report(bot, 8765)
    await scheduler.send_daily_report(bot, 8765)
    assert mocked.await_count == 2


@pytest.fixture
def test_setup(db_session, monkeypatch):
    class Session:
        async def __aenter__(self): return db_session
        async def __aexit__(self, *args): pass
    for mod in [medications, food, ux, profile, weight, common, scheduler]:
        if hasattr(mod, "AsyncSessionLocal"):
            monkeypatch.setattr(mod, "AsyncSessionLocal", Session)
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    for router in [common.recovery_router, admin.router, ux.router, profile.router,
                   medications.router, food.router, weight.router, callbacks.router, common.router]:
        router._parent_router = None
        dp.include_router(router)
    bot = Bot("123456:test", session=AsyncMock())
    yield dp, bot
    for router in [common.recovery_router, admin.router, ux.router, profile.router,
                   medications.router, food.router, weight.router, callbacks.router, common.router]:
        router._parent_router = None


async def feed_message(dp, bot, text, user_id=123, index=1):
    bot.session.reset_mock()
    entities = [MessageEntity(type="bot_command", offset=0, length=len(text))] if text.startswith("/") else None
    update = Update(update_id=index, message=Message(message_id=index, date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"), from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text, entities=entities))
    result = await dp.feed_update(bot, update)
    assert result is not UNHANDLED
    assert bot.session.called
    return bot.session.call_args.args[1]


async def test_today_concise_facts_no_repeated_tips(db_session, test_setup):
    """'Сегодня' shows concise facts: calories, macros, water, remaining, without repetitive coaching tips."""
    dp, bot = test_setup
    user = await make_user(db_session, 1001)
    user.language = "ru"
    user.target_calories = 2000
    user.target_protein = 150
    user.target_fat = 60
    user.target_carb = 200
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    # Log some food and water
    await crud.add_food_log(
        db_session, 1001,
        [{"name": "Лосось", "portion": "150г"}],
        calories=350, proteins=34.0, fats=18.0, carbs=0.0
    )
    await crud.add_water_log(db_session, 1001, 500)

    today_btn_text = i18n_locales.get_text("ux_today", "ru")
    res = await feed_message(dp, bot, today_btn_text, user_id=1001, index=1)

    # Check concise facts
    assert "350/2000" in res.text or ("350" in res.text and "2000" in res.text)
    assert "500" in res.text  # water
    assert "34" in res.text   # protein
    assert "1650" in res.text # remaining (2000 - 350)

    # Verify absence of repetitive coaching tips in 'Сегодня'
    for tip_key in ["ux_tip_balanced", "ux_tip_protein", "ux_tip_review", "ux_tip_weight"]:
        tip_phrase = i18n_locales.get_text(tip_key, "ru", cal_pct=17, protein_pct=22, goal="сохранить вес")
        # Ensure coaching tip sentence isn't present
        assert tip_phrase not in res.text

    # Verify reply markup is Today keyboard
    assert res.reply_markup == reply.get_today_keyboard("ru")


async def test_today_empty_state_concise_facts(db_session, test_setup):
    """'Сегодня' shows 0 meals and 0 ml cleanly without full coaching essay."""
    dp, bot = test_setup
    user = await make_user(db_session, 1002)
    user.language = "ru"
    user.target_calories = 2000
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    today_btn_text = i18n_locales.get_text("ux_today", "ru")
    res = await feed_message(dp, bot, today_btn_text, user_id=1002, index=2)

    assert "2000" in res.text
    assert "0" in res.text
    assert res.reply_markup == reply.get_today_keyboard("ru")


async def test_saved_legacy_report_details_strip_markdown(db_session, test_setup):
    user = await make_user(db_session, 9988)
    report_id = await crud.save_report_snapshot(db_session, 9988,
        '*Отчёт*\n```\n| Показатель | Факт |\n|---|---|\n| Калории | 350 |\n```',
        report_type='daily')
    message = SimpleNamespace(text=f"{i18n_locales.get_text('ux_details', 'ru')} · {report_id}", answer=AsyncMock())
    await common.view_report_details(message, 'ru', user)
    text = message.answer.call_args.args[0]
    assert '• Калории: Факт: 350' in text
    assert '*' not in text and '|' not in text and '`' not in text
    assert message.answer.call_args.kwargs['parse_mode'] is None
    callback = SimpleNamespace(data=f'ux:report:{report_id}', from_user=SimpleNamespace(id=9988),
        answer=AsyncMock(), bot=SimpleNamespace(send_message=AsyncMock()))
    await ux.report_details(callback, 'ru')
    assert callback.bot.send_message.call_args.args[1] == text


async def test_daily_report_on_request_detailed_breakdown(db_session, test_setup, monkeypatch):
    """Daily report triggers AI report on request and provides details snapshot."""
    dp, bot = test_setup
    user = await make_user(db_session, 1003)
    user.language = "ru"
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    await crud.add_food_log(
        db_session, 1003,
        [{"name": "Гречка с курицей", "portion": "250г"}],
        calories=450, proteins=35.0, fats=8.0, carbs=55.0
    )

    mock_gemini = AsyncMock(return_value="AI Анализ дня: Отличный баланс сложных углеводов и белка.")
    monkeypatch.setattr(gemini, "generate_report", mock_gemini)

    report_btn_text = i18n_locales.get_text("btn_daily_report", "ru")
    res = await feed_message(dp, bot, report_btn_text, user_id=1003, index=3)

    mock_gemini.assert_awaited_once()

    # The report summary was sent with get_report_keyboard
    assert any("Подробнее" in b.text for row in res.reply_markup.keyboard for b in row)

    # Find the details button text
    details_btn_text = next(b.text for row in res.reply_markup.keyboard for b in row if "Подробнее" in b.text)

    # Click Details button to view full AI breakdown
    details_res = await feed_message(dp, bot, details_btn_text, user_id=1003, index=4)
    assert "AI Анализ дня" in details_res.text


async def test_daily_report_empty_state_no_ai_call(db_session, test_setup, monkeypatch):
    """Daily report with no meals sends concise empty notice without calling Gemini."""
    dp, bot = test_setup
    user = await make_user(db_session, 1004)
    user.language = "ru"
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    mock_gemini = AsyncMock(return_value="Report text")
    monkeypatch.setattr(gemini, "generate_report", mock_gemini)

    report_btn_text = i18n_locales.get_text("btn_daily_report", "ru")
    res = await feed_message(dp, bot, report_btn_text, user_id=1004, index=5)

    # Gemini was not called
    mock_gemini.assert_not_awaited()

    # Sent ux_empty notice
    assert i18n_locales.get_text("ux_empty", "ru") in res.text


async def test_daily_report_snapshot_caching_and_new_entry_invalidation(db_session, test_setup, monkeypatch):
    """Repeat clicks without new data reuse existing snapshot (0 AI calls); new entry regenerates report."""
    dp, bot = test_setup
    clock = [100.0]
    monkeypatch.setattr(scheduler, 'monotonic', lambda: clock[0])
    user = await make_user(db_session, 1005)
    user.language = "ru"
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    # 1. Log breakfast
    await crud.add_food_log(
        db_session, 1005,
        [{"name": "Овсянка", "portion": "200г"}],
        calories=250, proteins=10.0, fats=5.0, carbs=40.0
    )

    mock_gemini = AsyncMock(return_value="Анализ завтрака")
    monkeypatch.setattr(gemini, "generate_report", mock_gemini)

    report_btn_text = i18n_locales.get_text("btn_daily_report", "ru")

    # First request: generates via Gemini
    res1 = await feed_message(dp, bot, report_btn_text, user_id=1005, index=10)
    assert mock_gemini.await_count == 1
    report1_btn = next(b.text for row in res1.reply_markup.keyboard for b in row if "Подробнее" in b.text)

    # Second request: no new data logged -> reuses cached snapshot!
    clock[0] += 31
    res2 = await feed_message(dp, bot, report_btn_text, user_id=1005, index=11)
    assert mock_gemini.await_count == 1  # Still 1! No extra AI task created!
    report2_btn = next(b.text for row in res2.reply_markup.keyboard for b in row if "Подробнее" in b.text)
    assert report1_btn == report2_btn  # Same durable snapshot ID!

    # 2. Now user logs lunch!
    await crud.add_food_log(
        db_session, 1005,
        [{"name": "Куриный суп", "portion": "300г"}],
        calories=350, proteins=25.0, fats=12.0, carbs=20.0
    )

    # Third request: new data logged -> fresh report generated with new data!
    mock_gemini.return_value = "Анализ завтрака и обеда"
    res3 = await feed_message(dp, bot, report_btn_text, user_id=1005, index=12)
    assert mock_gemini.await_count == 2  # Incremented to 2! Real new actions not suppressed!
    report3_btn = next(b.text for row in res3.reply_markup.keyboard for b in row if "Подробнее" in b.text)
    assert report3_btn != report1_btn  # New snapshot ID!


async def test_today_and_daily_report_across_all_7_languages(db_session, test_setup, monkeypatch):
    """Verify 'Сегодня' and 'Дневной отчет' work cleanly across all 7 languages."""
    dp, bot = test_setup
    mock_gemini = AsyncMock(return_value="Localized report")
    monkeypatch.setattr(gemini, "generate_report", mock_gemini)

    languages = ["en", "ru", "uk", "pl", "de", "tr", "es"]
    for i, lang in enumerate(languages):
        uid = 2000 + i
        user = await make_user(db_session, uid)
        user.language = lang
        await db_session.commit()

        async def context(handler, event, data, l=lang, u=user):
            data.update(db_user=u, user_language=l)
            return await handler(event, data)
        dp.message.outer_middleware(context)

        # 1. Test Сегодня
        today_text = i18n_locales.get_text("ux_today", lang)
        res_today = await feed_message(dp, bot, today_text, user_id=uid, index=100 + i)
        assert res_today.reply_markup == reply.get_today_keyboard(lang)

        # 2. Test Дневной отчет (empty state)
        report_text = i18n_locales.get_text("btn_daily_report", lang)
        res_report = await feed_message(dp, bot, report_text, user_id=uid, index=200 + i)
        assert i18n_locales.get_text("ux_empty", lang) in res_report.text


async def test_daily_report_concurrent_clicks_in_flight_deduplicated(db_session, test_setup, monkeypatch):
    """Concurrent/rapid clicks while report is generating drop duplicate requests without multiple Gemini calls."""
    import asyncio
    dp, bot = test_setup
    user = await make_user(db_session, 3001)
    user.language = "ru"
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    await crud.add_food_log(
        db_session, 3001,
        [{"name": "Яблоко", "portion": "1 шт"}],
        calories=80, proteins=0.5, fats=0.3, carbs=19.0
    )

    # Simulate Gemini taking a short delay during report generation
    async def slow_generate_report(*args, **kwargs):
        await asyncio.sleep(0.05)
        return "Отчет готов"

    mock_gemini = AsyncMock(side_effect=slow_generate_report)
    monkeypatch.setattr(gemini, "generate_report", mock_gemini)

    report_btn_text = i18n_locales.get_text("btn_daily_report", "ru")

    # Launch two concurrent requests (rapid double-tap)
    t1 = asyncio.create_task(feed_message(dp, bot, report_btn_text, user_id=3001, index=301))
    t2 = asyncio.create_task(feed_message(dp, bot, report_btn_text, user_id=3001, index=302))

    await asyncio.gather(t1, t2)

    # Gemini was called exactly ONCE, not twice!
    assert mock_gemini.await_count == 1


async def test_today_concurrent_clicks_in_flight_deduplicated(db_session, test_setup, monkeypatch):
    """Concurrent/rapid clicks on Сегодня drop duplicate requests."""
    import asyncio
    dp, bot = test_setup
    user = await make_user(db_session, 3002)
    user.language = "ru"
    await db_session.commit()

    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)

    today_btn_text = i18n_locales.get_text("ux_today", "ru")

    # Make today_data simulate a tiny delay to test concurrent in-flight
    orig_today_data = ux.ux.today_data
    async def slow_today_data(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await orig_today_data(*args, **kwargs)
    monkeypatch.setattr(ux.ux, "today_data", slow_today_data)

    bot.session.reset_mock()
    t1 = asyncio.create_task(dp.feed_update(bot, Update(update_id=401, message=Message(message_id=401, date=datetime.now(UTC),
        chat=Chat(id=3002, type="private"), from_user=User(id=3002, is_bot=False, first_name="Test"), text=today_btn_text))))
    t2 = asyncio.create_task(dp.feed_update(bot, Update(update_id=402, message=Message(message_id=402, date=datetime.now(UTC),
        chat=Chat(id=3002, type="private"), from_user=User(id=3002, is_bot=False, first_name="Test"), text=today_btn_text))))

    await asyncio.gather(t1, t2)

    # Exactly 1 response sent by bot across both rapid concurrent clicks!
    assert bot.session.call_count == 1
