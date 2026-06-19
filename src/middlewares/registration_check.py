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
        
        # If user is registered and not blocked, allow normal execution
        if db_user and not db_user.is_blocked:
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
