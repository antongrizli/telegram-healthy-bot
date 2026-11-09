"""Workout session repository: draft creation, live set logging, state mutations, and revision protection."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, List, Optional, Union
from sqlalchemy import delete, func, select, update
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


class EmptyWorkoutError(Exception):
    """Raised when finishing a live session in which nothing was completed."""


class StaleRevisionError(Exception):
    """Raised when incoming callback revision does not match the database session revision."""
    pass


async def _load_full(db: AsyncSession, session_id: int, missing_ok: bool = False) -> Optional[WorkoutSession]:
    """Reload a session with entries, sets and exercises; populate_existing so rows changed in this transaction show."""
    stmt = (
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.id == session_id)
        .execution_options(populate_existing=True)
    )
    result = await db.scalars(stmt)
    return result.first() if missing_ok else result.one()


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
    # Persist right away: callers open a fresh DB session per request and never commit this one,
    # so an uncommitted draft vanished and later inserts failed with a foreign key violation.
    await db.commit()
    # Refresh to load relationships
    return await _load_full(db, session.id)


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
    user_id: Optional[int] = None,
) -> Optional[WorkoutSession]:
    """Full session tree by id; when ``user_id`` is given, another user's session reads as missing."""
    session = await _load_full(db, session_id, missing_ok=True)
    if session is not None and user_id is not None and session.user_id != user_id:
        return None
    return session


async def add_draft_items(
    db: AsyncSession,
    session: WorkoutSession,
    items: List[DraftItem],
) -> WorkoutSession:
    """Add parsed draft items to a draft session."""
    # Callers often fetch the draft in a different (already closed) DB session; reload it here so the
    # instance is attached to ``db`` and its entries are loaded.
    session = await _load_full(db, session.id)
    # Find next position index
    max_pos = max((e.position for e in session.entries), default=-1)

    skipped: List[str] = []
    for item in items:
        if item.status in ("unparsed", "new_name"):
            # Never turn unrecognised text into library machines; keep it as a session note instead.
            if item.status == "unparsed" and item.raw:
                skipped.append(item.raw)
            continue
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

    if skipped:
        session.note = "\n".join(filter(None, [session.note, *skipped]))[:2000]
    session.revision += 1
    await db.flush()

    # Reload session with updated entries and sets
    return await _load_full(db, session.id)


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
        canonical = None
        if item.get("exercise_id"):
            canonical = await db.get(CanonicalExercise, item["exercise_id"])
            if canonical is not None and canonical.user_id != user_id:
                canonical = None
        if canonical is None:  # legacy plans without exercise ids
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

    return await _load_full(db, session.id)


async def log_set(
    db: AsyncSession,
    session_id: int,
    rev: int,
    entry_id: int,
    set_data: ParsedSet,
    user_id: Optional[int] = None,
) -> WorkoutSession:
    """Log a completed set in live session with revision guard (and owner guard when ``user_id`` is given)."""
    session = await db.get(WorkoutSession, session_id)
    if not session or (user_id is not None and session.user_id != user_id) or session.revision != rev:
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

    return await _load_full(db, session.id)


async def complete_entry_as_planned(
    db: AsyncSession,
    session_id: int,
    rev: int,
    entry_id: int,
    user_id: Optional[int] = None,
) -> WorkoutSession:
    """Mark every open set of an entry as done at its planned values.

    Sets with unknown weight/reps (and no planned duration) stay open so the user fills them in.
    """
    session = await db.get(WorkoutSession, session_id)
    if not session or (user_id is not None and session.user_id != user_id) or session.revision != rev:
        raise StaleRevisionError(f"Session revision mismatch: expected {session.revision if session else None}, got {rev}")

    entry = await db.get(WorkoutSessionEntry, entry_id)
    if not entry or entry.session_id != session.id:
        raise ValueError(f"Entry {entry_id} does not belong to session {session_id}")

    stmt = select(WorkoutSet).where(WorkoutSet.entry_id == entry_id, WorkoutSet.completed == False).order_by(WorkoutSet.idx)
    for ws in (await db.scalars(stmt)).all():
        weight = ws.weight_kg if ws.weight_kg is not None else ws.target_weight_kg
        reps = ws.reps if ws.reps is not None else ws.target_reps
        duration = ws.duration_sec if ws.duration_sec is not None else ws.target_duration_sec
        if duration is not None:
            ws.duration_sec = duration
        elif weight is not None and reps is not None:
            ws.weight_kg, ws.reps = weight, reps
        else:
            continue
        ws.completed = True

    session.revision += 1
    await db.flush()
    return await _load_full(db, session.id)


