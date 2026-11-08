"""Every Training v2 inline button must route to exactly the intended handler (no shadowing, no dead buttons)."""
import pathlib
import re

import pytest
from aiogram.types import CallbackQuery, User

from src.handlers.training import router

pytestmark = pytest.mark.asyncio

# Buttons rendered without a handler (spinner forever). Must stay empty.
KNOWN_UNIMPLEMENTED: set[str] = set()
# Actions that carry extra routing info in the 4th segment (onb_skip:0:0:<step>)
SPECIAL_DATA = {"onb_skip": "t2:onb_skip:0:0:1"}


def _emitted_actions():
    acts = set()
    for f in pathlib.Path("src/handlers/training").glob("*.py"):
        acts |= set(re.findall(r'make_cb\(\s*"([a-z_0-9]+)"', f.read_text()))
    return acts


def _handlers(r):
    yield from r.callback_query.handlers
    for sub in r.sub_routers:
        yield from _handlers(sub)


async def _first_handler(data):
    cb = CallbackQuery(id="1", from_user=User(id=1, is_bot=False, first_name="x"), chat_instance="c", data=data)
    for h in _handlers(router):
        if all([await f.call(cb) for f in h.filters]):
            return h.callback.__name__
    return None


async def test_every_button_has_a_handler():
    missing = set()
    for act in _emitted_actions():
        data = SPECIAL_DATA.get(act, f"t2:{act}:1:1:x")
        if await _first_handler(data) is None:
            missing.add(act)
    assert missing == KNOWN_UNIMPLEMENTED, f"unexpected dead buttons: {missing ^ KNOWN_UNIMPLEMENTED}"


@pytest.mark.parametrize("data,expected", [
    ("t2:eq_add_cat:0:0:leg_press", "handle_add_catalog_machine"),
    ("t2:eq_del_id:0:0:5", "handle_delete_equipment_item"),
    ("t2:ok_set:1:1:1", "handle_log_set_ok"),
    ("t2:start_plan:1:0", "handle_start_or_resume_live"),
    ("t2:hub:0:0", "handle_hub_callback"),
])
async def test_specific_callbacks_not_shadowed(data, expected):
    name = await _first_handler(data)
    assert name == expected
