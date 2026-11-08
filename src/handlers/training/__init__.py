"""Training v2 handlers package and router aggregation."""
from aiogram import BaseMiddleware, Router

from src.handlers.training import (
    hub,
    live,
    log_text,
    plan,
    progress,
    repeat,
    settings,
)



class TrainingLangMiddleware(BaseMiddleware):
    """Resolve the user's language once per update so cards and keyboards render in it (see training_locales)."""

    async def __call__(self, handler, event, data):
        from sqlalchemy import select

        from src.database.connection import AsyncSessionLocal
        from src.database.models import User
        from src.utils.training_locales import current_lang, norm_lang

        from_user = getattr(event, "from_user", None)
        lang = norm_lang(getattr(from_user, "language_code", None))
        if from_user is not None:
            try:
                async with AsyncSessionLocal() as db:
                    stored = await db.scalar(select(User.language).where(User.telegram_id == from_user.id))
                if stored:
                    lang = norm_lang(stored)
            except Exception:  # language lookup must never block a button press
                pass
        token = current_lang.set(lang)
        try:
            return await handler(event, data)
        finally:
            current_lang.reset(token)


router = Router(name="training_router")
router.message.outer_middleware(TrainingLangMiddleware())
router.callback_query.outer_middleware(TrainingLangMiddleware())
router.include_router(hub.router)
router.include_router(log_text.router)
router.include_router(live.router)
router.include_router(plan.router)
router.include_router(repeat.router)
router.include_router(settings.router)
router.include_router(progress.router)

__all__ = ["router"]
