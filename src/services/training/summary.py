"""Workout summary calculation: volume, PR detection, progression targets and session comparison."""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.utils.training_locales import t
from src.database.models import (
    CanonicalExercise,
    WorkoutSession,
    WorkoutSessionEntry,
    WorkoutSet,
)
from src.services.training.dto import ExerciseHistory, ParsedSet, SessionSummary
from src.services.training.progression import calculate_e1rm, next_target


def format_weight(w: Optional[float]) -> str:
    """Format weight without trailing zeros: 35.0 -> '35', 42.5 -> '42.5'."""
    if w is None or w == 0:
        return "0"
    val = float(w)
    if val.is_integer():
        return str(int(val))
    return f"{val:.1f}"


async def build_session_summary(
    db: AsyncSession,
    session: WorkoutSession,
) -> SessionSummary:
    """Build summary metrics for a completed workout session."""
    # Ensure entries and sets are loaded
    stmt = (
        select(WorkoutSessionEntry)
        .options(
            selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSessionEntry.session_id == session.id)
        .order_by(WorkoutSessionEntry.position.asc())
    )
    entries = (await db.scalars(stmt)).all()

    started = session.started_at or session.performed_at
    finished = session.finished_at or session.performed_at
    if started and finished:
        duration_mins = max(1, int((finished - started).total_seconds() / 60))
    else:
        duration_mins = 45

    total_volume_kg = 0.0
    total_sets = 0
    records: List[str] = []
    exercise_summaries: List[Dict[str, Any]] = []

    for entry in entries:
        ex = entry.canonical_exercise
        ex_name = ex.display_name if ex else "Exercise"
        working_sets = [s for s in entry.workout_sets if s.set_type == "working"]
        if not working_sets and entry.workout_sets:
            working_sets = entry.workout_sets

        n_sets = len(working_sets)
        total_sets += n_sets

        # Calculate volume for this exercise
        ex_vol = 0.0
        reps_list = []
        weights_list = []
        best_e1rm = 0.0

        for ws in working_sets:
            w = float(ws.weight_kg) if ws.weight_kg is not None else 0.0
            r = ws.reps or 0
            if w > 0 and r > 0:
                ex_vol += w * r
                weights_list.append(w)
                reps_list.append(r)
                e1 = calculate_e1rm(w, r)
                if e1 > best_e1rm:
                    best_e1rm = e1

        total_volume_kg += ex_vol

        # Check PR against past history
        if ex and best_e1rm > 0:
            past_stmt = (
                select(WorkoutSet)
                .join(WorkoutSessionEntry, WorkoutSet.entry_id == WorkoutSessionEntry.id)
                .join(WorkoutSession, WorkoutSessionEntry.session_id == WorkoutSession.id)
                .where(
                    WorkoutSession.user_id == session.user_id,
                    WorkoutSession.status == "finished",
                    WorkoutSession.id != session.id,
                    WorkoutSessionEntry.canonical_exercise_id == ex.id,
                )
            )
            past_sets = (await db.scalars(past_stmt)).all()
            past_best_e1rm = 0.0
            for ps in past_sets:
                pw = float(ps.weight_kg) if ps.weight_kg is not None else 0.0
                pr = ps.reps or 0
                if pw > 0 and pr > 0:
                    pe1 = calculate_e1rm(pw, pr)
                    if pe1 > past_best_e1rm:
                        past_best_e1rm = pe1

            if past_best_e1rm > 0 and best_e1rm > past_best_e1rm:
                records.append(t("sum_record_line", name=ex_name, new=best_e1rm, old=past_best_e1rm))

        # Next recommended target for next time
        top_w = max(weights_list) if weights_list else 0.0
        med_r = int(statistics.median(reps_list)) if reps_list else 0
        h_obj = ExerciseHistory(
            exercise_id=ex.id if ex else 0,
            name=ex_name,
            top_weight=top_w,
            working_sets=[ParsedSet(weight_kg=s.weight_kg, reps=s.reps) for s in working_sets],
            median_reps=med_r,
            feeling=entry.feeling,
            days_ago=0,
        )
        target = next_target(
            h_obj,
            pattern=ex.pattern if ex else None,
            goal="hypertrophy",
            weight_step=float(ex.weight_step) if ex and ex.weight_step else 2.5,
        )

        feeling_icon = "🟢" if entry.feeling == "easy" else ("🔴" if entry.feeling == "hard" else "🟡")
        if target.weight and target.weight > top_w:
            target_str = f"🎯 {format_weight(target.weight)} × {target.reps}"
        elif target.weight:
            target_str = t("sum_hold", w=format_weight(target.weight), r=target.reps)
        else:
            target_str = t("sum_reps_only", r=target.reps)

        load_str = f"{n_sets}×{med_r} @ {format_weight(top_w)}" if top_w > 0 else f"{n_sets}×{med_r}"
        exercise_summaries.append({
            "name": ex_name,
            "load_str": load_str,
            "feeling_icon": feeling_icon,
            "target_str": target_str,
        })

    # Compare volume with previous session
    vol_diff_pct = None
    prev_stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
        )
        .where(
            WorkoutSession.user_id == session.user_id,
            WorkoutSession.status == "finished",
            WorkoutSession.id != session.id,
        )
        .order_by(WorkoutSession.performed_at.desc())
        .limit(1)
    )
    prev_session = (await db.scalars(prev_stmt)).first()
    if prev_session:
        prev_vol = 0.0
        for pe in prev_session.entries:
            for ps in pe.workout_sets:
                pw = float(ps.weight_kg) if ps.weight_kg is not None else 0.0
                pr = ps.reps or 0
                if pw > 0 and pr > 0:
                    prev_vol += pw * pr
        if prev_vol > 0:
            vol_diff_pct = round(((total_volume_kg - prev_vol) / prev_vol) * 100.0, 1)

    return SessionSummary(
        session_id=session.id,
        duration_minutes=duration_mins,
        exercises_count=len(entries),
        sets_count=total_sets,
        total_volume_kg=round(total_volume_kg, 1),
        volume_diff_pct=vol_diff_pct,
        records=records,
        exercise_summaries=exercise_summaries,
    )
