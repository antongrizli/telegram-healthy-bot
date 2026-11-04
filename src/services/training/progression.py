"""Double progression rules, e1RM estimation, warmup calculation, and step rounding."""
from __future__ import annotations

import statistics
from typing import Optional, Tuple

from src.services.training.dto import ExerciseHistory, ParsedSet, Target


def rep_range(goal: str, pattern: Optional[str] = None) -> Tuple[int, int]:
    """Return (low, high) repetition range for given goal and pattern."""
    is_isolation = pattern == "isolation"
    goal_lower = (goal or "hypertrophy").lower()

    if goal_lower == "strength":
        return (8, 10) if is_isolation else (4, 6)
    elif goal_lower in ("endurance", "fat_loss"):
        return (12, 20)
    elif goal_lower == "health":
        return (10, 15)
    else:  # hypertrophy default
        return (10, 15) if is_isolation else (8, 12)


def round_step(val: float, step: float = 2.5, min_weight: Optional[float] = None) -> float:
    """Round weight to the nearest step and ensure it is not below min_weight."""
    if step <= 0:
        step = 2.5
    rounded = round(val / step) * step
    # Avoid negative numbers or float precision issues (.00000001)
    rounded = round(rounded, 2)
    if min_weight is not None and rounded < min_weight:
        rounded = min_weight
    return max(0.0, rounded)


def calculate_e1rm(weight: float, reps: int) -> float:
    """Calculate estimated 1RM using Epley formula: w * (1 + reps / 30) for reps <= 12."""
    if weight <= 0 or reps <= 0:
        return 0.0
    effective_reps = min(reps, 15)
    return round(weight * (1.0 + effective_reps / 30.0), 2)


def next_target(
    last: Optional[ExerciseHistory],
    pattern: Optional[str] = None,
    goal: str = "hypertrophy",
    energy: str = "normal",
    weight_step: float = 2.5,
    min_weight: Optional[float] = None,
    is_deload: bool = False,
) -> Target:
    """Calculate next workout target based on double progression logic."""
    lo, hi = rep_range(goal, pattern)

    if last is None:
        return Target(calibrate=True, reps=hi, weight=None)

    # Deload condition: 3 sessions without e1RM increase -> deload: -10% weight
    if is_deload:
        w = round_step(last.top_weight * 0.9, step=weight_step, min_weight=min_weight)
        return Target(weight=w, reps=lo, flag="deload")

    # Time away: > 21 days: -15%
    if last.days_ago > 21:
        w = round_step(last.top_weight * 0.85, step=weight_step, min_weight=min_weight)
        return Target(weight=w, reps=lo)

    # Time away: > 14 days: -10%
    if last.days_ago > 14:
        # Note: rounding down/nearest to step: 40 * 0.9 = 36 -> rounded to 35 if 2.5 step
        w = round_step(last.top_weight * 0.9, step=weight_step, min_weight=min_weight)
        # If 36 on 2.5 step: round(36/2.5)*2.5 = round(14.4)*2.5 = 14*2.5 = 35.0
        return Target(weight=w, reps=lo)

    # Pain reported
    if last.pain:
        w = round_step(last.top_weight * 0.8, step=weight_step, min_weight=min_weight)
        return Target(weight=w, reps=lo, flag="pain")

    working_sets = [s for s in last.working_sets if s.reps is not None]
    if not working_sets:
        return Target(weight=last.top_weight, reps=lo)

    reps_list = [s.reps for s in working_sets if s.reps is not None]
    median_reps = int(statistics.median(reps_list)) if reps_list else lo
    all_hit_top = all(s.reps >= hi for s in working_sets)

    # If feeling was hard or energy is low: keep current load
    if last.feeling == "hard" or energy == "low":
        return Target(weight=last.top_weight, reps=median_reps)

    # If all sets hit upper rep range and feeling was easy or normal: progress weight
    if all_hit_top and last.feeling in ("easy", "normal"):
        next_w = round_step(last.top_weight + weight_step, step=weight_step, min_weight=min_weight)
        return Target(weight=next_w, reps=lo)

    # If feeling was easy: push reps +2 towards top of range
    if last.feeling == "easy":
        return Target(weight=last.top_weight, reps=min(hi, median_reps + 2))

    # Normal feeling: push reps +1 towards top of range
    return Target(weight=last.top_weight, reps=min(hi, median_reps + 1))


def calculate_warmup_sets(
    working_weight: float,
    is_first_in_group: bool = True,
    is_isolation: bool = False,
    weight_step: float = 2.5,
) -> list[ParsedSet]:
    """Calculate warmup sets if working weight >= 20kg."""
    if working_weight < 20.0:
        return []

    if is_isolation or not is_first_in_group:
        w1 = round_step(working_weight * 0.5, step=weight_step)
        return [ParsedSet(weight_kg=w1, reps=12, set_type="warmup")]

    # First compound in group: 1x12 @ 50%, 1x6 @ 75%
    w1 = round_step(working_weight * 0.5, step=weight_step)
    w2 = round_step(working_weight * 0.75, step=weight_step)
    return [
        ParsedSet(weight_kg=w1, reps=12, set_type="warmup"),
        ParsedSet(weight_kg=w2, reps=6, set_type="warmup"),
    ]
