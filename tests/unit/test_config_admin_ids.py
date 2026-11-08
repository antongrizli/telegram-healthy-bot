"""ADMIN_USER_IDS must parse from the environment in every documented form (issue #17)."""
import pytest

from src.config import Settings

BASE = {"TELEGRAM_BOT_TOKEN": "x", "DATABASE_URL": "sqlite+aiosqlite://", "WEBAPP_URL": "https://example.test"}


@pytest.mark.parametrize("raw,expected", [
    ("111,222", [111, 222]),
    ("111, 222 ,", [111, 222]),
    ("111", [111]),
    ("", []),
    ("   ", []),
    ("[111,222]", [111, 222]),
    ("[]", []),
])
def test_admin_ids_from_env(monkeypatch, raw, expected):
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("ADMIN_USER_IDS", raw)
    assert Settings(_env_file=None).ADMIN_USER_IDS == expected


def test_admin_ids_default_empty_when_unset(monkeypatch):
    for k, v in BASE.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ADMIN_USER_IDS", raising=False)
    assert Settings(_env_file=None).ADMIN_USER_IDS == []


def test_admin_ids_from_init_kwargs():
    assert Settings(_env_file=None, ADMIN_USER_IDS=[5, "6"], **BASE).ADMIN_USER_IDS == [5, 6]
    assert Settings(_env_file=None, ADMIN_USER_IDS=7, **BASE).ADMIN_USER_IDS == [7]
