"""Training progress, history view, and WebApp integration."""
from __future__ import annotations

import html
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from src.config import settings
from src.database.connection import AsyncSessionLocal
from src.handlers.training.keyboards import make_cb
from src.services.training import history

logger = logging = __import__("logging").getLogger(__name__)
router = Router(name="training_progress_router")


@router.callback_query(F.data.startswith("t2:progress"))
async def show_training_progress(callback: CallbackQuery, state: FSMContext):
    """Render workout history, personal records and WebApp charts button."""
    await callback.answer()
    uid = callback.from_user.id

    async with AsyncSessionLocal() as db:
        weekly_count = await history.get_user_weekly_workout_count(db, uid)
        last_session = await history.get_last_finished_session(db, uid)

    lines = [
        "📈 <b>История и прогресс тренировок</b>\n",
        f"🔥 Тренировок на этой неделе: <b>{weekly_count}</b>",
    ]

    if last_session and last_session.entries:
        date_str = last_session.performed_at.strftime("%d.%m.%Y") if last_session.performed_at else ""
        lines.append(f"\n<b>Последняя тренировка ({date_str}):</b>")
        for entry in last_session.entries[:6]:
            ex_name = entry.canonical_exercise.display_name if entry.canonical_exercise else entry.raw_input
            w_sets = [s for s in entry.workout_sets if s.set_type == "working"] or entry.workout_sets
            n_sets = len(w_sets)
            w = w_sets[0].weight_kg if w_sets and w_sets[0].weight_kg else 0
            r = w_sets[0].reps if w_sets and w_sets[0].reps else 0
            dur = w_sets[0].duration_sec if w_sets and w_sets[0].duration_sec else 0

            if dur:
                lines.append(f"• {html.escape(ex_name)}: {int(dur/60)} мин")
            elif w:
                w_str = f"{w:.1f}".rstrip("0").rstrip(".")
                lines.append(f"• {html.escape(ex_name)}: {n_sets}×{r} @ {w_str} кг")
            else:
                lines.append(f"• {html.escape(ex_name)}: {n_sets}×{r}")

    text = "\n".join(lines)
    rows = []
    if settings.WEBAPP_URL:
        from aiogram.types import WebAppInfo
        rows.append([InlineKeyboardButton(text="📊 Открыть графики в WebApp", web_app=WebAppInfo(url=f"{settings.WEBAPP_URL}?tab=workouts"))])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data=make_cb("hub", 0, 0))])

    if callback.message:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
