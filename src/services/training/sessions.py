"""Workout session repository: draft creation, live set logging, state mutations, and revision protection."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, List, Optional, Union
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    CanonicalExercise,
    TrainingPlan,
    WorkoutSession,
    WorkoutSessionEntry,
    WorkoutSet,
)
from src.services.training.dto import DraftItem, ParsedSet, SessionSummary
from src.services.training.library import get_or_create_canonical

logger = logging.getLogger(__name__)


class StaleRevisionError(Exception):
    """Raised when incoming callback revision does not match the database session revision."""
    pass


async def get_or_create_draft(
    db: AsyncSession,
    user_id: int,
    source: str = "text",
    anchor_message_id: Optional[int] = None,
) -> WorkoutSession:
    """Find active draft session for user or create a new one."""
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.user_id == user_id, WorkoutSession.status == "draft")
        .order_by(WorkoutSession.id.desc())
        .limit(1)
    )
    session = (await db.scalars(stmt)).first()
    if session:
        if anchor_message_id and session.anchor_message_id != anchor_message_id:
            session.anchor_message_id = anchor_message_id
            await db.flush()
        return session

    now = datetime.now(UTC).replace(tzinfo=None)
    session = WorkoutSession(
        user_id=user_id,
        status="draft",
        source=source,
        performed_at=now,
        started_at=now,
        revision=0,
        anchor_message_id=anchor_message_id,
    )
    db.add(session)
    await db.flush()
    # Refresh to load relationships
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session.id)
    )
    return (await db.scalars(stmt)).one()


async def get_active_session(
    db: AsyncSession,
    user_id: int,
) -> Optional[WorkoutSession]:
    """Retrieve currently active (live) workout session if one exists."""
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.user_id == user_id, WorkoutSession.status == "active")
        .order_by(WorkoutSession.id.desc())
        .limit(1)
    )
    return (await db.scalars(stmt)).first()


async def get_session_full(
    db: AsyncSession,
    session_id: int,
) -> Optional[WorkoutSession]:
    """Retrieve full workout session by ID with entries, sets, and canonical exercises loaded."""
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session_id)
    )
    return (await db.scalars(stmt)).first()


async def add_draft_items(
    db: AsyncSession,
    session: WorkoutSession,
    items: List[DraftItem],
) -> WorkoutSession:
    """Add parsed draft items to a draft session."""
    # Find next position index
    max_pos = max((e.position for e in session.entries), default=-1)

    for item in items:
        max_pos += 1
        # Ensure canonical exercise exists
        canonical = await get_or_create_canonical(
            db,
            user_id=session.user_id,
            name=item.name,
            catalog_key=item.catalog_key,
            kind=item.kind,
        )

        entry = WorkoutSessionEntry(
            session_id=session.id,
            canonical_exercise_id=canonical.id,
            raw_input=item.raw,
            position=max_pos,
            status="done" if item.status == "ok" else "partial",
            feeling="normal",
            note=item.note,
        )
        db.add(entry)
        await db.flush()

        for idx, s in enumerate(item.sets, 1):
            ws = WorkoutSet(
                entry_id=entry.id,
                idx=idx,
                set_type=s.set_type,
                weight_kg=s.weight_kg,
                added_weight_kg=s.added_weight_kg,
                reps=s.reps,
                duration_sec=s.duration_sec,
                distance_m=s.distance_m,
                completed=False,  # Uncompleted until draft is saved
            )
            db.add(ws)

    session.revision += 1
    await db.flush()

    # Reload session with updated entries and sets
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session.id)
    )
    return (await db.scalars(stmt)).one()


async def start_from_plan(
    db: AsyncSession,
    user_id: int,
    plan_id: int,
    anchor_message_id: Optional[int] = None,
) -> WorkoutSession:
    """Initialize an active live workout session from an approved training plan."""
    plan = await db.get(TrainingPlan, plan_id)
    if not plan:
        raise ValueError(f"Plan {plan_id} not found")

    now = datetime.now(UTC).replace(tzinfo=None)
    session = WorkoutSession(
        user_id=user_id,
        status="active",
        source="live",
        plan_id=plan.id,
        focus=plan.focus,
        performed_at=now,
        started_at=now,
        revision=0,
        anchor_message_id=anchor_message_id,
    )
    db.add(session)
    await db.flush()

    # Create entries and target sets from plan.items
    items_data = plan.items or []
    for pos, item in enumerate(items_data):
        name = item.get("name", "Exercise")
        cat_key = item.get("catalog_key")
        kind = item.get("kind", "strength")
        canonical = await get_or_create_canonical(db, user_id, name, cat_key, kind)

        entry = WorkoutSessionEntry(
            session_id=session.id,
            canonical_exercise_id=canonical.id,
            raw_input=name,
            position=pos,
            status="done",
            feeling="normal",
        )
        db.add(entry)
        await db.flush()

        # Add warmup sets
        set_idx = 1
        for s in item.get("warmup", []):
            ws = WorkoutSet(
                entry_id=entry.id,
                idx=set_idx,
                set_type="warmup",
                target_weight_kg=s.get("weight_kg"),
                target_reps=s.get("reps"),
                weight_kg=s.get("weight_kg"),
                reps=s.get("reps"),
                completed=False,
            )
            db.add(ws)
            set_idx += 1

        # Add working sets
        for s in item.get("working", []):
            ws = WorkoutSet(
                entry_id=entry.id,
                idx=set_idx,
                set_type="working",
                target_weight_kg=s.get("weight_kg"),
                target_reps=s.get("reps"),
                target_duration_sec=s.get("duration_sec"),
                weight_kg=s.get("weight_kg"),
                reps=s.get("reps"),
                duration_sec=s.get("duration_sec"),
                completed=False,
            )
            db.add(ws)
            set_idx += 1

    plan.status = "in_progress"
    await db.flush()

    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session.id)
    )
    return (await db.scalars(stmt)).one()


async def log_set(
    db: AsyncSession,
    session_id: int,
    rev: int,
    entry_id: int,
    set_data: ParsedSet,
) -> WorkoutSession:
    """Log a completed set in live session with revision guard."""
    session = await db.get(WorkoutSession, session_id)
    if not session or session.revision != rev:
        raise StaleRevisionError(f"Session revision mismatch: expected {session.revision if session else None}, got {rev}")

    entry = await db.get(WorkoutSessionEntry, entry_id)
    if not entry or entry.session_id != session.id:
        raise ValueError(f"Entry {entry_id} does not belong to session {session_id}")

    # Find the next incomplete set in entry or create a new one
    stmt = (
        select(WorkoutSet)
        .where(WorkoutSet.entry_id == entry_id, WorkoutSet.completed == False)
        .order_by(WorkoutSet.idx.asc())
        .limit(1)
    )
    ws = (await db.scalars(stmt)).first()
    if ws:
        ws.weight_kg = set_data.weight_kg if set_data.weight_kg is not None else ws.target_weight_kg
        ws.reps = set_data.reps if set_data.reps is not None else ws.target_reps
        ws.duration_sec = set_data.duration_sec if set_data.duration_sec is not None else ws.target_duration_sec
        ws.distance_m = set_data.distance_m
        ws.completed = True
    else:
        # Create extra set
        curr_count = await db.scalar(select(func.count(WorkoutSet.id)).where(WorkoutSet.entry_id == entry_id)) or 0
        ws = WorkoutSet(
            entry_id=entry_id,
            idx=curr_count + 1,
            set_type=set_data.set_type,
            weight_kg=set_data.weight_kg,
            added_weight_kg=set_data.added_weight_kg,
            reps=set_data.reps,
            duration_sec=set_data.duration_sec,
            distance_m=set_data.distance_m,
            completed=True,
        )
        db.add(ws)

    session.revision += 1
    await db.flush()

    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session.id)
    )
    return (await db.scalars(stmt)).one()


async def skip_entry(
    db: AsyncSession,
    session_id: int,
    rev: int,
    entry_id: int,
) -> WorkoutSession:
    """Mark an entry as skipped."""
    session = await db.get(WorkoutSession, session_id)
    if not session or session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    entry = await db.get(WorkoutSessionEntry, entry_id)
    if entry and entry.session_id == session.id:
        entry.status = "skipped"
        session.revision += 1
        await db.flush()

    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session.id)
    )
    return (await db.scalars(stmt)).one()


async def finish_session(
    db: AsyncSession,
    session_id: int,
    rev: int,
    feeling: str = "normal",
) -> WorkoutSession:
    """Mark session as finished, mark all pending sets as completed if drafting, and record finished_at."""
    session = await db.get(WorkoutSession, session_id)
    if not session or session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    now = datetime.now(UTC).replace(tzinfo=None)
    session.status = "finished"
    session.finished_at = now
    session.feeling = feeling

    # Mark all sets in entries as completed and update legacy entry columns for backward read compatibility
    stmt = (
        select(WorkoutSessionEntry)
        .options(selectinload(WorkoutSessionEntry.workout_sets))
        .where(WorkoutSessionEntry.session_id == session.id)
    )
    entries = (await db.scalars(stmt)).all()
    for entry in entries:
        if feeling:
            entry.feeling = feeling
        for ws in entry.workout_sets:
            ws.completed = True

        # Keep legacy columns populated
        working_sets = [s for s in entry.workout_sets if s.set_type == "working"] or entry.workout_sets
        entry.sets = len(working_sets) if working_sets else 1
        if working_sets:
            entry.weight_kg = working_sets[0].weight_kg
            entry.reps = working_sets[0].reps
            entry.duration_sec = working_sets[0].duration_sec
            entry.distance_m = working_sets[0].distance_m

    session.revision += 1
    await db.flush()

    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session.id)
    )
    return (await db.scalars(stmt)).one()


async def cancel_session(
    db: AsyncSession,
    session_id: int,
    rev: int,
) -> None:
    """Cancel and delete draft or active session."""
    session = await db.get(WorkoutSession, session_id)
    if not session:
        return
    if session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    await db.delete(session)
    await db.flush()
