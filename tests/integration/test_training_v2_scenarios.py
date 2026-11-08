"""Integration tests for Training v2: Scenarios A, B, C, Planner, and Error Fallbacks."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    CanonicalExercise,
    ExerciseAlias,
    TrainingPlan,
    TrainingProfile,
    User,
    WorkoutSession,
    WorkoutSessionEntry,
    WorkoutSet,
)
from src.handlers.training import hub, live, log_text, plan, repeat
from src.handlers.training.states import TrainingFSM
from src.services.training import focus, planner, sessions
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


def fake_message(uid=123, text="", bot=None, msg_id=999):
    b = bot or SimpleNamespace(
        send_message=AsyncMock(),
        edit_message_text=AsyncMock(),
        delete_message=AsyncMock(),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(id=uid),
        from_user=SimpleNamespace(id=uid, language_code="ru"),
        text=text,
        date=datetime.now(UTC),
        answer=AsyncMock(return_value=SimpleNamespace(message_id=msg_id)),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        delete=AsyncMock(),
        bot=b,
        message_id=msg_id,
    )


def fake_callback(uid=123, data="", message=None):
    msg = message or fake_message(uid=uid, msg_id=888)
    return SimpleNamespace(
        from_user=SimpleNamespace(id=uid, language_code="ru"),
        data=data,
        message=msg,
        answer=AsyncMock(),
        bot=msg.bot,
    )


async def test_scenario_b_seven_lines_to_eight_exercises_and_save(db_session):
    """Scenario B: 7 input lines parse into 8 exercises, 23 sets, and saves properly."""
    u = await make_user(db_session, 2001)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=2001, user_id=2001))
    await state.set_state(TrainingFSM.draft)
    await state.update_data(anchor_message_id=555)

    input_text = (
        "Ellipsentrainer 20 минут кросс тренинг\n"
        "Brustpresse 35кг x 4 подхода по 15 раз\n"
        "Latzug 40кг 3 подхода по 15 раз\n"
        "Rudermaschine 45 кг 3 подхода по 12 раз\n"
        "Beinstrecker 40 кг 3 подхода по 12 раз\n"
        "Abduktion и  Adduktion 40 kg x 3 x 15\n"
        "Bizepsmaschine 15x3x15kg."
    )

    msg = fake_message(uid=2001, text=input_text)
    await log_text.handle_draft_text_input(msg, state)

    # 1. User message was deleted
    msg.delete.assert_awaited_once()

    # 2. Check draft in database
    data = await state.get_data()
    sess_id = data.get("session_id")
    assert sess_id is not None

    session = await db_session.get(WorkoutSession, sess_id)
    assert session is not None
    assert session.status == "draft"

    stmt = select(WorkoutSessionEntry).where(WorkoutSessionEntry.session_id == sess_id).order_by(WorkoutSessionEntry.position.asc())
    entries = (await db_session.scalars(stmt)).all()
    # 8 exercises (Abduktion & Adduktion split)
    assert len(entries) == 8

    # Total sets across all 8 entries = 1 (cardio) + 4 (Brustpresse) + 3*6 = 23
    set_count_stmt = select(WorkoutSet).join(WorkoutSessionEntry).where(WorkoutSessionEntry.session_id == sess_id)
    sets = (await db_session.scalars(set_count_stmt)).all()
    assert len(sets) == 23

    # 3. Save draft callback
    cb_save = fake_callback(uid=2001, data=f"t2:save_draft:{sess_id}:{session.revision}")
    await log_text.handle_save_draft(cb_save, state)

    cb_save.answer.assert_awaited_with("Сохранено ✓")

    finished = await db_session.get(WorkoutSession, sess_id)
    assert finished.status == "finished"
    assert finished.finished_at is not None


async def test_scenario_b_429_rate_limit_fallback_does_not_crash(db_session):
    """When Gemini returns 429, unparsed line stays marked in draft and user gets no crash."""
    u = await make_user(db_session, 2002)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=2002, user_id=2002))
    await state.set_state(TrainingFSM.draft)
    await state.update_data(anchor_message_id=556)

    input_text = "Непонятный странный тренажер abc 123"
    msg = fake_message(uid=2002, text=input_text)

    with patch("src.services.gemini.call_gemini_with_retry", side_effect=Exception("429 Quota Exceeded")):
        await log_text.handle_draft_text_input(msg, state)

    data = await state.get_data()
    sess_id = data.get("session_id")

    session = await db_session.get(WorkoutSession, sess_id)
    assert session is not None
    # Save still works
    cb_save = fake_callback(uid=2002, data=f"t2:save_draft:{sess_id}:{session.revision}")
    await log_text.handle_save_draft(cb_save, state)
    assert session.status == "finished"


async def test_scenario_a_live_plan_execution_and_idempotency(db_session):
    """Scenario A: start from plan, tap 9 sets, duplicate tap with old revision is safely ignored."""
    u = await make_user(db_session, 2003)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=2003, user_id=2003))

    # Create proposed plan with 3 exercises, 3 sets each = 9 sets
    plan_record = TrainingPlan(
        user_id=2003,
        local_date=datetime.now(UTC).date(),
        status="proposed",
        focus=["back"],
        items=[
            {
                "name": "Latzug",
                "kind": "strength",
                "working": [{"weight_kg": 40.0, "reps": 12} for _ in range(3)],
            },
            {
                "name": "Rudermaschine",
                "kind": "strength",
                "working": [{"weight_kg": 45.0, "reps": 10} for _ in range(3)],
            },
            {
                "name": "Bizepsmaschine",
                "kind": "strength",
                "working": [{"weight_kg": 15.0, "reps": 15} for _ in range(3)],
            },
        ],
        rationale="Test plan",
    )
    db_session.add(plan_record)
    await db_session.commit()
    await db_session.refresh(plan_record)

    cb_start = fake_callback(uid=2003, data=f"t2:start_plan:{plan_record.id}:0")
    await live.handle_start_or_resume_live(cb_start, state)

    data = await state.get_data()
    sess_id = data.get("session_id")
    assert sess_id is not None

    session = await db_session.get(WorkoutSession, sess_id)
    assert session.status == "active"
    curr_rev = session.revision

    # 1. Tap ✅ once
    cb_set1 = fake_callback(uid=2003, data=f"t2:ok_set:{sess_id}:{curr_rev}")
    await live.handle_log_set_ok(cb_set1, state)
    cb_set1.answer.assert_awaited_with("Подход записан ✓")

    # 2. Duplicate tap with the old rev -> ignored / "Экран устарел"
    cb_dup = fake_callback(uid=2003, data=f"t2:ok_set:{sess_id}:{curr_rev}")
    await live.handle_log_set_ok(cb_dup, state)
    cb_dup.answer.assert_awaited_with("Экран устарел, обновил")

    # 3. Test rest timer callback
    cb_rest = fake_callback(uid=2003, data=f"t2:rest_timer:{sess_id}:{curr_rev}:90")
    await live.handle_rest_timer(cb_rest, state)
    assert cb_rest.answer.called
    assert "Таймер запущен" in cb_rest.answer.call_args.args[0]

    # 4. Finish session early (even with old revision)
    cb_finish = fake_callback(uid=2003, data=f"t2:finish_live:{sess_id}:{curr_rev}")
    await live.handle_finish_live(cb_finish, state)
    cb_finish.answer.assert_awaited_with("Тренировка завершена ✓")

    finished_sess = await db_session.get(WorkoutSession, sess_id)
    assert finished_sess.status == "finished"


async def test_scenario_a_restart_preserves_live_session_in_hub(db_session):
    """If bot restarts during workout, user sees 'Resume workout' in Hub."""
    u = await make_user(db_session, 2004)
    storage = MemoryStorage()
    fresh_state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=2004, user_id=2004))

    # Create active session in DB
    now = datetime.now(UTC).replace(tzinfo=None)
    session = WorkoutSession(
        user_id=2004,
        status="active",
        source="live",
        performed_at=now,
        started_at=now,
        revision=5,
    )
    db_session.add(session)
    profile = TrainingProfile(user_id=2004, goal="hypertrophy", days_per_week=3)
    db_session.add(profile)
    await db_session.commit()

    msg = fake_message(uid=2004, text="🏋️ Тренировка")
    await hub.render_and_send_hub(msg, 2004, fresh_state, edit=False)

    msg.answer.assert_awaited_once()
    rendered_text = msg.answer.call_args[0][0]
    assert "Текущая тренировка" in rendered_text


async def test_scenario_c_repeat_previous_workout(db_session):
    """Scenario C: repeat previous session and uncheck one exercise."""
    u = await make_user(db_session, 2005)
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=2005, user_id=2005))

    # Create finished session with 3 exercises
    now = datetime.now(UTC).replace(tzinfo=None)
    session = WorkoutSession(
        user_id=2005, status="finished", source="text", performed_at=now, finished_at=now
    )
    db_session.add(session)
    await db_session.flush()

    for idx, name in enumerate(["Brustpresse", "Latzug", "Rudermaschine"]):
        ex = CanonicalExercise(user_id=2005, canonical_slug=name.lower(), display_name=name)
        db_session.add(ex)
        await db_session.flush()
        entry = WorkoutSessionEntry(session_id=session.id, canonical_exercise_id=ex.id, raw_input=name, position=idx)
        db_session.add(entry)
        await db_session.flush()
        ws = WorkoutSet(entry_id=entry.id, idx=1, weight_kg=40.0, reps=10, completed=True)
        db_session.add(ws)
    await db_session.commit()

    cb_open = fake_callback(uid=2005, data="t2:repeat_menu")
    await repeat.open_repeat_menu(cb_open, state)

    # Uncheck index 1 (Latzug)
    cb_tgl = fake_callback(uid=2005, data="t2:rep_tgl:0:0:1")
    await repeat.handle_toggle_repeat_item(cb_tgl, state)

    # Save checked
    cb_save = fake_callback(uid=2005, data="t2:rep_save:0:0")
    await repeat.handle_save_checked_repeat_items(cb_save, state)

    cb_save.answer.assert_awaited_with("Тренировка сохранена ✓")

    stmt = (
        select(WorkoutSession)
        .options(selectinload(WorkoutSession.entries))
        .where(WorkoutSession.user_id == 2005, WorkoutSession.source == "repeat")
        .order_by(WorkoutSession.id.desc())
    )
    new_sess = (await db_session.scalars(stmt)).first()
    assert new_sess is not None
    assert len(new_sess.entries) == 2  # 3 - 1 unchecked = 2


async def test_planner_accent_and_limitations():
    """Focus 'упор на спину без поясницы' prioritizes back and excludes hinge / lower_back."""
    focus_obj = focus.resolve_focus(text="хочу упор на спину без поясницы", minutes=45)
    assert "lower_back" in focus_obj.limitations
    assert "back" in (focus_obj.accent or "")

    lib = [
        {"key": "lat_pulldown", "display_name": "Latzug", "muscle_primary": "back_lats", "pattern": "pull_v"},
        {"key": "seated_cable_row", "display_name": "Rudermaschine", "muscle_primary": "back_upper", "pattern": "pull_h"},
        {"key": "deadlift_barbell", "display_name": "Kreuzheben", "muscle_primary": "lower_back", "pattern": "hinge"},
    ]

    plan_items = planner.build_plan(
        profile=None,
        library_exercises=lib,
        history_index={},
        focus=focus_obj,
        minutes=45,
    )

    names = [i.name for i in plan_items]
    assert "Latzug" in names
    assert "Rudermaschine" in names
    assert "Kreuzheben" not in names


async def test_equipment_reply_keyboard_and_add_delete_flow(db_session):
    """Test docked reply keyboard in equipment section and adding/deleting machines."""
    from src.handlers.training import settings as training_settings
    from src.keyboards.reply import get_equipment_reply_keyboard, get_training_hub_reply_keyboard
    from src.services.training import library

    uid = 2006
    user = await make_user(db_session, user_id=uid)
    user.language = "ru"
    await db_session.commit()
    prof = TrainingProfile(user_id=uid, goal="strength", experience="intermediate", days_per_week=3, session_minutes=45, split="auto", limitations=[])
    db_session.add(prof)
    await db_session.commit()

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))

    # 1. Open equipment section via reply text
    msg_open = fake_message(uid=uid, text="⚙️ Тренажёры")
    await training_settings.render_equipment_settings(msg_open, state, user_id=uid)

    assert msg_open.answer.called
    sent_call = msg_open.answer.call_args_list[0]  # the card; a second message carries the inline rest toggle
    # Verify that bottom docked reply keyboard is sent
    reply_markup = sent_call.kwargs.get("reply_markup")
    assert reply_markup is not None
    # Check that buttons include Add and Delete
    button_texts = [btn.text for row in reply_markup.keyboard for btn in row]
    assert "➕ Добавить тренажёр" in button_texts
    assert "🗑 Удалить тренажёр" in button_texts
    assert "✏️ Настроить профиль" in button_texts
    assert "⬅️ К тренировкам" in button_texts

    # 2. Tap "➕ Добавить тренажёр"
    msg_add = fake_message(uid=uid, text="➕ Добавить тренажёр")
    await training_settings.handle_add_equipment_start(msg_add, state)
    current_state = await state.get_state()
    assert current_state == TrainingFSM.equipment_add.state

    # 3. Add via catalog callback (leg_press)
    cb_cat = fake_callback(uid=uid, data="t2:eq_add_cat:0:0:leg_press")
    await training_settings.handle_add_catalog_machine(cb_cat, state)

    active_lib = await library.get_user_library(db_session, uid)
    assert len(active_lib) == 1
    assert active_lib[0].catalog_key == "leg_press"

    # 4. Add custom machine via text input
    await state.set_state(TrainingFSM.equipment_add)
    msg_custom = fake_message(uid=uid, text="Жим в Смите")
    await training_settings.handle_add_custom_machine(msg_custom, state)

    active_lib = await library.get_user_library(db_session, uid)
    assert len(active_lib) == 2
    names = [ex.display_name for ex in active_lib]
    assert "Жим в Смите" in names

    # 5. Start delete flow
    msg_del = fake_message(uid=uid, text="🗑 Удалить тренажёр")
    await training_settings.handle_delete_equipment_start(msg_del, state)
    current_state = await state.get_state()
    assert current_state == TrainingFSM.equipment_delete.state

    # 6. Delete item via inline button callback
    target_ex = [e for e in active_lib if e.catalog_key == "leg_press"][0]
    cb_del = fake_callback(uid=uid, data=f"t2:eq_del_id:0:0:{target_ex.id}")
    await training_settings.handle_delete_equipment_item(cb_del, state)

    active_lib_after = await library.get_user_library(db_session, uid)
    assert len(active_lib_after) == 1
    assert active_lib_after[0].display_name == "Жим в Смите"

    # 7. Delete remaining item via text
    await state.set_state(TrainingFSM.equipment_delete)
    msg_del_text = fake_message(uid=uid, text="Жим в Смите")
    await training_settings.handle_delete_custom_machine(msg_del_text, state)

    active_lib_final = await library.get_user_library(db_session, uid)
    assert len(active_lib_final) == 0

    # 8. Navigation button "⬅️ К тренировкам" returns to hub
    msg_hub = fake_message(uid=uid, text="⬅️ К тренировкам")
    await training_settings.handle_back_to_hub(msg_hub, state)
    assert msg_hub.answer.called
    hub_markup = msg_hub.answer.call_args.kwargs.get("reply_markup")
    assert hub_markup is not None
    hub_button_texts = [btn.text for row in hub_markup.keyboard for btn in row]
    assert "▶️ Начать" in hub_button_texts
    assert "⚙️ Тренажёры" in hub_button_texts

    # 9. Test handle_settings in hub.py directly
    msg_hub_eq = fake_message(uid=uid, text="⚙️ Тренажёры")
    await hub.handle_settings(msg_hub_eq, state)
    assert msg_hub_eq.answer.called
    eq_markup = msg_hub_eq.answer.call_args_list[0].kwargs.get("reply_markup")
    assert eq_markup is not None
    assert "➕ Добавить тренажёр" in [b.text for r in eq_markup.keyboard for b in r]

    # 10. Test handle_start_workout in hub.py directly
    msg_start = fake_message(uid=uid, text="▶️ Начать")
    await hub.handle_start_workout(msg_start, state)
    assert msg_start.answer.called
    plan_card_call = msg_start.answer.call_args
    assert "План" in plan_card_call.args[0]
    data = await state.get_data()
    assert data.get("plan_id") is not None
