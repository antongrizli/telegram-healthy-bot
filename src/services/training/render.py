"""HTML card templates and formatters for Telegram messages."""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

from src.services.training.dto import DraftItem, PlanItem, SessionSummary


def _fmt_w(w: Any) -> str:
    """Format weight without trailing zeros: 42.0 -> 42, 42.5 -> 42.5."""
    if w is None:
        return "0"
    try:
        val = float(w)
    except (ValueError, TypeError):
        return str(w)
    if val == 0:
        return "0"
    if val.is_integer():
        return str(int(val))
    return f"{val:.1f}"


def render_hub(
    last_session_str: str,
    weekly_count: int,
    weekly_target: int,
    recommendation_str: Optional[str] = None,
    is_active: bool = False,
    active_exercise: str = "",
    active_progress: str = "",
) -> str:
    """Render main training hub card."""
    lines = [
        "🏋️ <b>Тренировки</b>",
        f"Последняя: {html.escape(last_session_str)}",
        f"На этой неделе: <b>{weekly_count} из {weekly_target}</b>",
    ]
    if recommendation_str:
        lines.append(f"💡 <i>Сегодня рекомендую:</i> {html.escape(recommendation_str)}")
    if is_active:
        lines.append(f"\n▶️ <b>Текущая тренировка:</b> {html.escape(active_exercise)} · {active_progress}")
    return "\n".join(lines)


def render_goal_picker(
    selected_muscles: List[str],
    minutes: int = 45,
    energy: str = "normal",
    understood_note: Optional[str] = None,
) -> str:
    """Render '🎯 Другая цель' screen."""
    lines = [
        "🎯 <b>Что тренируем сегодня?</b>",
        "Нажмите группы мышц или напишите своими словами",
        "<i>(например: «упор на спину, без нагрузки на поясницу»).</i>\n",
    ]
    if understood_note:
        lines.append(f"<b>Понял:</b> {html.escape(understood_note)}\n")
    return "\n".join(lines)


def render_plan_card(
    plan_items: List[PlanItem],
    focus_name: str,
    minutes: int,
    rationale: str = "",
) -> str:
    """Render workout plan card with expandable biomechanical cues."""
    lines = [
        f"📋 <b>План · {html.escape(focus_name)} · ≈{minutes} мин · {len(plan_items)} упр.</b>",
    ]
    if rationale:
        lines.append(f"<i>Почему так:</i> {html.escape(rationale)}\n")

    cues_lines = []
    for idx, item in enumerate(plan_items, 1):
        if item.kind == "cardio":
            dur_min = int((item.working[0].duration_sec or 480) / 60) if item.working else 8
            lines.append(f"{idx}. <b>{html.escape(item.name)}</b> — {dur_min} мин разминка")
        else:
            w_count = len(item.working)
            first_w = item.working[0] if item.working else None
            w_str = _fmt_w(first_w.weight_kg) if first_w and first_w.weight_kg else ""
            reps_str = str(first_w.reps) if first_w and first_w.reps else ""

            load_part = f"{w_count}×{reps_str}" if reps_str else f"{w_count} подходов"
            if w_str and w_str != "0":
                load_part += f" @ {w_str} кг"

            reason_part = f"   <i>({html.escape(item.reason)})</i>" if item.reason else ""
            lines.append(f"{idx}. <b>{html.escape(item.name)}</b> — {load_part}{reason_part}")

            if item.warmup:
                wm = item.warmup[0]
                wm_w = _fmt_w(wm.weight_kg)
                wm_r = wm.reps or 12
                lines.append(f"   разминка 1×{wm_r} @ {wm_w} кг")

        if item.cue:
            cues_lines.append(f"• <b>{html.escape(item.name)}:</b> {html.escape(item.cue)}")

    if cues_lines:
        lines.append("\n<blockquote expandable>💡 <b>Подсказки по технике:</b>\n" + "\n".join(cues_lines) + "</blockquote>")

    return "\n".join(lines)


