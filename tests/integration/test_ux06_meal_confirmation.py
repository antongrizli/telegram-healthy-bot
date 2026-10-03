from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, UTC
import pytest
from sqlalchemy import select
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import ReplyKeyboardMarkup

from src.database import crud
from src.database.models import FoodLog
from src.handlers.food import handle_meal_draft, process_food_confirm, FoodLoggingState
from src.utils import i18n_locales
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS


@pytest.mark.asyncio
@pytest.mark.parametrize('accept', [True, False])
async def test_legacy_confirmation_after_restart_finalizes_persisted_card(db_session, accept):
    user = await make_user(db_session)
    did = await crud.save_meal_draft(db_session, 123, {
        'analysis': ANALYSIS, 'logged_at': datetime.now(UTC).isoformat(),
        'card_message_id': 777, 'meal_type': 'breakfast'})
    state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=123, user_id=123))
    await state.set_state(FoodLoggingState.waiting_for_confirm)
    await state.update_data(draft_id=did)
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123),
        bot=bot, answer=AsyncMock(), text=i18n_locales.get_text('btn_accept' if accept else 'btn_cancel', 'ru'))
    await process_food_confirm(message, state, 'ru', user)
    kwargs = bot.edit_message_text.call_args.kwargs
    assert kwargs['message_id'] == 777
    assert kwargs['reply_markup'] is None
    assert '?' not in kwargs['text']
    assert ('записан' if accept else 'отмен') in kwargs['text'].lower()


@pytest.mark.asyncio
async def test_format_food_logged_all_7_languages():
    locales = ["en", "ru", "uk", "pl", "de", "tr", "es"]
    meal_types = ["breakfast", "lunch", "dinner", "snack", "food"]
    for lang in locales:
        for mt in meal_types:
            text = i18n_locales.format_food_logged(mt, 350.6, lang)
            assert "351" in text, f"Calories rounding failed in {lang} {mt}: {text}"
            assert text.startswith("✅"), f"Must start with checkmark: {text}"
            assert "·" in text, f"Must have separator dot: {text}"


@pytest.mark.asyncio
async def test_strip_food_confirmation_question_all_7_languages():
    for lang in i18n_locales.LOCALES:
        raw_card = i18n_locales.get_text(
            "food_analysis_result", lang,
            items="- Oats (50g)\n- Milk (100ml)",
            calories=250, protein=12, fat=6, carb=35
        )
        assert "?" in raw_card or "¿" in raw_card
        cleaned = i18n_locales.strip_food_confirmation_question(raw_card)
        assert "?" not in cleaned
        assert "¿" not in cleaned
        assert "Oats" in cleaned
        assert "250" in cleaned


@pytest.mark.asyncio
async def test_handle_meal_draft_accept_single_message_and_cleans_card(db_session):
    user = await make_user(db_session)
    draft_id = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": {**ANALYSIS, "total_calories": 420},
            "meal_type": "breakfast",
            "logged_at": datetime.now(UTC).isoformat()
        }
    )
    raw_card_text = i18n_locales.get_text(
        "food_analysis_result", "ru",
        items="- Яичница (2 шт)",
        calories=420, protein=20, fat=15, carb=2
    ) + "\n🍳 Завтрак\n⚠️ Оценка приблизительная"

    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=123, user_id=123))
    mock_message = SimpleNamespace(
        text=raw_card_text,
        caption=None,
        answer=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock()
    )
    callback = SimpleNamespace(
        data=f"meal_draft:accept:{draft_id}",
        from_user=SimpleNamespace(id=123),
        answer=AsyncMock(),
        message=mock_message
    )

    await handle_meal_draft(callback, state, "ru", user)

    # 1. Card text was cleaned of question and active inline markup removed
    mock_message.edit_text.assert_called_once()
    cleaned_card_call = mock_message.edit_text.call_args
    assert "?" not in cleaned_card_call.args[0]
    assert cleaned_card_call.kwargs.get("reply_markup") is None

    # 2. Exactly ONE response message sent
    assert mock_message.answer.call_count == 1
    answer_call = mock_message.answer.call_args
    assert answer_call.args[0] == "✅ Завтрак записан · 420 ккал"
    assert isinstance(answer_call.kwargs.get("reply_markup"), ReplyKeyboardMarkup)

    # 3. Exactly one food log entry in DB
    food_logs = (await db_session.execute(select(FoodLog))).scalars().all()
    assert len(food_logs) == 1
    assert food_logs[0].meal_type == "breakfast"
    assert food_logs[0].calories == 420

    # 4. Repeated press on same finished draft returns draft_unavailable alert and no extra messages
    await handle_meal_draft(callback, state, "ru", user)
    assert mock_message.answer.call_count == 1  # Still 1, no second message!
    assert callback.answer.call_args.kwargs.get("show_alert") is True
    assert len((await db_session.execute(select(FoodLog))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_process_food_confirm_legacy_button_single_message(db_session):
    user = await make_user(db_session)
    draft_id = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": {**ANALYSIS, "total_calories": 550},
            "meal_type": "lunch",
            "logged_at": datetime.now(UTC).isoformat()
        }
    )

    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=123, user_id=123))
    await state.set_state(FoodLoggingState.waiting_for_confirm)
    await state.update_data(draft_id=draft_id, meal_type="lunch")

    mock_msg = SimpleNamespace(
        text="✅ Принять",
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=123),
        bot=SimpleNamespace(edit_message_reply_markup=AsyncMock()),
        answer=AsyncMock()
    )

    await process_food_confirm(mock_msg, state, "ru", user)

    # Exactly ONE response message sent with single compact confirmation
    assert mock_msg.answer.call_count == 1
    answer_call = mock_msg.answer.call_args
    assert answer_call.args[0] == "✅ Обед записан · 550 ккал"
    assert isinstance(answer_call.kwargs.get("reply_markup"), ReplyKeyboardMarkup)

    # State cleared
    assert await state.get_state() is None

    # DB contains the logged lunch
    food_logs = (await db_session.execute(select(FoodLog))).scalars().all()
    assert len(food_logs) == 1
    assert food_logs[0].meal_type == "lunch"
    assert food_logs[0].calories == 550
