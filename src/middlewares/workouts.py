"""Leaving text input preserves the workout draft but cancels the input prompt."""
from aiogram import BaseMiddleware
from sqlalchemy import select, update

from src.database.connection import AsyncSessionLocal
from src.database.models import User, Workout
from src.utils.i18n_locales import LOCALES


class WorkoutNavigationMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        text = event.text or ''
        labels = {v for locale in LOCALES.values() for k, v in locale.items()
                  if k.startswith(('btn_', 'ux_')) and isinstance(v, str) and '{' not in v}
        if text in labels or text.startswith('/'):
            async with AsyncSessionLocal() as db:
                user = await db.get(User, event.from_user.id)
                if user and (user.ux_preferences or {}).get('workout_budget_input'):
                    user.ux_preferences = {**user.ux_preferences, 'workout_budget_input': False}
                if user and (user.ux_preferences or {}).get('daily_workout_input'):
                    user.ux_preferences = {**user.ux_preferences, 'daily_workout_input': None}
                if user and (user.ux_preferences or {}).get('workout_frequency_input'):
                    user.ux_preferences = {**user.ux_preferences, 'workout_frequency_input': False}
                # Done is the parser lease's confirmation action; preserve its revision/state
                # so duplicate taps cannot start concurrent model calls.
                done_labels = {v for locale in LOCALES.values() for k, v in locale.items()
                               if k == 'btn_workout_done' and isinstance(v, str)}
                if text not in done_labels:
                    rows = (await db.scalars(select(Workout).where(Workout.user_id == event.from_user.id)
                                             .order_by(Workout.id.desc()).limit(2))).all()
                    for workout in rows:
                        if workout.pending and workout.pending.get('mode') not in ('manual_entries', 'collecting', 'parsing', 'needs_clarification'):
                            await db.execute(update(Workout).where(
                                Workout.id == workout.id, Workout.revision == workout.revision
                            ).values(pending={}, revision=Workout.revision + 1))
                await db.commit()
        return await handler(event, data)
