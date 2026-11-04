"""Comprehensive unit and integration tests for Training Menu & AI Workout Coach.

Tests:
1. RichMessageBuilder limits, structure validation, and HTML fallback formatting.
2. send_rich_or_fallback and edit_rich_or_fallback fallback behavior on API rejection.
3. Multilingual alias matching across sessions (e.g. 'Brust press 100kg x 10' -> 'Brustpresse 102.5kg x 10').
4. First-time exercise baseline rule (is_first_time=True, no historical comparison, next load based on feeling).
5. Existing history progressive overload rule (is_first_time=False, compares with last session).
6. Training Menu Hub, Draft Queue & Feeling Matrix FSM (undo, cycling feeling, quick-set feeling, commit).
7. Prepare / Coach mode (exercise cues, warm-up targets, 1-tap add recommended set to draft).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from src.database.models import (
    CanonicalExercise,
    ExerciseAlias,
    User,
    WorkoutSession,
    WorkoutSessionEntry,
)
from src.handlers import training as handler
from src.services import training_coach as coach
from src.utils.rich_message import (
    MAX_BLOCKS,
    MAX_CHARACTERS,
    MAX_NESTING_DEPTH,
    MAX_TABLE_COLUMNS,
    RichMessageBuilder,
    edit_rich_or_fallback,
    send_rich_or_fallback,
)
from tests.integration.test_security_and_meal_recovery import make_user

pytestmark = pytest.mark.asyncio


def fake_message(uid=123, text=""):
    return SimpleNamespace(
        chat=SimpleNamespace(id=uid),
        from_user=SimpleNamespace(id=uid, language_code="ru"),
        text=text,
        answer=AsyncMock(),
        edit_text=AsyncMock(),
        edit_reply_markup=AsyncMock(),
        delete=AsyncMock(),
        bot=SimpleNamespace(
            send_message=AsyncMock(),
            edit_message_text=AsyncMock(),
        ),
        message_id=999,
    )


def fake_callback(data, msg, uid=123):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=uid, language_code="ru"),
        message=msg,
        answer=AsyncMock(),
        bot=msg.bot,
    )


# --- 1. RichMessageBuilder & Fallback Tests ---

async def test_rich_message_builder_valid_payload():
    builder = RichMessageBuilder()
    builder.add_section_heading("🏋️ Workout Logged")
    builder.add_paragraph("Great session today!")
    builder.add_table(
        cells=[
            [
                {"text": "Exercise", "is_header": True, "align": "left"},
                {"text": "Weight / Reps", "is_header": True, "align": "right"},
                {"text": "Feel", "is_header": True, "align": "center"},
                {"text": "Next Target", "is_header": True, "align": "right"},
            ],
            [
                {"text": "Brust press", "is_header": False, "align": "left"},
                {"text": "100kg × 10", "is_header": False, "align": "right"},
                {"text": "🟡 OK", "is_header": False, "align": "center"},
                {"text": "↗️ 102.5kg × 10", "is_header": False, "align": "right"},
            ],
        ],
        is_bordered=True,
        is_striped=True,
        is_compact=True,
    )
    builder.add_details(
        title="📈 Progression & Next Training Plan",
        blocks=[
            {"type": "paragraph", "text": {"type": "bold", "text": "Brust press: "}},
            {"type": "paragraph", "text": {"type": "plain", "text": "Add 2.5kg next time. 💡 Tip: Keep elbows at 45°."}},
        ],
        is_open=True,
    )

    payload = builder.build_payload(chat_id=123456789)
    assert payload["chat_id"] == 123456789
    blocks = payload["rich_message"]["blocks"]
    assert len(blocks) == 4
    assert blocks[0]["type"] == "section_heading"
    assert blocks[2]["type"] == "table"
    assert blocks[2]["is_compact"] is True
    assert blocks[3]["type"] == "details"
    assert blocks[3]["is_open"] is True

    # Test HTML fallback output (mobile cards for wide 4-column table)
    html_out = builder.to_html()
    assert "<b>🏋️ Workout Logged</b>" in html_out
    assert "Great session today!" in html_out
    assert "Brust press" in html_out
    assert "100kg × 10" in html_out
    assert "Next Target" in html_out
    assert "<blockquote expandable>" in html_out

    # Small 2-column table fits within mobile pre block
    small_builder = RichMessageBuilder()
    small_builder.add_table([
        [{"text": "A", "is_header": True}, {"text": "B", "is_header": True}],
        [{"text": "1", "is_header": False}, {"text": "2", "is_header": False}],
    ])
    assert "<pre>" in small_builder.to_html()


async def test_rich_message_builder_enforces_limits():
    # 1. Column limit (20)
    builder = RichMessageBuilder()
    row_too_wide = [{"text": f"Col {i}", "is_header": True, "align": "left"} for i in range(MAX_TABLE_COLUMNS + 1)]
    with pytest.raises(ValueError, match="exceeds max table columns"):
        builder.add_table([row_too_wide])

    # 2. Max characters (32,768)
    builder2 = RichMessageBuilder()
    builder2.add_paragraph("A" * (MAX_CHARACTERS + 10))
    with pytest.raises(ValueError, match="Max characters exceeded"):
        builder2.build_payload(chat_id=123)

    # 3. Max blocks (500)
    builder3 = RichMessageBuilder()
    for _ in range(MAX_BLOCKS + 5):
        builder3.add_paragraph("short")
    with pytest.raises(ValueError, match="Max blocks exceeded"):
        builder3.build_payload(chat_id=123)

    # 4. Nesting depth (16)
    builder4 = RichMessageBuilder()
    curr = {"type": "paragraph", "text": {"type": "plain", "text": "leaf"}}
    for _ in range(MAX_NESTING_DEPTH + 2):
        curr = {"type": "details", "title": {"type": "plain", "text": "d"}, "blocks": [curr]}
    builder4.add_block(curr)
    with pytest.raises(ValueError, match="Max nesting depth exceeded"):
        builder4.build_payload(chat_id=123)


async def test_send_and_edit_rich_or_fallback():
    builder = RichMessageBuilder()
    builder.add_section_heading("Test Heading")
    builder.add_paragraph("Test Paragraph")

    bot = AsyncMock()
    # Mock bot call to raise error on custom SendRichMessage method (as would happen on live Bot API without rich message support)
    bot.side_effect = Exception("METHOD_NOT_FOUND")
    bot.send_message = AsyncMock()
    bot.edit_message_text = AsyncMock()

    # send_rich_or_fallback should fall back to send_message with HTML
    await send_rich_or_fallback(bot, chat_id=987, builder=builder)
    assert bot.send_message.called
    assert bot.send_message.call_args.kwargs["chat_id"] == 987
    assert bot.send_message.call_args.kwargs["parse_mode"] == "HTML"
    assert "<b>Test Heading</b>" in bot.send_message.call_args.kwargs["text"]

    # edit_rich_or_fallback should fall back to edit_message_text with HTML
    await edit_rich_or_fallback(bot, chat_id=987, message_id=456, builder=builder)
    assert bot.edit_message_text.called
    assert bot.edit_message_text.call_args.kwargs["message_id"] == 456
    assert "<b>Test Heading</b>" in bot.edit_message_text.call_args.kwargs["text"]


# --- 2. Multilingual Alias Matching & Persistence Tests ---

async def test_multilingual_alias_matching_and_first_time_baseline(db_session):
    """Session 1: Logs 'Brust press 100kg x 10' in German/English.
    Verifies:
    - is_first_time is True
    - No fake previous comparison in advice
    - Canonical exercise & aliases created
    Session 2: Logs 'Brustpresse 102.5kg x 10'.
    Verifies:
    - Matches the SAME canonical exercise!
    - is_first_time is False
    - Accurately compares with Session 1 (100kg -> 102.5kg).
    """
    db = db_session
    user = await make_user(db, 201)

    # --- Session 1 ---
    draft1 = [{"id": 1, "raw_text": "Brust press 100kg x 10", "feeling": "easy"}]
    parsed1 = await coach.parse_and_analyze_workout(user.telegram_id, draft1, "en", db)

    assert len(parsed1.parsed_exercises) == 1
    ex1 = parsed1.parsed_exercises[0]
    assert ex1.is_first_time is True
    # First time rule: no fake history comparison
    assert "100" in ex1.FormattedLoad
    # Since feeling was 'easy', next target suggests weight increase
    assert "↗️" in ex1.next_recommended_load

    # Save to DB
    session1, entries1 = await coach.save_workout_session(db, user.telegram_id, parsed1, draft1)
    assert session1.id is not None
    assert len(entries1) == 1

    # Verify canonical exercise created
    ce1 = await db.get(CanonicalExercise, entries1[0].canonical_exercise_id)
    assert ce1 is not None
    assert ce1.user_id == user.telegram_id

    # Verify aliases recorded
    aliases = list((await db.scalars(select(ExerciseAlias).where(ExerciseAlias.canonical_exercise_id == ce1.id))).all())
    alias_texts = [a.alias_normalized for a in aliases]
    assert any("brust press" in a for a in alias_texts)

    # --- Session 2 (Different wording/language: 'Brustpresse') ---
    draft2 = [{"id": 1, "raw_text": "Brustpresse 102.5kg x 10", "feeling": "normal"}]
    parsed2 = await coach.parse_and_analyze_workout(user.telegram_id, draft2, "de", db)

    assert len(parsed2.parsed_exercises) == 1
    ex2 = parsed2.parsed_exercises[0]
    # Existing history rule: is_first_time is FALSE!
    assert ex2.is_first_time is False
    # Compares to previous session
    assert "100" in ex2.comparison_and_next_advice

    # Save Session 2
    session2, entries2 = await coach.save_workout_session(db, user.telegram_id, parsed2, draft2)
    assert entries2[0].canonical_exercise_id == ce1.id  # Same canonical exercise!


async def test_feeling_adjusts_progression_target(db_session):
    """Verifies that 'easy', 'normal', and 'hard' produce different progressive overload targets."""
    db = db_session
    user = await make_user(db, 202)

    # Easy
    d_easy = [{"id": 1, "raw_text": "Latzug 40kg 3x10", "feeling": "easy"}]
    p_easy = await coach.parse_and_analyze_workout(user.telegram_id, d_easy, "ru", db)
    assert "↗️" in p_easy.parsed_exercises[0].next_recommended_load

    # Hard
    d_hard = [{"id": 1, "raw_text": "Latzug 40kg 3x10", "feeling": "hard"}]
    p_hard = await coach.parse_and_analyze_workout(user.telegram_id, d_hard, "ru", db)
    # Hard suggests consolidating / keeping same weight
    assert "➡️" in p_hard.parsed_exercises[0].next_recommended_load
    assert "закрепите" in p_hard.parsed_exercises[0].comparison_and_next_advice.lower()


# --- 3. Training Hub, Draft Queue & Feeling Matrix FSM Tests ---

@pytest.mark.skip(reason="Legacy prototype handler replaced by Training v2 in src/handlers/training/")
async def test_training_hub_and_draft_queue_lifecycle(db_session):
    """Tests:
    1. /training opens hub with sessions stats
    2. Start logging enters DRAFTING_WORKOUT
    3. User sends 2 exercises, undoes 1, adds 1
    4. Press Ready transitions to RATING_FEELING
    5. User toggles feeling and quick-sets all to hard
    6. Analyze & Save commits session and renders Rich Message.
    """
    db = db_session
    user = await make_user(db, 203)
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id)
    state = FSMContext(storage=storage, key=key)

    # 1. /training command
    msg_hub = fake_message(uid=user.telegram_id, text="/training")
    await handler.training_hub(msg_hub, state, "ru")
    assert msg_hub.answer.called
    curr_state = await state.get_state()
    assert curr_state == handler.TrainingStates.TRAINING_MENU.state

    # 2. Tap [Start Logging]
    cb_start = fake_callback("tr:start_log", msg_hub, uid=user.telegram_id)
    await handler.start_drafting(cb_start, state, "ru")
    assert await state.get_state() == handler.TrainingStates.DRAFTING_WORKOUT.state

    # 3. User sends exercises
    msg_ex1 = fake_message(uid=user.telegram_id, text="Brust press 100kg x 10")
    await handler.draft_user_message(msg_ex1, state, "ru")
    assert msg_ex1.delete.called

    msg_ex2 = fake_message(uid=user.telegram_id, text="Beinpresse 150kg 3x12")
    await handler.draft_user_message(msg_ex2, state, "ru")
    assert msg_ex2.delete.called

    data = await state.get_data()
    assert len(data["draft_items"]) == 2

    # Tap Undo
    cb_undo = fake_callback("tr:undo", msg_hub, uid=user.telegram_id)
    await handler.undo_draft_item(cb_undo, state, "ru")
    data = await state.get_data()
    assert len(data["draft_items"]) == 1
    assert data["draft_items"][0]["raw_text"] == "Brust press 100kg x 10"

    # Add Rudern
    msg_ex3 = fake_message(uid=user.telegram_id, text="Rudern 50kg 3x10")
    await handler.draft_user_message(msg_ex3, state, "ru")
    data = await state.get_data()
    assert len(data["draft_items"]) == 2

    # 4. Press Ready -> RATING_FEELING
    cb_ready = fake_callback("tr:ready", msg_hub, uid=user.telegram_id)
    await handler.draft_ready_feeling_prompt(cb_ready, state, "ru")
    assert await state.get_state() == handler.TrainingStates.RATING_FEELING.state

    # 5. Toggle feeling of item 0 (normal -> hard)
    cb_feel0 = fake_callback("tr:feel:0", msg_hub, uid=user.telegram_id)
    await handler.toggle_item_feeling(cb_feel0, state, "ru")
    data = await state.get_data()
    assert data["draft_items"][0]["feeling"] == "hard"

    # Quick set all to 'easy'
    cb_all_easy = fake_callback("tr:all_feel:easy", msg_hub, uid=user.telegram_id)
    await handler.set_all_feelings(cb_all_easy, state, "ru")
    data = await state.get_data()
    assert data["draft_items"][0]["feeling"] == "easy"
    assert data["draft_items"][1]["feeling"] == "easy"

    # 6. Tap [Analyze & Save Workout]
    cb_analyze = fake_callback("tr:analyze", msg_hub, uid=user.telegram_id)
    await handler.analyze_and_commit_workout(cb_analyze, state, "ru")

    # State cleared
    assert await state.get_state() is None

    # Check DB session created
    sessions = list((await db.scalars(select(WorkoutSession).where(WorkoutSession.user_id == user.telegram_id))).all())
    assert len(sessions) == 1
    entries = list((await db.scalars(select(WorkoutSessionEntry).where(WorkoutSessionEntry.session_id == sessions[0].id))).all())
    assert len(entries) == 2
    assert entries[0].feeling == "easy"


# --- 4. Prepare / Coach Mode Tests ---

@pytest.mark.skip(reason="Legacy prototype handler replaced by Training v2 in src/handlers/training/")
async def test_prepare_coach_mode_and_add_to_draft(db_session):
    """Tests Prepare Mode:
    1. Triggers PREPARE_SELECT
    2. User inputs custom goal or machine
    3. Coach produces warmup, cues, and complementary suggestion
    4. Tapping [Add to Today's Draft] pre-populates draft.
    """
    db = db_session
    user = await make_user(db, 204)
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id)
    state = FSMContext(storage=storage, key=key)

    # Open Prepare
    msg = fake_message(uid=user.telegram_id)
    cb_prep = fake_callback("tr:prepare", msg, uid=user.telegram_id)
    await handler.prepare_select(cb_prep, state, "ru")
    assert await state.get_state() == handler.TrainingStates.PREPARE_SELECT.state

    # User asks for advice on Brustpresse
    msg_query = fake_message(uid=user.telegram_id, text="Brustpresse")
    await handler.prepare_custom_text(msg_query, state, "ru")
    assert await state.get_state() == handler.TrainingStates.PREPARE_ADVICE.state

    # Tap [Add to Today's Draft]
    import base64
    quick_log = "Brustpresse 60kg x 10"
    b64_log = base64.urlsafe_b64encode(quick_log.encode("utf-8")).decode("ascii").rstrip("=")
    cb_add = fake_callback(f"tr:add_draft:{b64_log}", msg, uid=user.telegram_id)
    await handler.add_recommended_to_draft(cb_add, state, "ru")

    # Verify transitions to DRAFTING_WORKOUT and draft contains the recommended set
    assert await state.get_state() == handler.TrainingStates.DRAFTING_WORKOUT.state
    data = await state.get_data()
    assert len(data["draft_items"]) == 1
    assert data["draft_items"][0]["raw_text"] == quick_log


@pytest.mark.skip(reason="Legacy prototype handler replaced by Training v2 in src/handlers/training/")
async def test_multi_machine_selection_and_plan_generation(db_session):
    """Tests multi-machine selection with checkboxes and batch plan generation."""
    db = db_session
    user = await make_user(db, 205)

    # Prepopulate 2 canonical exercises for this user
    ce1 = CanonicalExercise(
        user_id=user.telegram_id,
        canonical_slug="brustpresse",
        display_name="Brustpresse",
        exercise_type="strength",
    )
    ce2 = CanonicalExercise(
        user_id=user.telegram_id,
        canonical_slug="latzug",
        display_name="Latzug",
        exercise_type="strength",
    )
    db.add_all([ce1, ce2])
    await db.commit()
    await db.refresh(ce1)
    await db.refresh(ce2)

    # Add historical entries
    s = WorkoutSession(user_id=user.telegram_id)
    db.add(s)
    await db.commit()
    await db.refresh(s)

    e1 = WorkoutSessionEntry(
        session_id=s.id,
        canonical_exercise_id=ce1.id,
        raw_input="Brustpresse 35kg 4x15",
        weight_kg=35.0,
        sets=4,
        reps=15,
        feeling="normal",
    )
    e2 = WorkoutSessionEntry(
        session_id=s.id,
        canonical_exercise_id=ce2.id,
        raw_input="Latzug 40kg 3x15",
        weight_kg=40.0,
        sets=3,
        reps=15,
        feeling="easy",
    )
    db.add_all([e1, e2])
    await db.commit()

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id)
    state = FSMContext(storage=storage, key=key)

    # 1. Open Prepare mode
    msg = fake_message(uid=user.telegram_id)
    cb_prep = fake_callback("tr:prepare", msg, uid=user.telegram_id)
    await handler.prepare_select(cb_prep, state, "ru")

    # 2. Toggle checkbox for machine 1
    cb_toggle1 = fake_callback(f"tr:toggle_ex:{ce1.id}", msg, uid=user.telegram_id)
    await handler.toggle_exercise_selection(cb_toggle1, state, "ru")
    data = await state.get_data()
    assert ce1.id in data["selected_exercises"]

    # 3. Toggle checkbox for machine 2
    cb_toggle2 = fake_callback(f"tr:toggle_ex:{ce2.id}", msg, uid=user.telegram_id)
    await handler.toggle_exercise_selection(cb_toggle2, state, "ru")
    data = await state.get_data()
    assert ce1.id in data["selected_exercises"]
    assert ce2.id in data["selected_exercises"]

    # 4. Run selected plan
    cb_run = fake_callback("tr:run_selected_plan", msg, uid=user.telegram_id)
    await handler.prepare_run_selected_plan(cb_run, state, "ru")
    assert await state.get_state() == handler.TrainingStates.PREPARE_ADVICE.state

    data = await state.get_data()
    planned = data.get("planned_exercises", [])
    assert len(planned) == 2
    names = [p["name"] for p in planned]
    assert "Brustpresse" in names
    assert "Latzug" in names

    # 5. Add whole plan to draft
    cb_add_all = fake_callback("tr:plan_add_all", msg, uid=user.telegram_id)
    await handler.plan_add_all_to_draft(cb_add_all, state, "ru")
    assert await state.get_state() == handler.TrainingStates.DRAFTING_WORKOUT.state
    data = await state.get_data()
    assert len(data["draft_items"]) == 2


@pytest.mark.skip(reason="Legacy prototype handler replaced by Training v2 in src/handlers/training/")
async def test_muscle_group_goal_and_custom_text_focus(db_session):
    """Tests generating a target workout plan for muscle groups (buttons and free-form text)."""
    db = db_session
    user = await make_user(db, 206)

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id)
    state = FSMContext(storage=storage, key=key)

    # 1. Quick button: Back focus
    msg = fake_message(uid=user.telegram_id)
    cb_goal = fake_callback("tr:goal:back", msg, uid=user.telegram_id)
    await handler.prepare_muscle_goal_selected(cb_goal, state, "ru")
    assert await state.get_state() == handler.TrainingStates.PREPARE_ADVICE.state
    data = await state.get_data()
    planned = data.get("planned_exercises", [])
    assert len(planned) >= 2
    # Verify back-related exercises
    combined_names = " ".join(p["name"].lower() for p in planned)
    assert any(w in combined_names for w in ["lat", "ruder", "тяг", "спин"])

    # 2. Free-form text focus: "хочу упор на спине"
    msg_custom = fake_message(uid=user.telegram_id, text="хочу упор на спине")
    await handler.prepare_custom_text(msg_custom, state, "ru")
    assert await state.get_state() == handler.TrainingStates.PREPARE_ADVICE.state
    data = await state.get_data()
    planned2 = data.get("planned_exercises", [])
    assert len(planned2) >= 2

    # Add all to draft
    cb_add_all = fake_callback("tr:plan_add_all", msg, uid=user.telegram_id)
    await handler.plan_add_all_to_draft(cb_add_all, state, "ru")
    assert await state.get_state() == handler.TrainingStates.DRAFTING_WORKOUT.state
    data = await state.get_data()
    assert len(data["draft_items"]) == len(planned2)


@pytest.mark.skip(reason="Legacy prototype handler replaced by Training v2 in src/handlers/training/")
async def test_overhauled_history_and_prs_formatting(db_session):
    """Tests clean formatting in training_history: PRs, no .00, no duplicate names on cardio, no trailing dots."""
    db = db_session
    user = await make_user(db, 207)

    # Add canonical exercises
    ce_str = CanonicalExercise(
        user_id=user.telegram_id,
        canonical_slug="brustpresse",
        display_name="Brustpresse",
        exercise_type="strength",
    )
    ce_cardio = CanonicalExercise(
        user_id=user.telegram_id,
        canonical_slug="ellipsentrainer",
        display_name="Ellipsentrainer",
        exercise_type="cardio",
    )
    ce_dot = CanonicalExercise(
        user_id=user.telegram_id,
        canonical_slug="bizepsmaschine",
        display_name="Bizepsmaschine .:",
        exercise_type="strength",
    )
    db.add_all([ce_str, ce_cardio, ce_dot])
    await db.commit()
    await db.refresh(ce_str)
    await db.refresh(ce_cardio)
    await db.refresh(ce_dot)

    # Add workout session
    from datetime import datetime, UTC
    s = WorkoutSession(
        user_id=user.telegram_id,
        performed_at=datetime(2026, 11, 2, 21, 44, tzinfo=UTC),
    )
    db.add(s)
    await db.commit()
    await db.refresh(s)

    # Add entries
    e1 = WorkoutSessionEntry(
        session_id=s.id,
        canonical_exercise_id=ce_str.id,
        raw_input="Brustpresse 35.00kg x 4",
        weight_kg=35.0,
        sets=4,
        reps=4,
        feeling="hard",
        next_recommended_load="➡️ 35.00kg × 4",
    )
    e2 = WorkoutSessionEntry(
        session_id=s.id,
        canonical_exercise_id=ce_cardio.id,
        raw_input="Ellipsentrainer 20 минут кросс тренинг",
        duration_sec=1200,
        feeling="hard",
        next_recommended_load="➡️ Сохранить темп",
    )
    e3 = WorkoutSessionEntry(
        session_id=s.id,
        canonical_exercise_id=ce_dot.id,
        raw_input="Bizepsmaschine 15.00kg x 3",
        weight_kg=15.0,
        sets=3,
        reps=3,
        feeling="hard",
        next_recommended_load="➡️ 15.00kg × 3",
    )
    db.add_all([e1, e2, e3])
    await db.commit()

    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=user.telegram_id, user_id=user.telegram_id)
    state = FSMContext(storage=storage, key=key)

    msg = fake_message(uid=user.telegram_id)
    cb_hist = fake_callback("tr:history", msg, uid=user.telegram_id)
    await handler.training_history(cb_hist, state, "ru")

    # Inspect the rendered message or edit_text call
    # In fallback or rich message, verify the content sent
    calls = msg.bot.edit_message_text.call_args_list or msg.edit_text.call_args_list
    assert len(calls) > 0
    rendered_text = str(calls[0])

    # 1. No .00 in weights
    assert "35.00" not in rendered_text
    assert "15.00" not in rendered_text
    assert "35 кг" in rendered_text or "35 kg" in rendered_text
    assert "15 кг" in rendered_text or "15 kg" in rendered_text

    # 2. PR section present
    assert "Личные рекорды" in rendered_text or "Personal Records" in rendered_text

    # 3. Trailing dot / punctuation removed from Bizepsmaschine
    assert "Bizepsmaschine .:" not in rendered_text
    assert "Bizepsmaschine" in rendered_text

    # 4. Cardio is clean: "20 мин" instead of duplicated sentence
    assert "20 мин" in rendered_text
    assert "Ellipsentrainer: Ellipsentrainer" not in rendered_text

