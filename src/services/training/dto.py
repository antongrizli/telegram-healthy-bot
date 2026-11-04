"""DTOs for Training v2: parser, planner, sessions and summary."""
from __future__ import annotations

from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field


class ParsedSet(BaseModel):
    weight_kg: Optional[float] = None
    added_weight_kg: Optional[float] = None
    reps: Optional[int] = None
    duration_sec: Optional[int] = None
    distance_m: Optional[int] = None
    set_type: Literal["warmup", "working", "drop", "failure"] = "working"


class DraftItem(BaseModel):
    line_no: int
    raw: str
    name: str
    exercise_id: Optional[int] = None
    catalog_key: Optional[str] = None
    kind: Literal["strength", "bodyweight", "cardio", "timed"] = "strength"
    sets: List[ParsedSet] = Field(default_factory=list)
    note: Optional[str] = None
    feeling: Optional[Literal["easy", "normal", "hard"]] = None
    status: Literal["ok", "ambiguous", "new_name", "unparsed"] = "ok"
    options: List[List[ParsedSet]] = Field(default_factory=list)
    source: Literal["rules", "llm", "user"] = "rules"


class PlanItem(BaseModel):
    exercise_id: Optional[int] = None
    catalog_key: Optional[str] = None
    name: str
    kind: str = "strength"
    warmup: List[ParsedSet] = Field(default_factory=list)
    working: List[ParsedSet] = Field(default_factory=list)
    rest_sec: int = 75
    reason: str = ""
    cue: str = ""
    calibrate: bool = False


class Target(BaseModel):
    weight: Optional[float] = None
    reps: Optional[int] = None
    calibrate: bool = False
    flag: Optional[str] = None


class ExerciseHistory(BaseModel):
    exercise_id: int
    name: str
    top_weight: float = 0.0
    working_sets: List[ParsedSet] = Field(default_factory=list)
    median_reps: int = 0
    feeling: Optional[str] = None
    days_ago: int = 999
    pain: bool = False
    e1rm: float = 0.0
    history_sessions: List[Any] = Field(default_factory=list)


class Focus(BaseModel):
    primary_muscles: List[str] = Field(default_factory=list)
    secondary_muscles: List[str] = Field(default_factory=list)
    modifiers: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    target_minutes: int = 45
    energy: str = "normal"  # "low", "normal", "high"
    accent: Optional[str] = None
    raw_text: Optional[str] = None


class SessionSummary(BaseModel):
    session_id: int
    duration_minutes: int = 0
    exercises_count: int = 0
    sets_count: int = 0
    total_volume_kg: float = 0.0
    volume_diff_pct: Optional[float] = None
    records: List[str] = Field(default_factory=list)
    exercise_summaries: List[dict] = Field(default_factory=list)
    ai_focus_next: Optional[str] = None
