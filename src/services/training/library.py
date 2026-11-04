"""User machine and exercise library management, alias matching, and catalog linking."""
from __future__ import annotations

import difflib
import re
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import CanonicalExercise, ExerciseAlias, WorkoutSessionEntry
from src.services.training.catalog import get_catalog, normalize_catalog_string


def slugify(text: str) -> str:
    """Generate safe snake_case slug from name."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "_", s)
    return s[:100] or "exercise"


async def get_user_library(db: AsyncSession, user_id: int) -> List[CanonicalExercise]:
    """Get all non-hidden exercises for a user with their aliases."""
    stmt = (
        select(CanonicalExercise)
        .options(selectinload(CanonicalExercise.aliases))
        .where(CanonicalExercise.user_id == user_id, CanonicalExercise.is_hidden == False)
        .order_by(CanonicalExercise.is_favorite.desc(), CanonicalExercise.display_name.asc())
    )
    result = await db.scalars(stmt)
    return list(result.all())


async def match_exercise(
    db: AsyncSession,
    user_id: int,
    name: str,
) -> Tuple[Optional[CanonicalExercise], str]:
    """Match exercise name against user aliases and catalog.
    Returns: (matched_canonical_or_none, status: 'exact' | 'fuzzy' | 'catalog' | 'unknown')
    """
    catalog = get_catalog()
    norm = normalize_catalog_string(name)

    # 1. Exact alias match
    stmt = (
        select(ExerciseAlias)
        .options(selectinload(ExerciseAlias.canonical_exercise))
        .where(ExerciseAlias.user_id == user_id, ExerciseAlias.alias_normalized == norm)
    )
    alias = (await db.scalars(stmt)).first()
    if alias and alias.canonical_exercise:
        return alias.canonical_exercise, "exact"

    # 2. Fuzzy search over user aliases
    all_aliases = (
        await db.scalars(
            select(ExerciseAlias)
            .options(selectinload(ExerciseAlias.canonical_exercise))
            .where(ExerciseAlias.user_id == user_id)
        )
    ).all()

    alias_map = {a.alias_normalized: a.canonical_exercise for a in all_aliases if a.canonical_exercise}
    close = difflib.get_close_matches(norm, list(alias_map.keys()), n=1, cutoff=0.85)
    if close:
        return alias_map[close[0]], "fuzzy"

    # 3. Catalog match
    cat_entry = catalog.find_by_name(name)
    if cat_entry:
        return None, "catalog"

    return None, "unknown"


async def get_or_create_canonical(
    db: AsyncSession,
    user_id: int,
    name: str,
    catalog_key: Optional[str] = None,
    kind: str = "strength",
) -> CanonicalExercise:
    """Find or create canonical exercise and register alias."""
    norm = normalize_catalog_string(name)
    catalog = get_catalog()

    # 1. Check existing alias
    stmt = select(ExerciseAlias).where(
        ExerciseAlias.user_id == user_id,
        ExerciseAlias.alias_normalized == norm,
    ).options(selectinload(ExerciseAlias.canonical_exercise))
    alias = (await db.scalars(stmt)).first()
    if alias and alias.canonical_exercise:
        if alias.canonical_exercise.is_hidden:
            alias.canonical_exercise.is_hidden = False
            await db.flush()
        return alias.canonical_exercise

    # 2. Check if slug exists
    slug = slugify(name)
    stmt_ex = select(CanonicalExercise).where(
        CanonicalExercise.user_id == user_id,
        CanonicalExercise.canonical_slug == slug,
    )
    ex = (await db.scalars(stmt_ex)).first()

    if not ex:
        # Fill from catalog if available
        cat_item = catalog.get(catalog_key) if catalog_key else catalog.find_by_name(name)
        muscle_primary = cat_item.get("muscle_primary") if cat_item else None
        muscles_sec = cat_item.get("muscles_secondary", []) if cat_item else []
        pattern = cat_item.get("pattern") if cat_item else None
        equipment = cat_item.get("equipment") if cat_item else None
        weight_step = float(cat_item.get("weight_step") or 2.5) if cat_item else 2.5
        item_kind = cat_item.get("kind", kind) if cat_item else kind
        cat_k = cat_item.get("key") if cat_item else catalog_key

        ex = CanonicalExercise(
            user_id=user_id,
            canonical_slug=slug,
            display_name=name,
            kind=item_kind,
            muscle_primary=muscle_primary,
            muscles_secondary=muscles_sec,
            pattern=pattern,
            equipment=equipment,
            weight_step=weight_step,
            catalog_key=cat_k,
            is_hidden=False,
        )
        db.add(ex)
        await db.flush()
    else:
        if ex.is_hidden:
            ex.is_hidden = False
            await db.flush()

    # Create alias
    new_alias = ExerciseAlias(
        user_id=user_id,
        alias_normalized=norm,
        canonical_exercise_id=ex.id,
    )
    db.add(new_alias)
    await db.flush()

    return ex


async def merge_exercises(
    db: AsyncSession,
    user_id: int,
    target_id: int,
    source_id: int,
) -> None:
    """Merge source exercise into target exercise (relinking entries, aliases and deleting source)."""
    if target_id == source_id:
        return

    # Update entries
    await db.execute(
        update(WorkoutSessionEntry)
        .where(WorkoutSessionEntry.canonical_exercise_id == source_id)
        .values(canonical_exercise_id=target_id)
    )

    # Move aliases
    source_ex = await db.get(CanonicalExercise, source_id)
    if source_ex:
        norm_source = normalize_catalog_string(source_ex.display_name)
        # Check if alias already exists
        exists = await db.scalar(
            select(ExerciseAlias.id).where(
                ExerciseAlias.user_id == user_id,
                ExerciseAlias.alias_normalized == norm_source,
            )
        )
        if not exists:
            db.add(ExerciseAlias(user_id=user_id, alias_normalized=norm_source, canonical_exercise_id=target_id))

    await db.execute(
        update(ExerciseAlias)
        .where(ExerciseAlias.canonical_exercise_id == source_id)
        .values(canonical_exercise_id=target_id)
    )

    # Delete source
    await db.delete(source_ex)
    await db.flush()


async def remove_user_exercise(
    db: AsyncSession,
    user_id: int,
    exercise_id: int,
) -> Optional[CanonicalExercise]:
    """Remove exercise from user library by marking is_hidden=True."""
    ex = await db.get(CanonicalExercise, exercise_id)
    if ex and ex.user_id == user_id:
        ex.is_hidden = True
        await db.flush()
        return ex
    return None


async def remove_user_exercise_by_name(
    db: AsyncSession,
    user_id: int,
    name: str,
) -> Optional[CanonicalExercise]:
    """Find and hide exercise by display name or fuzzy match."""
    norm = normalize_catalog_string(name)
    stmt = (
        select(CanonicalExercise)
        .where(
            CanonicalExercise.user_id == user_id,
            CanonicalExercise.is_hidden == False,
        )
    )
    exercises = list((await db.scalars(stmt)).all())
    for ex in exercises:
        if normalize_catalog_string(ex.display_name) == norm:
            ex.is_hidden = True
            await db.flush()
            return ex
    # Check fuzzy match
    names = [normalize_catalog_string(ex.display_name) for ex in exercises]
    close = difflib.get_close_matches(norm, names, n=1, cutoff=0.75)
    if close:
        for ex in exercises:
            if normalize_catalog_string(ex.display_name) == close[0]:
                ex.is_hidden = True
                await db.flush()
                return ex
    return None
