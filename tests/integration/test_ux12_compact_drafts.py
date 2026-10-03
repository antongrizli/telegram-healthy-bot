from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, UTC
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from src.database import crud
from src.handlers import food, ux
from src.utils import i18n_locales
from tests.integration.test_security_and_meal_recovery import make_user, ANALYSIS


@pytest.mark.asyncio
async def test_database_pagination_includes_drafts_after_twentieth(db_session):
    await make_user(db_session)
    await make_user(db_session, 456)
    ids = []
    for _ in range(23):
        ids.append(await crud.save_meal_draft(db_session, 123,
            {'analysis': ANALYSIS, 'logged_at': datetime.now(UTC).isoformat()}))
    await crud.save_meal_draft(db_session, 456, {'analysis': ANALYSIS})
    rows, total, page, pages = await crud.get_pending_meals_page(db_session, 123, 999)
    assert (total, page, pages) == (23, 5, 5)
    assert [row.id for row in rows] == ids[20:]
    message = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock())
    await food.show_pending_meals(message, 'ru', page=5)
    assert '(23 · 5/5)' in message.answer.call_args.args[0]
    buttons = message.answer.call_args.kwargs['reply_markup'].inline_keyboard[0]
    assert [b.text for b in buttons] == ['21', '22', '23']
    for did in ids[20:]:
        await crud.finish_meal_draft(db_session, did, 123, accept=False)
    _, total, page, pages = await crud.get_pending_meals_page(db_session, 123, 5)
    assert (total, page, pages) == (20, 4, 4)


@pytest.mark.asyncio
async def test_empty_drafts_shows_single_short_message(db_session):
    user = await make_user(db_session)
    for lang in ("ru", "en", "uk", "pl", "de", "tr", "es"):
        mock_msg = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            chat=SimpleNamespace(id=123),
            answer=AsyncMock()
        )
        await food.show_pending_meals(mock_msg, lang)
        mock_msg.answer.assert_called_once()
        sent_text = mock_msg.answer.call_args.args[0]
        assert sent_text == i18n_locales.get_text("no_pending_meals", lang)


