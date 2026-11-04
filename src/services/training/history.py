"""Exercise history, e1RM records, weekly volume and recovery statistics."""
from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    CanonicalExercise,
    WorkoutSession,
    WorkoutSessionEntry,
    WorkoutSet,
)
from src.services.training.dto import ExerciseHistory, ParsedSet
from src.services.training.progression import calculate_e1rm


async def get_exercise_history(
    db: AsyncSession,
    user_id: int,
    canonical_id: int,
) -> Optional[ExerciseHistory]:
    """Retrieve history for a given exercise including last session's sets and e1RM."""
    # Find all entries for this exercise in finished sessions
    stmt = (
        select(WorkoutSessionEntry)
        .join(WorkoutSession, WorkoutSessionEntry.session_id == WorkoutSession.id)
        .options(selectinload(WorkoutSessionEntry.workout_sets), selectinload(WorkoutSessionEntry.session))
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "finished",
            WorkoutSessionEntry.canonical_exercise_id == canonical_id,
        )
        .order_by(WorkoutSession.performed_at.desc(), WorkoutSessionEntry.id.desc())
        .limit(10)
    )
    result = await db.scalars(stmt)
    entries = result.all()
    if not entries:
        return None

    last_entry = entries[0]
    session_date = last_entry.session.performed_at or datetime.now(UTC).replace(tzinfo=None)
    now = datetime.now(UTC).replace(tzinfo=None)
    days_ago = max(0, (now - session_date).days)

    working_sets: List[ParsedSet] = []
    top_weight = 0.0
    pain = last_entry.feeling == "pain"

    # From sets relationship or fallback to entry columns
    if last_entry.workout_sets:
        for ws in last_entry.workout_sets:
            if ws.set_type == "working" and ws.completed:
                w = float(ws.weight_kg) if ws.weight_kg is not None else 0.0
                r = ws.reps or 0
                working_sets.append(ParsedSet(weight_kg=w, reps=r, set_type="working"))
                if w > top_weight:
                    top_weight = w
    elif last_entry.sets:
        w = float(last_entry.weight_kg) if last_entry.weight_kg is not None else 0.0
        r = last_entry.reps or 0
        top_weight = w
        for _ in range(last_entry.sets):
            working_sets.append(ParsedSet(weight_kg=w, reps=r, set_type="working"))

    reps_list = [s.reps for s in working_sets if s.reps]
    median_reps = int(statistics.median(reps_list)) if reps_list else 0

    best_e1rm = 0.0
    for s in working_sets:
        if s.weight_kg and s.reps:
            e = calculate_e1rm(s.weight_kg, s.reps)
            if e > best_e1rm:
                best_e1rm = e

    ex = await db.get(CanonicalExercise, canonical_id)
    name = ex.display_name if ex else "Exercise"

    return ExerciseHistory(
        exercise_id=canonical_id,
        name=name,
        top_weight=top_weight,
        working_sets=working_sets,
        median_reps=median_reps,
        feeling=last_entry.feeling,
        days_ago=days_ago,
        pain=pain,
        e1rm=best_e1rm,
    )


async def get_user_muscle_recovery_stats(
    db: AsyncSession,
    user_id: int,
) -> Dict[str, Dict[str, Any]]:
    """Calculate days_since and weekly_sets per muscle group."""
    now = datetime.now(UTC).replace(tzinfo=None)
    one_week_ago = now - timedelta(days=7)

    # Fetch last 30 days of entries
    stmt = (
        select(WorkoutSessionEntry)
        .join(WorkoutSession, WorkoutSessionEntry.session_id == WorkoutSession.id)
        .join(CanonicalExercise, WorkoutSessionEntry.canonical_exercise_id == CanonicalExercise.id)
        .options(
            selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSessionEntry.session),
            selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "finished",
            WorkoutSession.performed_at >= now - timedelta(days=30),
        )
        .order_by(WorkoutSession.performed_at.desc())
    )
    result = await db.scalars(stmt)
    entries = result.all()

    stats: Dict[str, Dict[str, Any]] = {}
    from src.services.training.focus import FOCUS_GROUPS

    for grp in FOCUS_GROUPS.keys():
        stats[grp] = {"days_since": 99.0, "weekly_sets": 0}

    for entry in entries:
        ex = entry.canonical_exercise
        if not ex or not ex.muscle_primary:
            continue

        session_date = entry.session.performed_at or now
        days_since = (now - session_date).total_seconds() / 86400.0
        n_sets = len(entry.workout_sets) if entry.workout_sets else (entry.sets or 1)

        # Map muscle_primary to group
        primary = ex.muscle_primary
        for grp, muscles in FOCUS_GROUPS.items():
            if primary in muscles:
                if days_since < stats[grp]["days_since"]:
                    stats[grp]["days_since"] = round(days_since, 1)
                if session_date >= one_week_ago:
                    stats[grp]["weekly_sets"] += n_sets

    return stats


async def get_user_weekly_workout_count(db: AsyncSession, user_id: int) -> int:
    """Return number of finished workouts in the current week (since Monday)."""
    now = datetime.now(UTC).replace(tzinfo=None)
    start_of_week = now - timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

    stmt = select(func.count(WorkoutSession.id)).where(
        WorkoutSession.user_id == user_id,
        WorkoutSession.status == "finished",
        WorkoutSession.performed_at >= start_of_week,
    )
    count = await db.scalar(stmt)
    return count or 0


async def get_last_finished_session(db: AsyncSession, user_id: int) -> Optional[WorkoutSession]:
    """Retrieve the most recent completed workout session."""
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == "finished",
        )
        .order_by(WorkoutSession.performed_at.desc())
        .limit(1)
    )
    result = await db.scalars(stmt)
    return result.first()