def render_draft_card(
    items: List[DraftItem],
    feeling: str = "normal",
) -> str:
    """Render parsed workout draft card with line-by-line validation status."""
    lines = [
        f"📝 <b>Черновик · {len(items)} упражнений</b>\n",
    ]

    for item in items:
        status_icon = "✅"
        if item.status == "ambiguous":
            status_icon = "❓"
        elif item.status == "new_name":
            status_icon = "🆕"
        elif item.status == "unparsed":
            status_icon = "❓"

        if item.status == "unparsed":
            lines.append(f"{status_icon} <i>Не распознано:</i> {html.escape(item.raw)}")
            continue

        if item.kind == "cardio":
            dur_min = int((item.sets[0].duration_sec or 0) / 60) if item.sets else 0
            dist_km = (item.sets[0].distance_m or 0) / 1000.0 if item.sets else 0
            parts = []
            if dur_min > 0:
                parts.append(f"{dur_min} мин")
            if dist_km > 0:
                parts.append(f"{dist_km:.1f} км")
            if item.note:
                parts.append(html.escape(item.note))
            lines.append(f"{status_icon} <b>{html.escape(item.name)}</b> — {' · '.join(parts)}")
        elif item.kind == "bodyweight":
            n_sets = len(item.sets)
            reps = item.sets[0].reps if item.sets else 0
            add_w = item.sets[0].added_weight_kg if item.sets else None
            load_str = f"{n_sets}×{reps}"
            if add_w:
                load_str += f" (+{_fmt_w(add_w)} кг)"
            note_str = f" · {html.escape(item.note)}" if item.note else ""
            lines.append(f"{status_icon} <b>{html.escape(item.name)}</b> — {load_str}{note_str}")
        else:
            # Strength
            n_sets = len(item.sets)
            reps = item.sets[0].reps if item.sets else 0
            w = item.sets[0].weight_kg if item.sets else 0
            w_str = _fmt_w(w)
            load_str = f"{n_sets}×{reps} @ {w_str} кг" if w_str != "0" else f"{n_sets}×{reps}"
            note_str = f" · {html.escape(item.note)}" if item.note else ""
            lines.append(f"{status_icon} <b>{html.escape(item.name)}</b> — {load_str}{note_str}")

    return "\n".join(lines)


def render_live_card(
    workout_name: str,
    exercise_pos: int,
    total_exercises: int,
    duration_str: str,
    exercise_name: str,
    set_idx: int,
    total_sets: int,
    target_weight: Optional[float],
    target_reps: Optional[int],
    completed_sets: List[Dict[str, Any]],
    last_session_str: str = "",
    machine_settings: Optional[str] = None,
) -> str:
    """Render live logging card (Scenario A)."""
    lines = [
        f"▶️ <b>{html.escape(workout_name)} · {exercise_pos}/{total_exercises} · ⏱ {duration_str}</b>",
        f"<b>{html.escape(exercise_name)}</b> — подход {set_idx} из {total_sets}",
    ]
    if machine_settings:
        lines.append(f"⚙️ <i>{html.escape(machine_settings)}</i>")

    target_w_str = _fmt_w(target_weight)
    target_str = f"🎯 {target_w_str} кг × {target_reps}" if target_weight else f"🎯 {target_reps} повт"
    if last_session_str:
        target_str += f"      прошлый раз: {html.escape(last_session_str)}"
    lines.append(target_str)

    if completed_sets:
        lines.append("")
        for cs in completed_sets:
            idx = cs.get("idx", 1)
            w = _fmt_w(cs.get("weight_kg"))
            r = cs.get("reps", 0)
            lines.append(f"✔ {idx}) {w} × {r}")

    return "\n".join(lines)


def render_summary_card(summary: SessionSummary) -> str:
    """Render post-workout summary card."""
    lines = [
        f"🏁 <b>Тренировка сохранена · {summary.duration_minutes} мин · {summary.exercises_count} упр. · {summary.sets_count} подходов</b>",
    ]
    vol_str = f"{int(summary.total_volume_kg):,} кг".replace(",", " ")
    diff_str = ""
    if summary.volume_diff_pct is not None:
        sign = "+" if summary.volume_diff_pct >= 0 else ""
        diff_str = f" ({sign}{summary.volume_diff_pct}% к прошлой)"
    lines.append(f"Объём: <b>{vol_str}</b>{diff_str}\n")

    if summary.records:
        lines.append("🏆 <b>Рекорды</b>")
        for rec in summary.records:
            lines.append(f"• {html.escape(rec)}")
        lines.append("")

    if summary.exercise_summaries:
        lines.append("📊 <b>По упражнениям</b>")
        for es in summary.exercise_summaries:
            name = es["name"]
            load = es["load_str"]
            feel = es["feeling_icon"]
            target = es["target_str"]
            lines.append(f"• {html.escape(name)} — {load} {feel} → {target}")

    if summary.ai_focus_next:
        lines.append(f"\n💡 <i>Фокус на следующий раз:</i> {html.escape(summary.ai_focus_next)}")

    return "\n".join(lines)
