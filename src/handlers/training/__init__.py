"""Training v2 handlers package and router aggregation."""
from aiogram import Router

from src.handlers.training import (
    hub,
    live,
    log_text,
    plan,
    progress,
    repeat,
    settings,
)

router = Router(name="training_router")
router.include_router(hub.router)
router.include_router(log_text.router)
router.include_router(live.router)
router.include_router(plan.router)
router.include_router(repeat.router)
router.include_router(settings.router)
router.include_router(progress.router)

__all__ = ["router"]
