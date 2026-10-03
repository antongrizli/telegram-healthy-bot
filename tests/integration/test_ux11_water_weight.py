from datetime import datetime, UTC
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Update, Message, User, Chat, MessageEntity
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from src.database import crud
from src.handlers import common, food, admin, profile, weight, callbacks, ux, medications
from src.keyboards import reply
from src.utils import i18n_locales
from tests.integration.test_security_and_meal_recovery import make_user


@pytest.fixture
def test_setup(db_session, monkeypatch):
    class Session:
        async def __aenter__(self): return db_session
        async def __aexit__(self, *args): pass
    for mod in [medications, food, ux, profile, weight, common]:
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


@pytest.mark.asyncio
async def test_ux11_water_logging_in_all_languages(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    expected_examples = {
        "en": "✅ Water +250 ml · today 250 ml",
        "ru": "✅ Вода +250 мл · сегодня 250 мл",
        "uk": "✅ Вода +250 мл · сьогодні 250 мл",
        "pl": "✅ Woda +250 ml · dzisiaj 250 ml",
        "de": "✅ Wasser +250 ml · heute 250 ml",
        "tr": "✅ Su +250 ml · bugün 250 ml",
        "es": "✅ Agua +250 ml · hoy 250 ml",
    }

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)
    idx = 10
    total_water = 0

    for lang in ["en", "ru", "uk", "pl", "de", "tr", "es"]:
        current_lang = lang
        user.language = lang
        await db_session.commit()

        # Step 1: Set state to WaterState.amount
        await state.set_state(ux.WaterState.amount)

        # Step 2: Feed water amount (250 ml on first iteration, 100 ml on subsequent)
        amount = 250 if lang == "en" else 100
        total_water += amount
        idx += 1
        res = await feed_message(dp, bot, str(amount), index=idx)

        # Assert only 1 message sent
        assert bot.session.call_args_list is not None
        assert len(bot.session.call_args_list) == 1

        # Assert format matches exactly
        expected_text = i18n_locales.format_water_logged(amount, total_water, lang)
        assert res.text == expected_text
        if lang in expected_examples and total_water == 250:
            assert res.text == expected_examples[lang]

        # Verify no long day summary (calories, macros, tips) in message
        assert "kcal" not in res.text
        assert "ккал" not in res.text
        assert "Protein" not in res.text
        assert "Белки" not in res.text
        assert "ux_saved" not in res.text

        # Assert keyboard is main menu
        expected_kb = reply.get_main_menu(lang, is_admin=False)
        assert [[b.text for b in row] for row in res.reply_markup.keyboard] == [
            [b.text for b in row] for row in expected_kb.keyboard
        ]

        # Assert state is cleared
        assert await state.get_state() is None


@pytest.mark.asyncio
async def test_ux11_weight_logging_in_all_languages(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    expected_78_5 = {
        "en": "✅ Weight 78.5 kg",
        "ru": "✅ Вес 78,5 кг",
        "uk": "✅ Вага 78,5 кг",
        "pl": "✅ Waga 78,5 kg",
        "de": "✅ Gewicht 78,5 kg",
        "tr": "✅ Kilo 78,5 kg",
        "es": "✅ Peso 78,5 kg",
    }

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)
    idx = 100

    for lang in ["en", "ru", "uk", "pl", "de", "tr", "es"]:
        current_lang = lang
        user.language = lang
        await db_session.commit()

        # Step 1: Set state to WeightState.waiting_for_weight
        await state.set_state(weight.WeightState.waiting_for_weight)

        # Step 2: Feed weight 78.5 (or comma 78,5)
        idx += 1
        input_str = "78,5" if lang in ("ru", "uk", "de") else "78.5"
        res = await feed_message(dp, bot, input_str, index=idx)

        # Assert only 1 message sent
        assert len(bot.session.call_args_list) == 1

        # Assert message matches UX-11 spec
        assert res.text == expected_78_5[lang]
        assert res.text == i18n_locales.format_weight_logged(78.5, lang)

        # Verify no motivational phrases or diff walls
        assert "📈" not in res.text
        assert "📉" not in res.text
        assert "Keep up" not in res.text
        assert "Продолжайте" not in res.text
        assert "набрали" not in res.text
        assert "сбросили" not in res.text
        assert "baseline" not in res.text

        # Assert keyboard is main menu
        expected_kb = reply.get_main_menu(lang, is_admin=False)
        assert [[b.text for b in row] for row in res.reply_markup.keyboard] == [
            [b.text for b in row] for row in expected_kb.keyboard
        ]

        # Assert state is cleared
        assert await state.get_state() is None

    # Step 3: Test integer weight formatting (e.g. 80 kg)
    current_lang = "ru"
    await state.set_state(weight.WeightState.waiting_for_weight)
    res_int_ru = await feed_message(dp, bot, "80", index=201)
    assert res_int_ru.text == "✅ Вес 80 кг"

    current_lang = "en"
    await state.set_state(weight.WeightState.waiting_for_weight)
    res_int_en = await feed_message(dp, bot, "80", index=202)
    assert res_int_en.text == "✅ Weight 80 kg"


@pytest.mark.asyncio
async def test_ux11_weight_logging_with_achievement(db_session, test_setup, monkeypatch):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    from src.services import gamification
    mock_check = AsyncMock(return_value=["weight_10"])
    monkeypatch.setattr(gamification, "check_new_achievements", mock_check)

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)
    await state.set_state(weight.WeightState.waiting_for_weight)

    res = await feed_message(dp, bot, "75.0", index=301)

    # Strictly single compact message sent matching food confirmation
    assert len(bot.session.call_args_list) == 1
    assert res.text == "✅ Вес 75 кг"
    # Gamification check was performed silently in DB
    mock_check.assert_awaited_once_with(db_session, 123)
    # Main menu keyboard returned
    assert res.reply_markup is not None
    # State cleared
    assert await state.get_state() is None


@pytest.mark.asyncio
async def test_ux11_invalid_inputs_remain_in_state(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)

    # 1. Invalid water input
    await state.set_state(ux.WaterState.amount)
    res_water = await feed_message(dp, bot, "not_a_number", index=401)
    assert res_water.text == i18n_locales.get_text("ux_invalid", "ru")
    assert await state.get_state() == ux.WaterState.amount

    # 2. Invalid weight input
    await state.set_state(weight.WeightState.waiting_for_weight)
    res_weight = await feed_message(dp, bot, "999", index=402)
    assert res_weight.text == i18n_locales.get_text("invalid_weight", "ru")
    assert await state.get_state() == weight.WeightState.waiting_for_weight


@pytest.mark.asyncio
async def test_ux11_full_stats_remain_in_today_button(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    user.language = "ru"
    await db_session.commit()
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    # Log some water first
    await crud.add_water_log(db_session, 123, 500)

    # Press "📊 Сегодня"
    today_btn_text = i18n_locales.get_text("ux_today", "ru")
    res = await feed_message(dp, bot, today_btn_text, index=501)

    # Verify that today's summary still includes targets, water, and full info
    assert "500" in res.text
    assert "ккал" in res.text
    assert res.reply_markup == reply.get_today_keyboard("ru")
