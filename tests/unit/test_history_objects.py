"""Regression: history_index holds ExerciseHistory pydantic objects, not dicts."""
from src.services.training import focus, planner
from src.services.training.dto import ExerciseHistory, ParsedSet
from src.services.training.parser import parse_line


def _hist(name="жим лёжа"):
    return ExerciseHistory(
        exercise_id=1,
        name=name,
        top_weight=60.0,
        working_sets=[ParsedSet(weight_kg=60.0, reps=8)],
        median_reps=8,
        days_ago=2,
    )


def test_build_plan_accepts_exercise_history_objects():
    lib = [{"id": 1, "display_name": "жим лёжа", "kind": "strength", "muscle_primary": "chest",
            "pattern": "horizontal_push", "weight_step": 2.5}]
    fo = focus.resolve_focus(selected_groups=["chest"], minutes=45, energy="normal")
    items = planner.build_plan(None, lib, {"жим лёжа": _hist()}, fo, minutes=45, energy="normal")
    assert items


def test_parser_accepts_exercise_history_objects():
    idx = {"жим лёжа": _hist()}
    items = parse_line("жим лёжа +2.5", history_index=idx)
    assert items[0].sets[0].weight_kg == 62.5


def test_adjust_weight_handler_accepts_decimal_state(monkeypatch):
    """Postgres Numeric gives Decimal; the +/- weight button must not crash on it."""
    import asyncio
    from decimal import Decimal
    from unittest.mock import AsyncMock, MagicMock

    from src.handlers.training import live

    state = MagicMock()
    state.get_data = AsyncMock(return_value={"current_w": Decimal("40.00"), "current_r": 10})
    state.update_data = AsyncMock()
    callback = MagicMock()
    callback.answer = AsyncMock()
    callback.data = "t2:adj_w:1:0:2.5"
    callback.message = None

    class _Ctx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(live, "AsyncSessionLocal", lambda: _Ctx())
    monkeypatch.setattr(live.sessions, "get_session_full", AsyncMock(return_value=None))

    asyncio.run(live.handle_adjust_weight_reps(callback, state))
    state.update_data.assert_awaited_with(current_w=42.5)
