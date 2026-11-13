"""Regression tests: extra set beyond plan, plan status lifecycle, elapsed-time formatting."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.database.models import TrainingPlan, WorkoutSession, WorkoutSet
from src.handlers.training.live import _elapsed_str
from src.services.training import sessions
from src.services.training.dto import ParsedSet
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


async def _start(db, uid):
    await make_user(db, uid)
    plan = TrainingPlan(
        user_id=uid, local_date=datetime.now(UTC).date(), status="proposed", focus=["back"],
        items=[{"name": "Latzug", "kind": "strength", "working": [{"weight_kg": 40.0, "reps": 12}]}],
        rationale="t",
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan, await sessions.start_from_plan(db, uid, plan.id)


async def test_log_extra_set_beyond_plan(db_session):
    plan, s = await _start(db_session, 3101)
    entry = s.entries[0]
    s = await sessions.log_set(db_session, s.id, s.revision, entry.id, ParsedSet(weight_kg=40.0, reps=12))
    s = await sessions.log_set(db_session, s.id, s.revision, entry.id, ParsedSet(weight_kg=40.0, reps=10))
    sets = (await db_session.scalars(
        select(WorkoutSet).where(WorkoutSet.entry_id == entry.id).order_by(WorkoutSet.idx)
    )).all()
    assert [x.idx for x in sets] == [1, 2] and all(x.completed for x in sets)


async def test_plan_done_on_finish(db_session):
    plan, s = await _start(db_session, 3102)
    assert (await db_session.get(TrainingPlan, plan.id)).status == "in_progress"
    s = await sessions.log_set(db_session, s.id, s.revision, s.entries[0].id, ParsedSet(weight_kg=40.0, reps=12))
    await sessions.finish_session(db_session, s.id, s.revision)
    await db_session.refresh(plan)
    assert plan.status == "done"


async def test_plan_restorable_on_cancel(db_session):
    plan, s = await _start(db_session, 3103)
    await sessions.cancel_session(db_session, s.id, s.revision)
    await db_session.refresh(plan)
    assert plan.status == "proposed"


def test_elapsed_str():
    now = datetime.now(UTC).replace(tzinfo=None)
    assert _elapsed_str(None) == "0:00"
    assert _elapsed_str(now - timedelta(minutes=5, seconds=7)) in ("5:07", "5:08")
    assert _elapsed_str(now - timedelta(hours=1, minutes=2)).startswith("1:02:")


# ---- UX: hub progress, live card title, "last time" hint ----
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from src.handlers.training import hub, live
from tests.integration.test_training_v2_scenarios import fake_message


def _state(uid):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))


async def _two_ex_plan(db, uid):
    await make_user(db, uid)
    plan = TrainingPlan(
        user_id=uid, local_date=datetime.now(UTC).date(), status="proposed", focus=["back", "chest"],
        items=[
            {"name": "Latzug", "kind": "strength", "working": [{"weight_kg": 40.0, "reps": 12}]},
            {"name": "Rudern", "kind": "strength", "working": [{"weight_kg": 45.0, "reps": 10}]},
        ],
        rationale="t",
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return await sessions.start_from_plan(db, uid, plan.id)


async def test_hub_shows_real_active_progress(db_session):
    s = await _two_ex_plan(db_session, 3201)
    first = sorted(s.entries, key=lambda e: e.position)[0]
    await sessions.log_set(db_session, s.id, s.revision, first.id, ParsedSet(weight_kg=40.0, reps=12))
    await db_session.commit()
    msg = fake_message(uid=3201)
    await hub.render_and_send_hub(msg, 3201, _state(3201), edit=False)
    text = msg.answer.call_args[0][0]
    assert "Rudern" in text and "2/2" in text and "1/2" not in text


async def test_live_card_title_and_last_time_hint(db_session):
    # previous finished workout for Latzug: 40 x 12
    s0 = await _two_ex_plan(db_session, 3202)
    e0 = sorted(s0.entries, key=lambda e: e.position)[0]
    s0 = await sessions.log_set(db_session, s0.id, s0.revision, e0.id, ParsedSet(weight_kg=40.0, reps=12))
    await sessions.finish_session(db_session, s0.id, s0.revision)
    await db_session.commit()
    plan = (await db_session.scalars(select(TrainingPlan).where(TrainingPlan.user_id == 3202))).first()
    s1 = await sessions.start_from_plan(db_session, 3202, plan.id)
    await db_session.commit()

    msg = fake_message(uid=3202)
    await live.render_current_live_card(msg, s1, _state(3202), edit=False)
    card = msg.answer.call_args[0][0]
    assert "Сессия #" not in card
    assert "Спина" in card and "Грудь" in card
    assert "прошлый раз: 40 × 12" in card


# ---- i18n ----
from src.handlers.training import TrainingLangMiddleware
from src.utils import training_locales as tl


def test_every_key_has_all_languages_and_matching_placeholders():
    import re
    ph = lambda x: sorted(re.findall(r"\{[a-z_0-9:.]+\}", x))
    for key, vals in tl.COPY.items():
        assert len(vals) == len(tl.LANGS), key
        assert all(v.strip() for v in vals), key
        base = ph(vals[0])
        for lang, v in zip(tl.LANGS, vals):
            assert ph(v) == base, (key, lang)


def test_render_and_keyboard_follow_language():
    from src.handlers.training.keyboards import get_live_logging_keyboard
    from src.services.training import render
    for lang, word in (("en", "Rest"), ("de", "Pause"), ("uk", "Відпочинок")):
        token = tl.current_lang.set(lang)
        try:
            kb = get_live_logging_keyboard(1, 1, 40.0, 10)
            texts = " ".join(b.text for row in kb.inline_keyboard for b in row)
            assert word in texts
            assert not any("а" <= c.lower() <= "я" for c in texts) or lang == "uk"
            assert "Тренировк" not in render.render_hub("x", 1, 3) or lang == "ru"
        finally:
            tl.current_lang.reset(token)


async def test_middleware_sets_language_from_user_profile(db_session):
    u = await make_user(db_session, 3301)
    u.language = "de"
    await db_session.commit()
    seen = {}

    async def handler(event, data):
        seen["lang"] = tl.current_lang.get()

    event = fake_message(uid=3301)
    await TrainingLangMiddleware()(handler, event, {})
    assert seen["lang"] == "de"
    assert tl.current_lang.get() == "ru"  # reset after the update


async def test_onboarding_and_profile_follow_language(db_session):
    import re
    from src.handlers.training import settings as st
    await make_user(db_session, 3401)
    msg = fake_message(uid=3401)
    token = tl.current_lang.set("de")
    try:
        await st.start_onboarding(msg, _state(3401))
    finally:
        tl.current_lang.reset(token)
    text = msg.answer.call_args[0][0]
    kb = msg.answer.call_args.kwargs["reply_markup"]
    shown = text + " ".join(b.text for row in kb.inline_keyboard for b in row)
    assert "Hauptziel" in text and "Überspringen" in shown
    assert not re.search("[А-Яа-я]", shown)


# ---- undo set / abort ----
from tests.integration.test_training_v2_scenarios import fake_callback


async def test_undo_last_set_restores_plan_target_and_removes_extra(db_session):
    plan, s = await _start(db_session, 3501)
    e = s.entries[0]
    assert await sessions.undo_last_set(db_session, s.id, s.revision, 3501) is None  # nothing logged
    s = await sessions.log_set(db_session, s.id, s.revision, e.id, ParsedSet(weight_kg=42.0, reps=11))
    s = await sessions.log_set(db_session, s.id, s.revision, e.id, ParsedSet(weight_kg=40.0, reps=9))  # extra set
    s = await sessions.undo_last_set(db_session, s.id, s.revision, 3501)  # removes the extra one
    sets = (await db_session.scalars(select(WorkoutSet).where(WorkoutSet.entry_id == e.id).order_by(WorkoutSet.idx))).all()
    assert len(sets) == 1 and sets[0].completed
    s = await sessions.undo_last_set(db_session, s.id, s.revision, 3501)  # un-completes the planned one
    await db_session.refresh(sets[0])
    assert sets[0].completed is False and sets[0].target_reps == 12


async def test_undo_rejects_other_user_and_stale_revision(db_session):
    plan, s = await _start(db_session, 3502)
    with pytest.raises(ValueError):
        await sessions.undo_last_set(db_session, s.id, s.revision, 9999)
    with pytest.raises(sessions.StaleRevisionError):
        await sessions.undo_last_set(db_session, s.id, s.revision + 5, 3502)


async def test_abort_flow_deletes_session_and_restores_plan(db_session):
    plan, s = await _start(db_session, 3503)
    sid, rev, pid = s.id, s.revision, plan.id
    cb = fake_callback(uid=3503, data=f"t2:abort_live:{sid}:{rev}")
    await live.handle_abort_live(cb, _state(3503))
    assert "?" in cb.message.edit_text.call_args[0][0]  # confirmation shown, nothing deleted yet
    assert await db_session.get(WorkoutSession, sid) is not None

    cb2 = fake_callback(uid=3503, data=f"t2:abort_yes:{sid}:{rev}")
    await live.handle_abort_confirmed(cb2, _state(3503))
    db_session.expire_all()
    assert await db_session.get(WorkoutSession, sid) is None
    assert (await db_session.get(TrainingPlan, pid)).status == "proposed"


async def test_abort_yes_ignores_foreign_session(db_session):
    plan, s = await _start(db_session, 3504)
    sid, rev = s.id, s.revision
    cb = fake_callback(uid=9998, data=f"t2:abort_yes:{sid}:{rev}")
    await live.handle_abort_confirmed(cb, _state(9998))
    db_session.expire_all()
    assert await db_session.get(WorkoutSession, sid) is not None


# ---- finishing a live workout early ----
from src.database.models import WorkoutSessionEntry


async def test_early_finish_drops_unperformed_sets_and_keeps_exercise_feeling(db_session):
    s = await _two_ex_plan(db_session, 3601)
    first, second = sorted(s.entries, key=lambda e: e.position)
    fid, sid_second = first.id, second.id
    s = await sessions.log_set(db_session, s.id, s.revision, fid, ParsedSet(weight_kg=40.0, reps=12))
    first_entry = await db_session.get(WorkoutSessionEntry, fid)
    first_entry.feeling = "hard"  # what handle_exercise_feeling stores
    await db_session.commit()
    s = await sessions.get_session_full(db_session, s.id)

    done = await sessions.finish_session(db_session, s.id, s.revision)
    assert done.status == "finished" and done.feeling == "hard"
    assert [e.id for e in done.entries] == [fid]  # the untouched 2nd exercise is gone
    assert all(ws.completed for ws in done.entries[0].workout_sets)
    assert done.entries[0].feeling == "hard"  # not overwritten with "normal"
    assert await db_session.get(WorkoutSessionEntry, sid_second) is None


async def test_finish_live_without_any_completed_set_raises(db_session):
    s = await _two_ex_plan(db_session, 3602)
    with pytest.raises(sessions.EmptyWorkoutError):
        await sessions.finish_session(db_session, s.id, s.revision)
    assert (await db_session.get(WorkoutSession, s.id)).status == "active"


async def test_finish_live_handler_discards_empty_workout(db_session):
    s = await _two_ex_plan(db_session, 3603)
    sid, rev = s.id, s.revision
    cb = fake_callback(uid=3603, data=f"t2:finish_live:{sid}:{rev}")
    await live.handle_finish_live(cb, _state(3603))
    db_session.expire_all()
    assert await db_session.get(WorkoutSession, sid) is None
    cb.answer.assert_awaited()


async def test_log_set_returns_fresh_tree_in_few_queries(db_session):
    """Regression for the double reload: one tap must stay cheap and the returned tree must show the new set."""
    from sqlalchemy import event
    plan, s = await _start(db_session, 3701)
    e = s.entries[0]
    await db_session.commit()
    engine = db_session.bind.sync_engine
    count = []
    hook = lambda conn, cur, stmt, *a: count.append(stmt)
    event.listen(engine, "before_cursor_execute", hook)
    try:
        s = await sessions.log_set(db_session, s.id, s.revision, e.id, ParsedSet(weight_kg=40.0, reps=12))
        s = await sessions.log_set(db_session, s.id, s.revision, e.id, ParsedSet(weight_kg=40.0, reps=8))  # extra set
    finally:
        event.remove(engine, "before_cursor_execute", hook)
    entry = next(x for x in s.entries if x.id == e.id)
    assert [x.idx for x in sorted(entry.workout_sets, key=lambda x: x.idx)] == [1, 2]  # extra set visible, no stale tree
    assert len(count) <= 24, len(count)


# ---- edit_or_send ----
from unittest.mock import AsyncMock
from types import SimpleNamespace
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText

from src.handlers.training.ui import edit_or_send


def _msg(edit_exc=None):
    return SimpleNamespace(
        message_id=10,
        edit_text=AsyncMock(side_effect=edit_exc),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=77)),
    )


async def test_edit_or_send_edits_in_place():
    m = _msg()
    assert await edit_or_send(m, "x") == 10
    m.answer.assert_not_awaited()


async def test_edit_or_send_not_modified_is_not_an_error():
    m = _msg(TelegramBadRequest(EditMessageText(text="x"), "Bad Request: message is not modified"))
    assert await edit_or_send(m, "x") == 10
    m.answer.assert_not_awaited()


async def test_edit_or_send_falls_back_to_new_message_when_edit_refused():
    m = _msg(TelegramBadRequest(EditMessageText(text="x"), "Bad Request: message to edit not found"))
    assert await edit_or_send(m, "x", reply_markup="kb") == 77
    m.answer.assert_awaited_once()


async def test_edit_or_send_lets_network_errors_propagate():
    m = _msg(TelegramNetworkError(EditMessageText(text="x"), "boom"))
    with pytest.raises(TelegramNetworkError):
        await edit_or_send(m, "x")


# ---- rest timer ----
async def test_rest_timer_schedules_localized_message_with_exercise_name(db_session):
    from unittest.mock import patch
    plan, s = await _start(db_session, 3801)
    eid = s.entries[0].id
    st = _state(3801)
    await st.update_data(current_entry_id=eid)
    cb = fake_callback(uid=3801, data=f"t2:rest_timer:{s.id}:{s.revision}:60")
    with patch("src.services.scheduler.scheduler") as sched:
        await live.handle_rest_timer(cb, st)
    cb.answer.assert_awaited_once()
    args = sched.add_job.call_args.kwargs["args"]
    assert "Latzug" in args[2] and "60" in args[2]


def test_rest_suggestion_differs_for_isolation_and_compound():
    from types import SimpleNamespace as NS
    from src.handlers.training.keyboards import get_live_logging_keyboard
    assert live._suggested_rest_sec(NS(pattern="isolation")) == 75
    assert live._suggested_rest_sec(NS(pattern="squat")) == 120
    assert live._suggested_rest_sec(None) == 120
    kb = get_live_logging_keyboard(1, 1, 40.0, 10, rest_sec=75)
    data = [b.callback_data for row in kb.inline_keyboard for b in row if "rest_timer" in (b.callback_data or "")]
    assert data == ["t2:rest_timer:1:1:75"]


async def test_rest_timer_ignores_foreign_entry_name(db_session):
    from unittest.mock import patch
    plan, s = await _start(db_session, 3802)
    st = _state(9997)
    await st.update_data(current_entry_id=s.entries[0].id)
    cb = fake_callback(uid=9997, data=f"t2:rest_timer:{s.id}:{s.revision}:60")
    with patch("src.services.scheduler.scheduler") as sched:
        await live.handle_rest_timer(cb, st)
    assert "Latzug" not in sched.add_job.call_args.kwargs["args"][2]


# ---- auto rest timer ----
async def test_rest_pref_toggle_and_auto_timer_after_set(db_session):
    from unittest.mock import patch
    from src.database.models import TrainingProfile
    plan = await _two_ex_plan(db_session, 3901)  # fixture returns the started session
    sid = plan.id
    first_id = sorted(plan.entries, key=lambda e: e.position)[0].id
    db_session.add(TrainingProfile(user_id=3901, goal="hypertrophy", days_per_week=3))
    await db_session.commit()

    # toggle on through the real handler
    from src.handlers.training import settings as st
    tg = fake_callback(uid=3901, data="t2:tgl_rest:0:0")
    await st.handle_toggle_rest_pref(tg, _state(3901))
    db_session.expire_all()
    assert (await db_session.get(TrainingProfile, 3901)).rest_timer is True
    markup = tg.message.edit_reply_markup.call_args.kwargs["reply_markup"]
    assert "ON" in markup.inline_keyboard[0][0].text or "ВКЛ" in markup.inline_keyboard[0][0].text

    # a mid-exercise set schedules a ping; the LAST set of an exercise does not (feeling prompt comes instead)
    db_session.add(WorkoutSet(entry_id=first_id, idx=2, set_type="working", target_reps=10, target_weight_kg=40, completed=False))
    await db_session.commit()
    st_fsm = _state(3901)
    await st_fsm.update_data(current_entry_id=first_id, current_w=40.0, current_r=12)
    session = await sessions.get_session_full(db_session, sid)
    with patch("src.services.scheduler.scheduler") as sched:
        cb = fake_callback(uid=3901, data=f"t2:ok_set:{sid}:{session.revision}")
        await live.handle_log_set_ok(cb, st_fsm)
        assert sched.add_job.called
    assert "120" in cb.answer.call_args[0][0] or "75" in cb.answer.call_args[0][0]


async def test_no_auto_timer_when_preference_off(db_session):
    from unittest.mock import patch
    from src.database.models import TrainingProfile
    s = await _two_ex_plan(db_session, 3902)
    first = sorted(s.entries, key=lambda e: e.position)[0]
    db_session.add(TrainingProfile(user_id=3902, goal="hypertrophy", days_per_week=3))
    db_session.add(WorkoutSet(entry_id=first.id, idx=2, set_type="working", target_reps=10, target_weight_kg=40, completed=False))
    await db_session.commit()
    st_fsm = _state(3902)
    await st_fsm.update_data(current_entry_id=first.id, current_w=40.0, current_r=12)
    s = await sessions.get_session_full(db_session, s.id)
    with patch("src.services.scheduler.scheduler") as sched:
        await live.handle_log_set_ok(fake_callback(uid=3902, data=f"t2:ok_set:{s.id}:{s.revision}"), st_fsm)
    assert not sched.add_job.called


# ---- ownership across the live/draft API ----
async def test_service_calls_reject_foreign_user(db_session):
    plan, s = await _start(db_session, 4001)
    sid, rev, eid = s.id, s.revision, s.entries[0].id
    assert await sessions.get_session_full(db_session, sid, user_id=9999) is None
    assert (await sessions.get_session_full(db_session, sid, user_id=4001)).id == sid
    with pytest.raises(sessions.StaleRevisionError):
        await sessions.log_set(db_session, sid, rev, eid, ParsedSet(weight_kg=1, reps=1), user_id=9999)
    with pytest.raises(sessions.StaleRevisionError):
        await sessions.skip_entry(db_session, sid, rev, eid, user_id=9999)
    with pytest.raises(sessions.StaleRevisionError):
        await sessions.finish_session(db_session, sid, rev, user_id=9999)
    await sessions.cancel_session(db_session, sid, rev, user_id=9999)  # ignored, not deleted
    assert await db_session.get(WorkoutSession, sid) is not None


async def test_foreign_user_cannot_log_set_or_cancel_draft_via_callbacks(db_session):
    plan, s = await _start(db_session, 4002)
    sid, rev, eid = s.id, s.revision, s.entries[0].id
    st = _state(9996)
    await st.update_data(current_entry_id=eid, current_w=40.0, current_r=12)
    cb = fake_callback(uid=9996, data=f"t2:ok_set:{sid}:{rev}")
    await live.handle_log_set_ok(cb, st)
    cb2 = fake_callback(uid=9996, data=f"t2:cancel_draft:{sid}:{rev}")
    from src.handlers.training import log_text
    await log_text.handle_cancel_draft(cb2, st)
    db_session.expire_all()
    remaining = (await db_session.scalars(select(WorkoutSet).where(WorkoutSet.entry_id == eid))).all()
    assert remaining and not any(x.completed for x in remaining)
    assert await db_session.get(WorkoutSession, sid) is not None


async def test_undo_draft_uses_eagerly_loaded_entries(db_session):
    """undo_draft used a lazy `session.entries`, which raises MissingGreenlet on a fresh async session."""
    from src.handlers.training import log_text
    from src.services.training.dto import DraftItem
    await make_user(db_session, 4003)
    draft = await sessions.get_or_create_draft(db_session, 4003)
    await sessions.add_draft_items(db_session, draft, [
        DraftItem(line_no=1, raw="a", name="Latzug", kind="strength", status="ok", sets=[ParsedSet(weight_kg=40, reps=10)]),
        DraftItem(line_no=2, raw="b", name="Rudern", kind="strength", status="ok", sets=[ParsedSet(weight_kg=30, reps=10)]),
    ])
    await db_session.commit()
    sid = draft.id
    rev = (await db_session.get(WorkoutSession, sid)).revision
    db_session.expunge_all()  # emulate a fresh handler session: nothing preloaded
    cb = fake_callback(uid=4003, data=f"t2:undo_draft:{sid}:{rev}")
    await log_text.handle_undo_last_draft_item(cb, _state(4003))
    db_session.expire_all()
    names = (await db_session.scalars(select(WorkoutSessionEntry).where(WorkoutSessionEntry.session_id == sid))).all()
    assert len(names) == 1


async def test_complete_entry_as_planned_skips_unknown_sets(db_session):
    plan, s = await _start(db_session, 3190)
    entry = s.entries[0]
    rev_before = s.revision
    extra_known = WorkoutSet(entry_id=entry.id, idx=90, set_type="working", target_weight_kg=40, target_reps=12, completed=False)
    unknown = WorkoutSet(entry_id=entry.id, idx=91, set_type="working", completed=False)
    db_session.add_all([extra_known, unknown])
    await db_session.commit()

    out = await sessions.complete_entry_as_planned(db_session, s.id, s.revision, entry.id, user_id=3190)
    done = {ws.idx: ws.completed for ws in out.entries[0].workout_sets}
    assert done[90] is True and done[91] is False
    assert all(ws.completed for ws in out.entries[0].workout_sets if ws.idx != 91)
    assert out.revision == rev_before + 1


async def test_complete_entry_as_planned_stale_revision(db_session):
    _, s = await _start(db_session, 3191)
    with pytest.raises(sessions.StaleRevisionError):
        await sessions.complete_entry_as_planned(db_session, s.id, s.revision + 5, s.entries[0].id, user_id=3191)


async def test_draft_save_keeps_training_hub_and_ux_progress_delegates_to_history(db_session):
    """After saving a draft workout, state stays TrainingFSM.hub and tapping '📈 Прогресс' shows training history."""
    from src.database.models import CanonicalExercise, WorkoutSessionEntry
    from src.handlers import ux as ux_handlers
    from src.handlers.training import log_text, progress
    from src.handlers.training.states import TrainingFSM

    await make_user(db_session, 4101)
    st = _state(4101)
    await st.set_state(TrainingFSM.draft)

    draft = await sessions.get_or_create_draft(db_session, 4101, source="text")
    ce = CanonicalExercise(user_id=4101, canonical_slug="latzug", display_name="Latzug", kind="strength")
    db_session.add(ce)
    await db_session.flush()
    entry = WorkoutSessionEntry(session_id=draft.id, canonical_exercise_id=ce.id, raw_input="Latzug", position=0)
    db_session.add(entry)
    await db_session.flush()
    db_session.add(WorkoutSet(entry_id=entry.id, idx=1, set_type="working", weight_kg=45.0, reps=12, completed=False))
    await db_session.commit()

    cb_save = fake_callback(uid=4101, data=f"t2:save_draft:{draft.id}:{draft.revision}")
    await log_text.handle_save_draft(cb_save, st)
    assert await st.get_state() == TrainingFSM.hub.state

    # Tapping reply '📈 Прогресс' goes through ux.progress_section, which must delegate to training progress
    msg_prog = fake_message(uid=4101, text="📈 Прогресс")
    await ux_handlers.progress_section(msg_prog, st, "ru")
    msg_prog.answer.assert_awaited_once()
    rendered = msg_prog.answer.call_args[0][0]
    assert "Всего тренировок" in rendered
    assert "Latzug" in rendered
    kb = msg_prog.answer.call_args.kwargs["reply_markup"]
    hist_cb_data = next(
        b.callback_data for row in kb.inline_keyboard for b in row
        if (b.callback_data or "").startswith("t2:hist_view:")
    )
    cb_detail = fake_callback(uid=4101, data=hist_cb_data)
    await progress.handle_history_detail(cb_detail, st)
    detail_html = cb_detail.message.edit_text.call_args[0][0]
    assert "Latzug" in detail_html and "45×12" in detail_html


async def test_start_workout_tags_untagged_and_uses_covered_groups_when_no_legs(db_session):
    """If user only added upper-body machines (untagged), '▶️ Начать' tags them and builds a plan instead of failing."""
    from src.database.models import CanonicalExercise, TrainingProfile
    from src.handlers.training.states import TrainingFSM

    await make_user(db_session, 4102)
    db_session.add(TrainingProfile(user_id=4102, goal="hypertrophy", days_per_week=3, session_minutes=45))
    # Untouched muscle_primary (None) matching catalog entry 'Latzug' and 'Brustpresse'
    db_session.add(CanonicalExercise(user_id=4102, canonical_slug="latzug", display_name="Latzug", kind="strength"))
    db_session.add(CanonicalExercise(user_id=4102, canonical_slug="brustpresse", display_name="Brustpresse", kind="strength"))
    await db_session.commit()

    st = _state(4102)
    msg = fake_message(uid=4102, text="▶️ Начать")
    await hub.handle_start_workout(msg, st)

    assert await st.get_state() == TrainingFSM.plan.state
    rendered = msg.answer.call_args[0][0]
    assert "Latzug" in rendered or "Brustpresse" in rendered


async def test_webapp_workout_progress_includes_training_v2_sessions(db_session):
    """WebApp workouts dashboard includes finished Training v2 WorkoutSessions and CanonicalExercises."""
    from src.database.models import CanonicalExercise, WorkoutSessionEntry
    from src.services import workout_progress

    user = await make_user(db_session, 4103)
    ce = CanonicalExercise(user_id=4103, canonical_slug="bench_press", display_name="Жим лёжа", kind="strength")
    db_session.add(ce)
    await db_session.flush()
    now = datetime.now(UTC).replace(tzinfo=None)
    sess = WorkoutSession(
        user_id=4103, status="finished", source="text", performed_at=now, started_at=now, finished_at=now, feeling="normal"
    )
    db_session.add(sess)
    await db_session.flush()
    entry = WorkoutSessionEntry(session_id=sess.id, canonical_exercise_id=ce.id, raw_input="Жим лёжа", position=0)
    db_session.add(entry)
    await db_session.flush()
    db_session.add(WorkoutSet(entry_id=entry.id, idx=1, set_type="working", weight_kg=60.0, reps=10, completed=True))
    await db_session.commit()

    data = await workout_progress.dashboard(db_session, user, exercise_id=-ce.id)
    assert data["totals"]["sessions"] == 1
    assert len(data["history"]) == 1
    assert data["history"][0]["entries"][0]["name"] == "Жим лёжа"
    assert any(item["id"] == -ce.id and item["name"] == "Жим лёжа" for item in data["library"])
    assert len(data["progress"]) == 1
    assert data["progress"][0]["metrics"]["max_weight"] == 60.0


async def test_webapp_today_plan_v2_configure_generate_and_accept_starts_live_session(db_session, monkeypatch):
    """WebApp Today's Plan configures, generates and accepts via Training v2 (TrainingProfile + CanonicalExercise)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from aiohttp.test_utils import TestClient, TestServer

    from src.database.models import CanonicalExercise, TrainingPlan, TrainingProfile
    from src.services import gemini
    from src.webapp import server
    from tests.integration.test_security_and_meal_recovery import signed_data

    user = await make_user(db_session, 4104)
    db_session.add(TrainingProfile(user_id=4104, goal="hypertrophy", days_per_week=3, session_minutes=45))
    db_session.add_all([
        CanonicalExercise(user_id=4104, canonical_slug="latzug", display_name="Latzug", kind="strength", muscle_primary="back_lats", pattern="pull_v"),
        CanonicalExercise(user_id=4104, canonical_slug="brustpresse", display_name="Brustpresse", kind="strength", muscle_primary="chest", pattern="push_h"),
    ])
    await db_session.commit()

    monkeypatch.setattr(gemini, "call_gemini_with_retry", AsyncMock(return_value=SimpleNamespace(text='{"rationale": "Фокус на верх тела.", "cues": {}}')))
    headers = {"X-Telegram-Init-Data": signed_data(4104)}

    async with TestClient(TestServer(server.create_app(None))) as client:
        res = await client.get("/api/workouts", headers=headers)
        assert res.status == 200
        initial = await res.json()
        assert initial["preferences"]["sessions_per_week"] == 3
        assert initial["preferences"]["minutes"] == 45
        assert initial["today_plan"] is None

        # Updating frequency syncs both User.training_preferences and TrainingProfile
        res_pref = await client.post("/api/workouts/preferences", headers=headers, json={"sessions_per_week": 4})
        assert res_pref.status == 200
        db_session.expire_all()
        assert (await db_session.get(TrainingProfile, 4104)).days_per_week == 4

        res_cfg = await client.post(
            "/api/workouts/configure",
            headers=headers,
            json={"revision": 0, "preferences": {"focus": "upper", "minutes": 45, "effort": "normal", "wishes": ""}},
        )
        assert res_cfg.status == 200
        cfg = await res_cfg.json()
        assert cfg["status"] == "draft"
        assert cfg["preferences"]["focus"] == "upper"

        res_gen = await client.post(f"/api/workouts/today/{cfg['id']}/generate", headers=headers, json={"revision": cfg["revision"]})
        assert res_gen.status == 200
        gen = await res_gen.json()
        assert gen["status"] == "proposed"
        assert gen["items"]
        assert all(it["exercise_id"] < 0 for it in gen["items"])
        assert "Фокус на верх тела." in gen["summary"]

        # Accept starts a Training v2 active WorkoutSession visible on subsequent dashboard load
        res_acc = await client.post(f"/api/workouts/today/{cfg['id']}/accept", headers=headers, json={"revision": gen["revision"]})
        assert res_acc.status == 200
        acc = await res_acc.json()
        assert acc["ok"] is True

        # Idempotent repeat accept with previous revision returns same session
        res_acc_dup = await client.post(f"/api/workouts/today/{cfg['id']}/accept", headers=headers, json={"revision": gen["revision"]})
        assert res_acc_dup.status == 200
        assert (await res_acc_dup.json())["workout_id"] == acc["workout_id"]

        res_after = await client.get("/api/workouts", headers=headers)
        assert res_after.status == 200
        after = await res_after.json()
        assert after["today_plan"]["status"] == "accepted"
        assert after["active"]["id"] == acc["workout_id"]
        assert after["active"]["status"] == "active"

        db_session.expire_all()
        stored_plan = await db_session.get(TrainingPlan, cfg["id"])
        assert stored_plan.status == "in_progress"

        # Foreign user cannot act on another user's TrainingPlan
        await make_user(db_session, 4105)
        foreign_headers = {"X-Telegram-Init-Data": signed_data(4105)}
        assert (await client.post(f"/api/workouts/today/{cfg['id']}/accept", headers=foreign_headers, json={"revision": gen["revision"]})).status == 409