@pytest.mark.asyncio
async def test_compact_drafts_list_and_card_navigation(db_session):
    user = await make_user(db_session)
    # Create 3 drafts
    draft_ids = []
    for i in range(1, 4):
        analysis = {
            "food_items": [{"name": f"Продукт {i}", "weight_grams": 100 * i, "calories": 150 * i, "protein": 10, "fat": 5, "carb": 20}],
            "total_calories": 150 * i,
            "total_protein": 10,
            "total_fat": 5,
            "total_carb": 20
        }
        did = await crud.save_meal_draft(
            db_session, 123,
            {
                "analysis": analysis,
                "meal_type": "breakfast" if i == 1 else ("lunch" if i == 2 else "dinner"),
                "logged_at": datetime.now(UTC).isoformat()
            }
        )
        draft_ids.append(did)

    mock_msg = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=123),
        answer=AsyncMock()
    )

    # Step 1: Open drafts list
    await food.show_pending_meals(mock_msg, "ru")
    # Exactly 1 message sent
    assert mock_msg.answer.call_count == 1
    sent_text = mock_msg.answer.call_args.args[0]
    sent_markup = mock_msg.answer.call_args.kwargs.get("reply_markup")

    assert "📥 Черновики еды (3)" in sent_text
    assert "1. 🍳 Завтрак · 150 ккал" in sent_text
    assert "2. 🍲 Обед · 300 ккал" in sent_text
    assert "3. 🍝 Ужин · 450 ккал" in sent_text
    assert "Выберите черновик для просмотра и подтверждения:" in sent_text

    # Selection row: 3 buttons, followed by close button
    assert len(sent_markup.inline_keyboard) == 2
    row0 = sent_markup.inline_keyboard[0]
    assert len(row0) == 3
    assert row0[0].text == "1"
    assert row0[0].callback_data == f"uxdraft:view:{draft_ids[0]}:1"
    assert row0[1].text == "2"
    assert row0[1].callback_data == f"uxdraft:view:{draft_ids[1]}:1"
    assert row0[2].text == "3"
    assert row0[2].callback_data == f"uxdraft:view:{draft_ids[2]}:1"

    close_btn = sent_markup.inline_keyboard[1][0]
    assert close_btn.text == "❌ Закрыть"
    assert close_btn.callback_data == "uxdraft:close"

    # Step 2: Click draft #2 to view card in-place
    card_msg = SimpleNamespace(
        message_id=555,
        text=sent_text,
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock()
    )
    cb_view = SimpleNamespace(
        data=f"uxdraft:view:{draft_ids[1]}:1",
        from_user=SimpleNamespace(id=123),
        message=card_msg,
        answer=AsyncMock()
    )
    await food.uxdraft_view_card(cb_view, "ru")
    card_msg.edit_text.assert_called_once()
    card_text = card_msg.edit_text.call_args.args[0]
    card_kb = card_msg.edit_text.call_args.kwargs.get("reply_markup")

    assert "300" in card_text
    assert "Обед" in card_text
    # Card keyboard has Accept/Correct/Cancel, Type, and Back to drafts
    assert len(card_kb.inline_keyboard) == 3
    assert card_kb.inline_keyboard[0][0].callback_data == f"meal_draft:accept:{draft_ids[1]}"
    assert card_kb.inline_keyboard[1][0].callback_data == f"uxdraft:type:{draft_ids[1]}"
    assert card_kb.inline_keyboard[2][0].text == "◀️ К списку черновиков"
    assert card_kb.inline_keyboard[2][0].callback_data == "uxdraft:list:1"

    # Step 3: Return back to drafts list
    card_msg.edit_text.reset_mock()
    cb_back_list = SimpleNamespace(
        data="uxdraft:list:1",
        from_user=SimpleNamespace(id=123),
        message=card_msg,
        answer=AsyncMock()
    )
    await food.uxdraft_show_list(cb_back_list, "ru")
    card_msg.edit_text.assert_called_once()
    assert "📥 Черновики еды (3)" in card_msg.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_pagination_with_more_than_five_drafts(db_session):
    user = await make_user(db_session)
    # Create 7 drafts
    draft_ids = []
    for i in range(1, 8):
        analysis = {
            "food_items": [{"name": f"Блюдо {i}", "weight_grams": 100, "calories": 100 * i, "protein": 10, "fat": 5, "carb": 15}],
            "total_calories": 100 * i,
            "total_protein": 10,
            "total_fat": 5,
            "total_carb": 15
        }
        did = await crud.save_meal_draft(
            db_session, 123,
            {
                "analysis": analysis,
                "meal_type": "food",
                "logged_at": datetime.now(UTC).isoformat()
            }
        )
        draft_ids.append(did)

    mock_msg = SimpleNamespace(
        from_user=SimpleNamespace(id=123),
        chat=SimpleNamespace(id=123),
        answer=AsyncMock()
    )

    # Page 1
    await food.show_pending_meals(mock_msg, "ru")
    sent_text = mock_msg.answer.call_args.args[0]
    sent_markup = mock_msg.answer.call_args.kwargs.get("reply_markup")

    assert "📥 Черновики еды (7 · 1/2)" in sent_text
    assert "1. 🍽️ Другое · 100 ккал" in sent_text
    assert "5. 🍽️ Другое · 500 ккал" in sent_text
    assert "6. 🍽️ Другое" not in sent_text

    # Selection row has 5 items
    assert len(sent_markup.inline_keyboard[0]) == 5
    # Pagination row
    nav_row = sent_markup.inline_keyboard[1]
    assert len(nav_row) == 3
    assert nav_row[0].text == "⬅️"
    assert nav_row[0].callback_data == "uxdraft:noop"  # page 1 disabled prev
    assert nav_row[1].text == "1/2"
    assert nav_row[2].text == "➡️"
    assert nav_row[2].callback_data == "uxdraft:list:2"

    # Navigate to Page 2
    list_msg = SimpleNamespace(
        message_id=666,
        edit_text=AsyncMock(),
        answer=AsyncMock()
    )
    cb_page2 = SimpleNamespace(
        data="uxdraft:list:2",
        from_user=SimpleNamespace(id=123),
        message=list_msg,
        answer=AsyncMock()
    )
    await food.uxdraft_show_list(cb_page2, "ru")
    list_msg.edit_text.assert_called_once()
    page2_text = list_msg.edit_text.call_args.args[0]
    page2_markup = list_msg.edit_text.call_args.kwargs.get("reply_markup")

    assert "📥 Черновики еды (7 · 2/2)" in page2_text
    assert "6. 🍽️ Другое · 600 ккал" in page2_text
    assert "7. 🍽️ Другое · 700 ккал" in page2_text
    assert len(page2_markup.inline_keyboard[0]) == 2
    assert page2_markup.inline_keyboard[0][0].text == "6"
    assert page2_markup.inline_keyboard[0][0].callback_data == f"uxdraft:view:{draft_ids[5]}:2"
    assert page2_markup.inline_keyboard[0][1].text == "7"
    assert page2_markup.inline_keyboard[0][1].callback_data == f"uxdraft:view:{draft_ids[6]}:2"

    # Page 2 navigation: left is active, right is disabled
    nav_row2 = page2_markup.inline_keyboard[1]
    assert nav_row2[0].callback_data == "uxdraft:list:1"
    assert nav_row2[1].text == "2/2"
    assert nav_row2[2].callback_data == "uxdraft:noop"


