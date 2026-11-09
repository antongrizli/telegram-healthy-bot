"""Shared "found machines -> Add all / Cancel" confirmation used by equipment settings and goal gap filling."""
from __future__ import annotations

import contextlib
import html
import logging
from typing import Any, Dict, List, Literal

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.database.connection import AsyncSessionLocal
from src.database.models import User
from src.handlers.training.keyboards import make_cb
from src.services.training import library
from src.services.training.catalog import get_catalog, normalize_catalog_string
from src.services.training.plan_context import load_plan_context
from src.utils.training_locales import t

logger = logging.getLogger(__name__)
router = Router(name="training_machines_confirm_router")

ReturnTo = Literal["equipment", "plan"]


def dedupe_against_library(names: List[str], library_items: List[Dict[str, Any]]) -> List[str]:
    """Drop names already in the library (same normalized name or same catalog entry) and repeats."""
    catalog = get_catalog()
    known_names = {normalize_catalog_string(i.get("display_name") or "") for i in library_items}
    known_keys = {i.get("catalog_key") for i in library_items if i.get("catalog_key")}
    result: List[str] = []
    seen = set()
    for name in names:
        norm = normalize_catalog_string(name)
        if not norm or norm in known_names or norm in seen:
            continue
        entry = catalog.find_by_name(name)
        if entry and entry["key"] in known_keys:
            continue
        seen.add(norm)
        result.append(name)
    return result


async def propose_machines(message: Message, state: FSMContext, names: List[str], return_to: ReturnTo) -> None:
    """Show found machines and ask for confirmation. Nothing is written before the user confirms."""
    await state.update_data(eq_ai_candidates=names, eq_ai_return=return_to)
    items = "\n".join(f"• {html.escape(n)}" for n in names)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("eq_ai_add_all"), callback_data=make_cb("eq_ai_ok", 0, 0)),
        InlineKeyboardButton(text=t("eq_ai_cancel"), callback_data=make_cb("eq_ai_no", 0, 0)),
    ]])
    await message.answer(t("eq_ai_found", items=items), parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("t2:eq_ai_ok:"))
async def handle_ai_machines_confirm(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    data = await state.get_data()
    names = data.get("eq_ai_candidates") or []
    return_to = data.get("eq_ai_return") or "equipment"
    await state.update_data(eq_ai_candidates=None, eq_ai_return=None)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.edit_reply_markup(reply_markup=None)
    if not names:
        await callback.message.answer(t("eq_ai_expired"))
        return

    async with AsyncSessionLocal() as db:
        user = await db.get(User, uid)
        lang = user.language if user and user.language else "ru"
        ctx = await load_plan_context(db, uid)
        fresh = dedupe_against_library(names, ctx.library)
        for name in fresh:
            await library.get_or_create_canonical(db, user_id=uid, name=name, tag_with_llm=True, lang=lang)
        await db.commit()

    feedback = t("eq_ai_added", count=len(fresh))
    if return_to == "plan":
        from src.handlers.training.plan import rebuild_current_plan

        await rebuild_current_plan(callback, state, feedback=feedback)
    else:
        from src.handlers.training.settings import render_equipment_settings

        await render_equipment_settings(callback, state, user_id=uid, feedback=feedback)


@router.callback_query(F.data.startswith("t2:eq_ai_no:"))
async def handle_ai_machines_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(eq_ai_candidates=None, eq_ai_return=None)
    with contextlib.suppress(TelegramBadRequest):
        await callback.message.delete()
