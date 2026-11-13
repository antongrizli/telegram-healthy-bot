"""Single loader of everything the planner needs: profile, user library and exercise history."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import CanonicalExercise, TrainingProfile
from src.services.training import history, library
from src.services.training.dto import ExerciseHistory
from src.services.training.focus import groups_for


@dataclass
class PlanContext:
    profile: Optional[TrainingProfile]
    library: List[Dict[str, Any]] = field(default_factory=list)
    history_index: Dict[str, ExerciseHistory] = field(default_factory=dict)
    covered_groups: Set[str] = field(default_factory=set)


def exercise_to_dict(ex: CanonicalExercise) -> Dict[str, Any]:
    return {
        "id": ex.id,
        "display_name": ex.display_name,
        "kind": ex.kind,
        "muscle_primary": ex.muscle_primary,
        "muscles_secondary": ex.muscles_secondary,
        "pattern": ex.pattern,
        "equipment": ex.equipment,
        "weight_step": ex.weight_step,
        "min_weight": ex.min_weight,
        "catalog_key": ex.catalog_key,
        "is_favorite": ex.is_favorite,
    }


async def load_plan_context(db: AsyncSession, user_id: int) -> PlanContext:
    profile = await db.get(TrainingProfile, user_id)
    exercises = await library.get_user_library(db, user_id)
    histories = await history.get_histories(db, user_id, [e.id for e in exercises])
    covered = set(groups_for([e.muscle_primary for e in exercises if e.muscle_primary]))
    if any(e.kind == "cardio" for e in exercises):
        covered.add("cardio")
    return PlanContext(
        profile=profile,
        library=[exercise_to_dict(e) for e in exercises],
        history_index={e.display_name.lower(): histories[e.id] for e in exercises if e.id in histories},
        covered_groups=covered,
    )
