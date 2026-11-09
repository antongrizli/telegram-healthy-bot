"""Pasted reports must not pollute the machine library."""
import pytest
from sqlalchemy import func, select

from src.database.models import CanonicalExercise
from src.services.training import parser, sessions
from src.services.training.report_text import clean_report
from tests.integration.test_security_and_meal_recovery import make_user
from tests.unit.test_report_text import REPORT

pytestmark = pytest.mark.asyncio


async def _count(db, uid):
    return await db.scalar(select(func.count()).select_from(CanonicalExercise).where(CanonicalExercise.user_id == uid))


async def test_unparsed_lines_do_not_create_machines(db_session):
    await make_user(db_session, 4101)
    session = await sessions.get_or_create_draft(db_session, 4101, source="text")
    items = parser.parse_text("Самочувствие: Нормально", lang="ru")
    assert items and all(i.status == "unparsed" for i in items)
    await sessions.add_draft_items(db_session, session, items)
    assert await _count(db_session, 4101) == 0


async def test_same_report_twice_no_duplicates(db_session):
    uid = 4102
    await make_user(db_session, uid)
    items = parser.parse_text(clean_report(REPORT).body, lang="ru")
    session = await sessions.get_or_create_draft(db_session, uid, source="text")
    session = await sessions.add_draft_items(db_session, session, items)
    first = await _count(db_session, uid)
    assert first == 8
    await sessions.add_draft_items(db_session, session, items)
    assert await _count(db_session, uid) == first
    names = (await db_session.scalars(select(CanonicalExercise.display_name).where(CanonicalExercise.user_id == uid))).all()
    assert all(":" not in n for n in names)


async def test_new_draft_survives_session_close(db_session):
    """Regression: a draft created by one DB session was only flushed, so the next request hit an FK violation."""
    from src.database.models import WorkoutSession

    await make_user(db_session, 4103)
    session = await sessions.get_or_create_draft(db_session, 4103, source="text")
    sid = session.id
    await db_session.rollback()  # what closing a request's session without commit does
    assert await db_session.get(WorkoutSession, sid) is not None
