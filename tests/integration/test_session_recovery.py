from datetime import datetime, UTC
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Update, Message, User, Chat, MessageEntity
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from src.database import crud
from src.handlers import common, food, admin, profile, weight, callbacks
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS


@pytest.mark.asyncio
async def test_restart_stale_keyboard_and_recovery_commands_through_dispatcher(db_session):
    user = await make_user(db_session)
    draft_id = await crud.save_meal_draft(db_session, 123,
        {"analysis": ANALYSIS, "logged_at": datetime.now(UTC).isoformat()})
    dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    async def context(handler, event, data):
        data.update(db_user=user, user_language="ru")
        return await handler(event, data)
    dp.message.outer_middleware(context)
    for router in [common.recovery_router, admin.router, profile.router, food.router,
                   weight.router, callbacks.router, common.router]:
        dp.include_router(router)
    bot = Bot("123456:test")
    bot.session = AsyncMock()
    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)

    async def send(text, index):
        bot.session.reset_mock()
        entities = [MessageEntity(type="bot_command", offset=0, length=len(text))] if text.startswith("/") else None
        update = Update(update_id=index, message=Message(message_id=index, date=datetime.now(UTC),
            chat=Chat(id=123, type="private"), from_user=User(id=123, is_bot=False, first_name="Test"),
            text=text, entities=entities))
        result = await dp.feed_update(bot, update)
        assert result is not UNHANDLED
        assert bot.session.called
        return bot.session.call_args.args[1]

    # Exact old reply buttons seen in the screenshot, with empty MemoryStorage.
    for index, text in enumerate(["✅ Принять", "❌ Отмена", "Завтрак", "✅ Принять"], 1):
        response = await send(text, index)
        assert "📥 Неподтверждённая еда" in response.text
        assert response.reply_markup.keyboard  # Main menu, replacing the old keyboard.
        assert await crud.get_meal_draft(db_session, draft_id, 123) is not None

    response = await send("📥 Неподтверждённая еда", 8)
    assert response.reply_markup.inline_keyboard[0][0].callback_data == f"meal_draft:accept:{draft_id}"

    # Commands take precedence over state-specific catch-all handlers.
    for index, command in enumerate(["/start", "/cancel"], 10):
        await state.set_state(food.FoodLoggingState.waiting_for_confirm)
        await state.update_data(analysis={"stale": True})
        await send(command, index)
        assert await state.get_state() is None
        assert await state.get_data() == {}

    # A partially lost confirmation state must recover, not throw KeyError.
    await state.set_state(food.FoodLoggingState.waiting_for_confirm)
    await send("✅ Принять", 20)
    assert await state.get_state() is None
    assert await crud.get_meal_draft(db_session, draft_id, 123) is not None
    await dp.storage.close()
    await dp.fsm.events_isolation.close()
