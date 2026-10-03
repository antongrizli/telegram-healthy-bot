"""Deliver a durable draft to its current card, with safe replacement semantics."""
import logging
from aiogram.exceptions import TelegramAPIError
from src.database import crud
from src.utils.telegram_edit import try_edit

logger = logging.getLogger(__name__)


async def finalize_card(message, payload, language, accept):
    """Best-effort UI cleanup after the database transaction has succeeded."""
    from src.handlers.food import format_draft_card_text
    from src.utils import i18n_locales
    text = i18n_locales.strip_food_confirmation_question(format_draft_card_text(payload, language))
    status = (i18n_locales.format_food_logged(payload.get('meal_type', 'food'),
        payload['analysis']['total_calories'], language) if accept
        else i18n_locales.get_text('food_cancelled', language))
    text += '\n\n' + status
    card_id = payload.get('card_message_id')
    try:
        if isinstance(card_id, int) and hasattr(message, 'bot') and hasattr(message.bot, 'edit_message_text'):
            await try_edit(message.bot.edit_message_text, chat_id=message.chat.id,
                message_id=card_id, text=text, reply_markup=None, parse_mode=None)
        elif hasattr(message, 'edit_text'):
            await try_edit(message.edit_text, text, reply_markup=None, parse_mode=None)
    except TelegramAPIError:
        logger.warning('Could not finalize meal card; database result remains saved')


async def deliver_card(bot, db, draft, chat_id, language, status_message_id=None):
    from src.handlers.food import format_draft_card_text, get_draft_keyboard
    target = draft.payload.get('card_message_id') or status_message_id
    kwargs = dict(text=format_draft_card_text(draft.payload, language),
                  reply_markup=get_draft_keyboard(draft.id, language, draft.payload.get('draft_page', 1)),
                  parse_mode=None)
    edited = False
    if isinstance(target, int):
        edited = await try_edit(bot.edit_message_text, chat_id=chat_id, message_id=target, **kwargs)
    if not edited:
        sent = await bot.send_message(chat_id, **kwargs)
        target = sent.message_id
    if isinstance(target, int):
        await crud.save_meal_draft(db, draft.user_id,
            {**draft.payload, 'card_message_id': target}, draft.id)
    if isinstance(status_message_id, int) and status_message_id != target:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=status_message_id)
        except TelegramAPIError:
            logger.info('Could not remove auxiliary meal status message')
    return target
