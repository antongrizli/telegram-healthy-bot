import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services.training import llm
from src.services.training.focus import groups_for


def test_catalog_candidates_arms():
    names = llm.catalog_candidates(["arms"], "de")
    assert names
    from src.services.training.catalog import get_catalog

    cat = get_catalog()
    for n in names:
        assert cat.find_by_name(n)["muscle_primary"] in {"biceps", "triceps"}


def test_suggest_falls_back_on_error():
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(side_effect=RuntimeError("429"))):
        assert asyncio.run(llm.suggest_machines(["arms"], None, [], [], "de")) == []
        names = asyncio.run(llm.pick_suggestions(["arms"], None, [], [], "de"))
    assert names and names == llm.catalog_candidates(["arms"], "de")


def test_suggest_excludes_existing():
    resp = SimpleNamespace(text='{"machines": ["Latzug", "Trizepsmaschine"]}')
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(return_value=resp)):
        assert asyncio.run(llm.suggest_machines(["back"], None, [], ["Latzug"], "de")) == ["Trizepsmaschine"]


def test_groups_for_maps_muscles_and_groups():
    assert groups_for(["arms", "chest", "biceps", "quads"]) == ["arms", "chest", "legs"]
