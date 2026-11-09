import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.services.training import library, llm


def _resp(text):
    return SimpleNamespace(text=text)


def _run_classify(payload):
    with patch("src.services.training.llm.gemini.call_gemini_with_retry", AsyncMock(return_value=_resp(payload))):
        return asyncio.run(llm.classify_machine("Beinbeuger sitzend", "de"))


def test_classify_rejects_unknown_muscle():
    assert _run_classify('{"catalog_key":null,"muscle_primary":"wings","pattern":"isolation","kind":"strength"}') is None


def test_classify_accepts_beinbeuger():
    payload = '{"catalog_key":"leg_curl","muscle_primary":"hamstrings","pattern":"isolation","kind":"strength"}'
    assert _run_classify(payload) == {
        "catalog_key": "leg_curl", "muscle_primary": "hamstrings", "pattern": "isolation", "kind": "strength",
    }


def test_classify_drops_unknown_catalog_key():
    payload = '{"catalog_key":"nope","muscle_primary":"hamstrings","pattern":"isolation","kind":"strength"}'
    assert _run_classify(payload)["catalog_key"] is None


def test_ensure_tags_prefers_catalog():
    db = SimpleNamespace(flush=AsyncMock())
    ex = SimpleNamespace(display_name="Latzug", muscle_primary=None, muscles_secondary=[], pattern=None,
                         equipment=None, catalog_key=None, kind="strength")
    mock = AsyncMock()
    with patch("src.services.training.llm.classify_machine", mock):
        assert asyncio.run(library.ensure_tags(db, ex)) is True
    mock.assert_not_called()
    assert ex.muscle_primary == "back_lats"


def test_ensure_tags_uses_llm_for_unknown():
    db = SimpleNamespace(flush=AsyncMock())
    ex = SimpleNamespace(display_name="Zzz Gerät 9", muscle_primary=None, muscles_secondary=[], pattern=None,
                         equipment=None, catalog_key=None, kind="strength")
    info = {"catalog_key": None, "muscle_primary": "glutes", "pattern": "isolation", "kind": "strength"}
    with patch("src.services.training.llm.classify_machine", AsyncMock(return_value=info)):
        assert asyncio.run(library.ensure_tags(db, ex)) is True
    assert ex.muscle_primary == "glutes"