@pytest.mark.asyncio
async def test_close_and_noop_callbacks():
    # Test close callback
    msg = SimpleNamespace(
        delete=AsyncMock(),
        edit_reply_markup=AsyncMock()
    )
    cb_close = SimpleNamespace(
        data="uxdraft:close",
        message=msg,
        answer=AsyncMock()
    )
    await food.uxdraft_close(cb_close)
    cb_close.answer.assert_called_once()
    msg.delete.assert_called_once()

    # Test noop callback
    cb_noop = SimpleNamespace(
        data="uxdraft:noop",
        answer=AsyncMock()
    )
    await food.uxdraft_noop(cb_noop)
    cb_noop.answer.assert_called_once()


@pytest.mark.asyncio
async def test_all_seven_languages_drafts_locale_consistency(db_session):
    user = await make_user(db_session)
    analysis = {
        "food_items": [{"name": "Item", "weight_grams": 100, "calories": 200, "protein": 10, "fat": 5, "carb": 15}],
        "total_calories": 200,
        "total_protein": 10,
        "total_fat": 5,
        "total_carb": 15
    }
    did = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": analysis,
            "meal_type": "breakfast",
            "logged_at": datetime.now(UTC).isoformat()
        }
    )

    for lang in ("ru", "en", "uk", "pl", "de", "tr", "es"):
        btn_label = i18n_locales.get_text("btn_pending_meals", lang)
        title = i18n_locales.get_text("food_drafts_title", lang)
        prompt = i18n_locales.get_text("food_drafts_select_prompt", lang)
        back_label = i18n_locales.get_text("food_drafts_back_to_list", lang)
        close_label = i18n_locales.get_text("food_drafts_close", lang)

        assert btn_label != "btn_pending_meals"
        assert title != "food_drafts_title"
        assert prompt != "food_drafts_select_prompt"
        assert back_label != "food_drafts_back_to_list"
        assert close_label != "food_drafts_close"

        mock_msg = SimpleNamespace(
            from_user=SimpleNamespace(id=123),
            chat=SimpleNamespace(id=123),
            answer=AsyncMock()
        )
        await food.show_pending_meals(mock_msg, lang)
        text = mock_msg.answer.call_args.args[0]
        markup = mock_msg.answer.call_args.kwargs.get("reply_markup")

        assert title in text
        assert prompt in text
        assert markup.inline_keyboard[-1][0].text == close_label


@pytest.mark.asyncio
async def test_accept_and_cancel_from_card_opened_via_compact_list(db_session):
    user = await make_user(db_session)
    storage = MemoryStorage()
    state = FSMContext(storage, StorageKey(bot_id=1, chat_id=123, user_id=123))

    did = await crud.save_meal_draft(
        db_session, 123,
        {
            "analysis": ANALYSIS,
            "meal_type": "breakfast",
            "logged_at": datetime.now(UTC).isoformat()
        }
    )

    card_msg = SimpleNamespace(
        message_id=999,
        text="Draft text",
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        answer=AsyncMock()
    )

    # View card
    cb_view = SimpleNamespace(
        data=f"uxdraft:view:{did}:1",
        from_user=SimpleNamespace(id=123),
        message=card_msg,
        answer=AsyncMock()
    )
    await food.uxdraft_view_card(cb_view, "ru")
    assert card_msg.edit_text.call_count == 1

    # Accept card
    cb_accept = SimpleNamespace(
        data=f"meal_draft:accept:{did}",
        from_user=SimpleNamespace(id=123),
        message=card_msg,
        answer=AsyncMock()
    )
    await food.handle_meal_draft(cb_accept, state, "ru", user)
    # Buttons removed from card
    card_msg.edit_text.assert_called()
    # Confirmation sent
    assert card_msg.answer.call_count == 1
    assert "✅ Завтрак записан" in card_msg.answer.call_args.args[0]

    # After accept, opening list shows no pending meals
    list_msg = SimpleNamespace(
        message_id=1000,
        edit_text=AsyncMock(),
        answer=AsyncMock()
    )
    cb_list = SimpleNamespace(
        data="uxdraft:list:1",
        from_user=SimpleNamespace(id=123),
        message=list_msg,
        answer=AsyncMock()
    )
    await food.uxdraft_show_list(cb_list, "ru")
    list_msg.edit_text.assert_called_once()
    assert list_msg.edit_text.call_args.args[0] == i18n_locales.get_text("no_pending_meals", "ru")
