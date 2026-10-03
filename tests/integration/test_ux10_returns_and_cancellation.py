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
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS


@pytest.fixture
def test_setup(db_session, monkeypatch):
    class Session:
        async def __aenter__(self): return db_session
        async def __aexit__(self, *args): pass
    for mod in [medications, food, ux, profile, weight, common]:
        if hasattr(mod, 'AsyncSessionLocal'):
            monkeypatch.setattr(mod, 'AsyncSessionLocal', Session)
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
async def test_ux10_back_to_main_menu_clears_all_states_and_preserves_drafts(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    draft_id = await crud.save_meal_draft(db_session, 123,
        {"analysis": ANALYSIS, "logged_at": datetime.now(UTC).isoformat()})
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)

    states_to_test = [
        (food.FoodLoggingState.waiting_for_input, {}),
        (food.FoodLoggingState.waiting_for_confirm, {"draft_id": draft_id}),
        (food.FoodLoggingState.waiting_for_correction, {"draft_id": draft_id}),
        (food.MealEditingState.waiting_for_edit_text, {"draft_id": draft_id}),
        (food.LocalCorrectionState.value, {"draft_id": draft_id, "correction_action": "portion"}),
        (weight.WeightState.waiting_for_weight, {}),
        (ux.WaterState.amount, {}),
        (profile.ProfileStatesGroup.age, {}),
        (profile.ProfileStatesGroup.confirm_delete, {}),
        (medications.MedicationSetup.name, {"med_category": "medicine"}),
    ]

    for lang in i18n_locales.LOCALES:
        current_lang = lang
        back_text = i18n_locales.get_text('ux_back', lang)
        for s_idx, (st, data) in enumerate(states_to_test):
            await state.set_state(st)
            await state.set_data(data)

            idx = 1000 + hash(lang) % 1000 + s_idx
            res = await feed_message(dp, bot, back_text, index=idx)
            
            # Response must be simple return to main menu
            assert res.text == i18n_locales.get_text('return_to_main_menu', lang)
            assert res.reply_markup == reply.get_main_menu(lang, user.is_admin)
            
            # FSM state must be cleared
            assert await state.get_state() is None
            assert await state.get_data() == {}
            
            # Draft must be preserved
            d = await crud.get_meal_draft(db_session, draft_id, 123)
            assert d is not None

    await dp.storage.close()
    await dp.fsm.events_isolation.close()


@pytest.mark.asyncio
async def test_ux10_cancelling_input_preserves_drafts_and_returns_clean_messages(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)
    draft_id = await crud.save_meal_draft(db_session, 123,
        {"analysis": ANALYSIS, "logged_at": datetime.now(UTC).isoformat()})
    
    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)

    # 1. Food input cancel preserves draft
    await state.set_state(food.FoodLoggingState.waiting_for_input)
    res = await feed_message(dp, bot, "❌ Отмена", index=1)
    assert res.text == i18n_locales.get_text("food_cancelled", "ru")
    assert await state.get_state() is None
    assert await crud.get_meal_draft(db_session, draft_id, 123) is not None

    # 2. Local correction cancel preserves draft
    await state.set_state(food.LocalCorrectionState.value)
    await state.set_data({"draft_id": draft_id, "correction_action": "portion"})
    res = await feed_message(dp, bot, "❌ Отмена", index=2)
    assert res.text == i18n_locales.get_text("food_cancelled", "ru")
    assert await state.get_state() is None
    assert await crud.get_meal_draft(db_session, draft_id, 123) is not None

    # 3. Weight cancel returns weight_cancelled
    await state.set_state(weight.WeightState.waiting_for_weight)
    res = await feed_message(dp, bot, "❌ Отмена", index=3)
    assert res.text == i18n_locales.get_text("weight_cancelled", "ru")
    assert await state.get_state() is None

    # Weight command cancel
    await state.set_state(weight.WeightState.waiting_for_weight)
    res = await feed_message(dp, bot, "/cancel", index=4)
    assert res.text == i18n_locales.get_text("weight_cancelled", "ru")
    assert await state.get_state() is None

    # 4. Water cancel returns water_cancelled
    await state.set_state(ux.WaterState.amount)
    res = await feed_message(dp, bot, "❌ Отмена", index=5)
    assert res.text == i18n_locales.get_text("water_cancelled", "ru")
    assert await state.get_state() is None

    # Water command cancel
    await state.set_state(ux.WaterState.amount)
    res = await feed_message(dp, bot, "/cancel", index=6)
    assert res.text == i18n_locales.get_text("water_cancelled", "ru")
    assert await state.get_state() is None

    # 5. Profile setup cancel
    await state.set_state(profile.ProfileStatesGroup.age)
    res = await feed_message(dp, bot, "❌ Отмена", index=7)
    assert res.text == i18n_locales.get_text("profile_setup_cancelled", "ru")
    assert await state.get_state() is None

    # 6. Profile delete cancel
    await state.set_state(profile.ProfileStatesGroup.confirm_delete)
    res = await feed_message(dp, bot, "❌ Отмена", index=8)
    assert res.text == i18n_locales.get_text("ux_delete_cancelled", "ru")
    assert await state.get_state() is None

    # 7. Medication setup cancel
    await state.set_state(medications.MedicationSetup.name)
    res = await feed_message(dp, bot, "❌ Отмена", index=9)
    assert res.text == i18n_locales.get_text("action_cancelled", "ru")
    assert await state.get_state() is None

    # 8. Command cancel in food confirmation exits state while preserving draft
    await state.set_state(food.FoodLoggingState.waiting_for_confirm)
    await state.set_data({"draft_id": draft_id})
    res = await feed_message(dp, bot, "/cancel", index=10)
    assert res.text == i18n_locales.get_text("food_cancelled", "ru")
    assert await state.get_state() is None
    assert await crud.get_meal_draft(db_session, draft_id, 123) is not None

    # 9. In confirmation, clicking reply "❌ Отмена" explicitly finishes/rejects that draft
    await state.set_state(food.FoodLoggingState.waiting_for_confirm)
    await state.set_data({"draft_id": draft_id})
    res = await feed_message(dp, bot, "❌ Отмена", index=11)
    assert res.text == i18n_locales.get_text("food_cancelled", "ru")
    assert await state.get_state() is None
    # Now the draft is marked finished (no longer pending)
    assert await crud.get_meal_draft(db_session, draft_id, 123) is None

    await dp.storage.close()
    await dp.fsm.events_isolation.close()


@pytest.mark.asyncio
async def test_ux10_recovery_message_never_contains_food_logging_instruction():
    for lang in i18n_locales.LOCALES:
        rec_text = i18n_locales.get_text("session_recovery", lang)
        food_btn = i18n_locales.get_text("btn_log_food", lang)
        pending_btn = i18n_locales.get_text("btn_pending_meals", lang)
        
        # Pending meals path must be in recovery
        assert pending_btn in rec_text
        # Food prompt / instructions must NOT be in recovery
        assert food_btn not in rec_text
        assert "фото" not in rec_text
        assert "photo" not in rec_text