async def test_webapp_today_plan_v2_pain_returns_safety_without_items(db_session):
    """WebApp Today's Plan v2 checks pain keywords in wishes before proposing exercises."""
    from aiohttp.test_utils import TestClient, TestServer
    from src.database.models import CanonicalExercise, TrainingProfile
    from src.webapp import server
    from tests.integration.test_security_and_meal_recovery import signed_data

    await make_user(db_session, 4106)
    db_session.add(TrainingProfile(user_id=4106, goal="hypertrophy", days_per_week=3, session_minutes=45))
    db_session.add(CanonicalExercise(user_id=4106, canonical_slug="latzug", display_name="Latzug", kind="strength", muscle_primary="back_lats", pattern="pull_v"))
    await db_session.commit()

    headers = {"X-Telegram-Init-Data": signed_data(4106)}
    async with TestClient(TestServer(server.create_app(None))) as client:
        res_cfg = await client.post(
            "/api/workouts/configure",
            headers=headers,
            json={"revision": 0, "preferences": {"focus": "custom", "minutes": 40, "effort": "normal", "wishes": "Болит спина"}},
        )
        assert res_cfg.status == 200
        cfg = await res_cfg.json()
        res_gen = await client.post(f"/api/workouts/today/{cfg['id']}/generate", headers=headers, json={"revision": cfg["revision"]})
        assert res_gen.status == 200
        gen = await res_gen.json()
        assert gen["status"] == "proposed"
        assert gen["items"] == []
        assert gen["summary"]
