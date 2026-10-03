from datetime import datetime, UTC
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import Update, Message, User, Chat, MessageEntity, ReplyKeyboardMarkup
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from src.handlers import common, food, admin, profile, weight, callbacks, ux, medications
from src.keyboards import reply
from src.config import settings
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
    return [call.args[1] for call in bot.session.call_args_list]


@pytest.mark.asyncio
async def test_ux08_more_section_and_settings_in_all_languages(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)

    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    state = dp.fsm.get_context(bot=bot, chat_id=123, user_id=123)
    idx = 10

    for lang in ["en", "ru", "uk", "pl", "de", "tr", "es"]:
        current_lang = lang
        user.language = lang
        await db_session.commit()

        # Step 1: Open "More" (ux_more) from main menu
        idx += 1
        more_btn = i18n_locales.get_text("ux_more", lang)
        responses = await feed_message(dp, bot, more_btn, index=idx)

        # STRICT CRITERIA: Exactly ONE response when opening More (no second inline message)
        assert len(responses) == 1
        msg = responses[0]
        assert msg.text == i18n_locales.get_text("ux_more", lang)

        # Check section keyboard structure:
        # Row 1: Profile | Settings
        # Row 2: Medications | Help
        # Row 3: Back to main menu
        kb = msg.reply_markup
        assert isinstance(kb, ReplyKeyboardMarkup)
        assert [[b.text for b in row] for row in kb.keyboard] == [
            [i18n_locales.get_text("btn_my_profile", lang), i18n_locales.get_text("btn_settings", lang)],
            [i18n_locales.get_text("btn_medications", lang), i18n_locales.get_text("btn_help", lang)],
            [i18n_locales.get_text("ux_back", lang)],
        ]

        # Step 2: Click "Settings" (btn_settings) in reply menu
        idx += 1
        settings_btn = i18n_locales.get_text("btn_settings", lang)
        responses = await feed_message(dp, bot, settings_btn, index=idx)

        # Check that MenuButtonWebApp is configured with ?panel=settings
        set_menu_call = next(r for r in responses if r.__api_method__ == "setChatMenuButton")
        assert set_menu_call.menu_button.web_app.url == f"{settings.WEBAPP_URL}?panel=settings"
        assert set_menu_call.menu_button.text == settings_btn

        # Check instruction message
        msg_call = next(r for r in responses if r.__api_method__ == "sendMessage")
        assert msg_call.text == i18n_locales.get_text("ux_open_menu", lang, section=settings_btn)

        # Step 3: Back returns to main menu
        idx += 1
        back_btn = i18n_locales.get_text("ux_back", lang)
        responses = await feed_message(dp, bot, back_btn, index=idx)
        assert len(responses) == 1
        assert responses[0].text == i18n_locales.get_text("return_to_main_menu", lang)
        assert responses[0].reply_markup == reply.get_main_menu(lang, is_admin=False)


@pytest.mark.asyncio
async def test_ux09_progress_section_and_charts_in_all_languages(db_session, test_setup):
    dp, bot = test_setup
    user = await make_user(db_session, 123)

    current_lang = "ru"
    async def context(handler, event, data):
        data.update(db_user=user, user_language=current_lang)
        return await handler(event, data)
    dp.message.outer_middleware(context)

    idx = 100

    for lang in ["en", "ru", "uk", "pl", "de", "tr", "es"]:
        current_lang = lang
        user.language = lang
        await db_session.commit()

        # Step 1: Open "Progress" (ux_progress) from main menu
        idx += 1
        progress_btn = i18n_locales.get_text("ux_progress", lang)
        responses = await feed_message(dp, bot, progress_btn, index=idx)

        # Exactly ONE response when opening Progress section
        assert len(responses) == 1
        msg = responses[0]
        assert msg.text == progress_btn

        # Verify NO second "ux_progress" button inside section keyboard!
        kb = msg.reply_markup
        assert isinstance(kb, ReplyKeyboardMarkup)
        keyboard_texts = [[b.text for b in row] for row in kb.keyboard]
        assert progress_btn not in [b for row in keyboard_texts for b in row]

        # Verify exact 2x2 + Back layout
        assert keyboard_texts == [
            [i18n_locales.get_text("btn_charts", lang), i18n_locales.get_text("btn_all_achievements", lang)],
            [i18n_locales.get_text("btn_view_card", lang), i18n_locales.get_text("btn_weekly_report", lang)],
            [i18n_locales.get_text("ux_back", lang)],
        ]

        # Step 2: Click "Charts" (btn_charts)
        idx += 1
        charts_btn = i18n_locales.get_text("btn_charts", lang)
        responses = await feed_message(dp, bot, charts_btn, index=idx)
        set_menu_call = next(r for r in responses if r.__api_method__ == "setChatMenuButton")
        assert set_menu_call.menu_button.web_app.url == f"{settings.WEBAPP_URL}?tab=charts"
        assert set_menu_call.menu_button.text == charts_btn

        # Step 3: Click "Achievements" (btn_all_achievements)
        idx += 1
        ach_btn = i18n_locales.get_text("btn_all_achievements", lang)
        responses = await feed_message(dp, bot, ach_btn, index=idx)
        set_menu_call = next(r for r in responses if r.__api_method__ == "setChatMenuButton")
        assert set_menu_call.menu_button.web_app.url == f"{settings.WEBAPP_URL}?tab=achievements"
        assert set_menu_call.menu_button.text == ach_btn

        # Step 4: Click "Health card" (btn_view_card)
        idx += 1
        card_btn = i18n_locales.get_text("btn_view_card", lang)
        responses = await feed_message(dp, bot, card_btn, index=idx)
        set_menu_call = next(r for r in responses if r.__api_method__ == "setChatMenuButton")
        assert set_menu_call.menu_button.web_app.url == f"{settings.WEBAPP_URL}?tab=health-card"
        assert set_menu_call.menu_button.text == card_btn

        # Step 5: Back returns cleanly to main menu
        idx += 1
        back_btn = i18n_locales.get_text("ux_back", lang)
        responses = await feed_message(dp, bot, back_btn, index=idx)
        assert len(responses) == 1
        assert responses[0].text == i18n_locales.get_text("return_to_main_menu", lang)
        assert responses[0].reply_markup == reply.get_main_menu(lang, is_admin=False)