async def undo_last_set(
    db: AsyncSession,
    session_id: int,
    rev: int,
    user_id: int,
) -> Optional[WorkoutSession]:
    """Undo the most recently logged set of a live session; returns None when nothing was logged yet."""
    session = await db.get(WorkoutSession, session_id)
    if not session or session.user_id != user_id:
        raise ValueError(f"Session {session_id} not found for user")
    if session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    stmt = (
        select(WorkoutSet)
        .join(WorkoutSessionEntry, WorkoutSet.entry_id == WorkoutSessionEntry.id)
        .where(WorkoutSessionEntry.session_id == session_id, WorkoutSet.completed == True)  # noqa: E712
        .order_by(WorkoutSet.id.desc())
        .limit(1)
    )
    last = (await db.scalars(stmt)).first()
    if last is None:
        return None

    planned = any(v is not None for v in (last.target_weight_kg, last.target_reps, last.target_duration_sec))
    if planned:
        last.completed = False  # keep the planned target so the set can be logged again
    else:
        await db.delete(last)  # an extra set added during the workout
    entry = await db.get(WorkoutSessionEntry, last.entry_id)
    if entry and entry.status == "skipped":
        entry.status = "done"
    session.revision += 1
    await db.flush()
    return await get_session_full(db, session_id)


async def skip_entry(
    db: AsyncSession,
    session_id: int,
    rev: int,
    entry_id: int,
    user_id: Optional[int] = None,
) -> WorkoutSession:
    """Mark an entry as skipped."""
    session = await db.get(WorkoutSession, session_id)
    if not session or (user_id is not None and session.user_id != user_id) or session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    entry = await db.get(WorkoutSessionEntry, entry_id)
    if entry and entry.session_id == session.id:
        entry.status = "skipped"
        session.revision += 1
        await db.flush()

    return await _load_full(db, session.id)


_FEELING_RANK = {"easy": 0, "normal": 1, "hard": 2, "pain": 3}


async def finish_session(
    db: AsyncSession,
    session_id: int,
    rev: int,
    feeling: Optional[str] = None,
    user_id: Optional[int] = None,
) -> WorkoutSession:
    """Finish a session and record finished_at.

    Draft (text-logged) sessions: all sets count as done and ``feeling`` (default "normal") applies to every entry.
    Live sessions: only sets the user actually completed are kept (pending sets and untouched/skipped entries are
    dropped, so unperformed work never counts as volume), per-exercise feelings are preserved, and the session feeling
    is ``feeling`` or the hardest exercise feeling. Raises EmptyWorkoutError when a live session has no completed sets.
    """
    session = await db.get(WorkoutSession, session_id)
    if not session or (user_id is not None and session.user_id != user_id) or session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    stmt = (
        select(WorkoutSessionEntry)
        .options(selectinload(WorkoutSessionEntry.workout_sets))
        .where(WorkoutSessionEntry.session_id == session.id)
    )
    entries = (await db.scalars(stmt)).all()
    live = session.status == "active"

    if live and not any(ws.completed for e in entries for ws in e.workout_sets):
        raise EmptyWorkoutError(f"Session {session_id} has no completed sets")

    kept = []
    for entry in entries:
        if live:
            for ws in [w for w in entry.workout_sets if not w.completed]:
                await db.delete(ws)
            sets = [w for w in entry.workout_sets if w.completed]
            if not sets:
                await db.delete(entry)
                continue
        else:
            entry.feeling = feeling or "normal"
            for ws in entry.workout_sets:
                ws.completed = True
            sets = list(entry.workout_sets)
        kept.append(entry)

        # Keep legacy columns populated
        working_sets = [w for w in sets if w.set_type == "working"] or sets
        entry.sets = len(working_sets) if working_sets else 1
        if working_sets:
            entry.weight_kg = working_sets[0].weight_kg
            entry.reps = working_sets[0].reps
            entry.duration_sec = working_sets[0].duration_sec
            entry.distance_m = working_sets[0].distance_m

    if live and not feeling:
        feeling = max((e.feeling for e in kept if e.feeling), key=lambda f: _FEELING_RANK.get(f, 1), default="normal")

    session.status = "finished"
    session.finished_at = datetime.now(UTC).replace(tzinfo=None)
    session.feeling = feeling or "normal"
    if session.plan_id:
        plan = await db.get(TrainingPlan, session.plan_id)
        if plan and plan.status == "in_progress":
            plan.status = "done"

    session.revision += 1
    await db.flush()

    return await _load_full(db, session.id)


async def cancel_session(
    db: AsyncSession,
    session_id: int,
    rev: int,
    user_id: Optional[int] = None,
) -> None:
    """Cancel and delete draft or active session (silently ignores another user's session)."""
    session = await db.get(WorkoutSession, session_id)
    if not session or (user_id is not None and session.user_id != user_id):
        return
    if session.revision != rev:
        raise StaleRevisionError("Revision mismatch")

    if session.plan_id:
        plan = await db.get(TrainingPlan, session.plan_id)
        if plan and plan.status == "in_progress":
            plan.status = "proposed"  # let the user start the plan again
    await db.delete(session)
    await db.flush()
