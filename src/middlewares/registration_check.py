from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

class RegistrationCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        db_user = data.get("db_user")
        from src.database import crud
        if db_user and crud.is_user_deleted(db_user):
            from src.database.connection import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await crud.delete_user(db, db_user.telegram_id)
            db_user = None
            data["db_user"] = None
        
        # If user is registered and not blocked, allow normal execution
        if db_user and not db_user.is_blocked:
            return await handler(event, data)
            
        # Configured admins can process requests even before completing a profile.
        from src.config import settings
        actor = data.get("event_from_user") or getattr(event, "from_user", None)
        words = (event.text or "").split() if isinstance(event, Message) else []
        command = words[0].split("@")[0] if words else ""
        from src.utils import i18n_locales
        access_labels = set()
        for key in ('btn_access_menu', 'btn_access_pending', 'btn_access_back',
                    'btn_auto_approval_on', 'btn_auto_approval_off'):
            access_labels.update(i18n_locales.get_all_translations(key))
        from src.keyboards.reply import is_access_button
        is_counted_access_button = isinstance(event, Message) and any(
            is_access_button(event.text, key) for key in ("btn_access_menu", "btn_access_pending"))
        is_access_action = (
            is_counted_access_button or command in ("/approve", "/reject", "/pending", "/admin")
            or (isinstance(event, Message) and event.text in access_labels | {"👑 Admin Panel", "👑 Админ-панель"})
            or (isinstance(event, CallbackQuery) and (getattr(event, "data", None) or "").startswith("accessreq:"))
        )
        if actor and actor.id in settings.ADMIN_USER_IDS and not (db_user and db_user.is_blocked) and is_access_action:
            return await handler(event, data)

        # Check FSM state to see if they are in the setup flow
        state = data.get("state")
        current_state = await state.get_state() if state else None
        is_in_setup = current_state is not None and current_state.startswith("ProfileStatesGroup:")
        
        # Check if the command is /start
        is_start_cmd = False
        if isinstance(event, Message) and event.text:
            text_strip = event.text.strip()
            if text_strip and text_strip.split()[0] == "/start":
                is_start_cmd = True
            
        if is_in_setup or is_start_cmd:
            return await handler(event, data)
            
        # Otherwise, block the message and redirect to setup
        from src.handlers.profile import start_profile_setup
        user_lang = data.get("user_language", "en")
        if isinstance(event, Message):
            await start_profile_setup(event, state, user_lang, db_user=None)
        elif isinstance(event, CallbackQuery):
            if event.message:
                await start_profile_setup(event.message, state, user_lang, db_user=None)
            await event.answer()
            
        return
