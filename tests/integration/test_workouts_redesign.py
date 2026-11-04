import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from src.database.models import Exercise, Workout, WorkoutEntry, WorkoutTemplate
from src.handlers import workouts as handler
from src.services import workout_input
from src.services import workout_templates as wt
from src.services import workouts as svc
from src.utils.workout_locales import tr
from tests.integration.test_security_and_meal_recovery import make_user
from tests.integration.test_workout_cards import callback, message

pytestmark = pytest.mark.asyncio


async def test_silent_text_collection_no_llm_calls_before_done(db_session):
    """Until Done is clicked: 0 LLM calls, bot stays silent during input, draft persists."""
    db = db_session
    user = await make_user(db, 101)
    msg, state = message(uid=user.telegram_id), SimpleNamespace(clear=AsyncMock())

    # User opens Add -> Workout
    await handler.add_workout_from_add_menu(msg, state, 'ru', user)
    w = await svc.current(db, user.telegram_id)
    assert w is not None
    assert w.pending.get('mode') == 'manual_entries'

    # User sends two messages with performed exercises
    msg1 = message(text='Ellipsentrainer 20 минут кросс тренинг', uid=user.telegram_id)
    msg1.message_id = 501
    await handler.manual_workout_input(msg1, 'ru')
    # Bot stays silent: no answer sent to msg1
    assert msg1.answer.call_count == 0

    msg2 = message(text='Brustpresse 35кг x 4 подхода по 15 раз\nLatzug 40кг 3 подхода по 15 раз', uid=user.telegram_id)
    msg2.message_id = 502
    await handler.manual_workout_input(msg2, 'ru')
    assert msg2.answer.call_count == 0

    # Verify state in DB
    await db.refresh(w)
    raw = w.feedback.get('raw_exercises', [])
    assert len(raw) == 3
    assert raw[0] == 'Ellipsentrainer 20 минут кросс тренинг'
    assert raw[1] == 'Brustpresse 35кг x 4 подхода по 15 раз'
    assert raw[2] == 'Latzug 40кг 3 подхода по 15 раз'
    assert w.status == 'active'

    # 0 entries saved to history before Done
    entries = await svc.entries(db, w)
    assert len(entries) == 0


async def test_user_example_chat_input_and_clarification_gives_8_exercises(db_session, monkeypatch):
    """The user example from Section 5.3:
    Cardio 20 min + 5 lifts + Abduktion/Adduktion clarification resolved -> exactly 8 exercises!
    Bizepsmaschine 15x3x15kg correctly resolves to 15kg, 3x15.
    """
    db = db_session
    user = await make_user(db, 102)
    msg, state = message(uid=user.telegram_id), SimpleNamespace(clear=AsyncMock())

    await handler.add_workout_from_add_menu(msg, state, 'ru', user)
    w = await svc.current(db, user.telegram_id)

    input_text = (
        "Ellipsentrainer 20 минут кросс тренинг\n"
        "Brustpresse 35кг x 4 подхода по 15 раз\n"
        "Latzug 40кг 3 подхода по 15 раз\n"
        "Rudermaschine 45 кг 3 подхода по 12 раз\n"
        "Beinstrecker 40 кг 3 подхода по 12 раз\n"
        "Abduktion и Adduktion 40 kg x 3 x 15\n"
        "Bizepsmaschine 15x3x15kg."
    )
    user_msg = message(text=input_text, uid=user.telegram_id)
    user_msg.message_id = 601
    await handler.manual_workout_input(user_msg, 'ru')

    # Mock the LLM parser response matching Section 5.3 rules
    mock_parsed = workout_input.ParsedWorkout(
        exercises=[
            workout_input.ParsedExercise(
                name="Ellipsentrainer", kind="cardio", source_lines=[1],
                sets=[workout_input.ParsedSet(minutes=20, intensity=None)]
            ),
            workout_input.ParsedExercise(
                name="Brustpresse", kind="strength", source_lines=[2],
                sets=[workout_input.ParsedSet(weight=35.0, reps=15) for _ in range(4)]
            ),
            workout_input.ParsedExercise(
                name="Latzug", kind="strength", source_lines=[3],
                sets=[workout_input.ParsedSet(weight=40.0, reps=15) for _ in range(3)]
            ),
            workout_input.ParsedExercise(
                name="Rudermaschine", kind="strength", source_lines=[4],
                sets=[workout_input.ParsedSet(weight=45.0, reps=12) for _ in range(3)]
            ),
            workout_input.ParsedExercise(
                name="Beinstrecker", kind="strength", source_lines=[5],
                sets=[workout_input.ParsedSet(weight=40.0, reps=12) for _ in range(3)]
            ),
            workout_input.ParsedExercise(
                name="Bizepsmaschine", kind="strength", source_lines=[7],
                sets=[workout_input.ParsedSet(weight=15.0, reps=15) for _ in range(3)]
            ),
        ],
        clarifications=[
            workout_input.ClarificationItem(
                id="split_abduktion_adduktion",
                question="Abduktion и Adduktion: по 3 подхода × 15 повторов с 40 кг на каждом тренажёре?",
                type="split_exercises",
                exercise_names=["Abduktion", "Adduktion"],
                sets=[workout_input.ParsedSet(weight=40.0, reps=15) for _ in range(3)],
                options=[
                    workout_input.ClarificationOption(key="yes", label="Да, на каждом"),
                    workout_input.ClarificationOption(key="edit", label="Уточнить запись"),
                ],
                context={"source_lines": [6]},
            )
        ],
        comments=[],
    )

    monkeypatch.setattr(workout_input, "parse_lines", AsyncMock(return_value=mock_parsed))

    # User clicks Done
    status_msg = message(uid=user.telegram_id)
    msg.answer.return_value = status_msg
    await handler.finish_chat_workout(msg, 'ru', user)

    # Clarification is displayed
    await db.refresh(w)
    assert w.pending.get('mode') == 'needs_clarification'
    assert status_msg.edit_text.call_count == 1
    clarify_text = status_msg.edit_text.call_args.args[0]
    assert "Abduktion и Adduktion" in clarify_text

    # User clicks "Да, на каждом"
    cb = callback(f"woc:clarify:{w.id}:split_abduktion_adduktion:yes", status_msg, user.telegram_id)
    await handler.handle_workout_clarification(cb, 'ru', user)

    # Now workout is completed!
    await db.refresh(w)
    assert w.status == 'finished'

    # Exactly 8 distinct exercises in history!
    saved_entries = await svc.entries(db, w)
    assert len(saved_entries) == 8

    ex_map = {}
    for entry in saved_entries:
        ex = await db.get(Exercise, entry.exercise_id)
        ex_map[ex.name] = entry

    # Verify each distinct exercise
    assert "Ellipsentrainer" in ex_map
    assert ex_map["Ellipsentrainer"].actual[0]['kind'] == 'cardio'
    assert ex_map["Ellipsentrainer"].actual[0]['minutes'] == 20
    assert ex_map["Ellipsentrainer"].actual[0]['intensity'] is None

    assert "Brustpresse" in ex_map
    assert len(ex_map["Brustpresse"].actual) == 4
    assert [s['weight'] for s in ex_map["Brustpresse"].actual] == [35, 35, 35, 35]
    assert [s['reps'] for s in ex_map["Brustpresse"].actual] == [15, 15, 15, 15]

    # Abduktion and Adduktion are 2 separate exercises
    assert "Abduktion" in ex_map
    assert "Adduktion" in ex_map
    assert len(ex_map["Abduktion"].actual) == 3
    assert len(ex_map["Adduktion"].actual) == 3
    assert [s['weight'] for s in ex_map["Abduktion"].actual] == [40, 40, 40]
    assert [s['weight'] for s in ex_map["Adduktion"].actual] == [40, 40, 40]

    # Bizepsmaschine: 15kg x 3 x 15
    assert "Bizepsmaschine" in ex_map
    assert len(ex_map["Bizepsmaschine"].actual) == 3
    assert [s['weight'] for s in ex_map["Bizepsmaschine"].actual] == [15, 15, 15]
    assert [s['reps'] for s in ex_map["Bizepsmaschine"].actual] == [15, 15, 15]

    # Auto-saved template exists in database
    user_templates = [t for t in await wt.templates(db, user.telegram_id) if t.name != wt.LAST_WORKOUT_TEMPLATE]
    assert len(user_templates) == 1
    assert "Тренировка" in user_templates[0].name


async def test_verbal_numbers_bodyweight_and_multiple_reps_notation(db_session, monkeypatch):
    """Section 5.4 examples:
    - Бег двадцать минут -> cardio 20 min
    - Подтягивание на турнике -> bodyweight with unknown reps
    - Жим 20 кг: 10, 10, 8 повторений -> 3 sets
    """
    db = db_session
    user = await make_user(db, 103)
    msg, state = message(uid=user.telegram_id), SimpleNamespace(clear=AsyncMock())

    await handler.add_workout_from_add_menu(msg, state, 'ru', user)
    w = await svc.current(db, user.telegram_id)

    raw_lines = [
        "Бег двадцать минут",
        "Подтягивание на турнике",
        "Жим 20 кг: 10, 10, 8 повторений",
    ]
    for i, line in enumerate(raw_lines):
        m = message(text=line, uid=user.telegram_id)
        m.message_id = 700 + i
        await handler.manual_workout_input(m, 'ru')

    mock_parsed = workout_input.ParsedWorkout(
        exercises=[
            workout_input.ParsedExercise(
                name="Бег", kind="cardio", source_lines=[1],
                sets=[workout_input.ParsedSet(minutes=20, intensity=None)]
            ),
            workout_input.ParsedExercise(
                name="Подтягивание на турнике", kind="bodyweight", source_lines=[2],
                sets_unknown=True,
                sets=[workout_input.ParsedSet(weight=0, reps=None, load_type="bodyweight")]
            ),
            workout_input.ParsedExercise(
                name="Жим", kind="strength", source_lines=[3],
                sets=[
                    workout_input.ParsedSet(weight=20.0, reps=10),
                    workout_input.ParsedSet(weight=20.0, reps=10),
                    workout_input.ParsedSet(weight=20.0, reps=8),
                ]
            ),
        ],
        clarifications=[],
        comments=[],
    )

    monkeypatch.setattr(workout_input, "parse_lines", AsyncMock(return_value=mock_parsed))

    status_msg = message(uid=user.telegram_id)
    msg.answer.return_value = status_msg
    await handler.finish_chat_workout(msg, 'ru', user)

    await db.refresh(w)
    assert w.status == 'finished'

    entries = await svc.entries(db, w)
    assert len(entries) == 3

    bw_entry = None
    bench_entry = None
    for e in entries:
        ex = await db.get(Exercise, e.exercise_id)
        if ex.kind == 'strength' and ex.load_type == 'bodyweight':
            bw_entry = e
        if ex.name == 'Жим':
            bench_entry = e

    assert bw_entry is not None
    assert bw_entry.actual == [{'weight': 0, 'load_type': 'bodyweight', 'reps': None, 'confirmed': False}]

    assert bench_entry is not None
    assert [s['reps'] for s in bench_entry.actual] == [10, 10, 8]


async def test_cancellation_discards_draft_and_prevents_late_save(db_session):
    """Section 6: Cancel discards draft; late save is rejected."""
    db = db_session
    user = await make_user(db, 104)
    msg, state = message(uid=user.telegram_id), SimpleNamespace(clear=AsyncMock())

    await handler.add_workout_from_add_menu(msg, state, 'ru', user)
    w = await svc.current(db, user.telegram_id)

    m = message(text="Жим лёжа 20 кг 10 раз", uid=user.telegram_id)
    m.message_id = 801
    await handler.manual_workout_input(m, 'ru')

    # User cancels
    await handler.cancel_chat_workout(msg, 'ru', user)
    await db.refresh(w)
    assert w.status == 'cancelled'

    # Attempting to finish a cancelled workout raises StaleWorkout
    mock_parsed = workout_input.ParsedWorkout(
        exercises=[
            workout_input.ParsedExercise(
                name="Жим лёжа", kind="strength", source_lines=[1],
                sets=[workout_input.ParsedSet(weight=20.0, reps=10)]
            )
        ],
        clarifications=[],
        comments=[],
    )
    with pytest.raises(svc.StaleWorkout):
        await svc.finish_parsed_workout(db, w, user, mock_parsed, 'ru')


async def test_deterministic_comparison_and_report_format(db_session, monkeypatch):
    """Section 7: Report format, deterministic comparisons, focus for next time, no full plan dumped."""
    db = db_session
    user = await make_user(db, 105)

    # Session 1: Brustpresse 30 kg x 4 x 15, Latzug 40 kg x 3 x 15
    parsed1 = workout_input.ParsedWorkout(
        exercises=[
            workout_input.ParsedExercise(
                name="Brustpresse", kind="strength", source_lines=[1],
                sets=[workout_input.ParsedSet(weight=30.0, reps=15) for _ in range(4)]
            ),
            workout_input.ParsedExercise(
                name="Latzug", kind="strength", source_lines=[2],
                sets=[workout_input.ParsedSet(weight=40.0, reps=15) for _ in range(3)]
            ),
        ],
        clarifications=[], comments=[],
    )
    w1 = await svc.start(db, user)
    await svc.finish_parsed_workout(db, w1, user, parsed1, 'ru')

    # Session 2: Brustpresse 35 kg x 4 x 15 (weight increased!), Latzug 40 kg x 3 x 15 (no change)
    parsed2 = workout_input.ParsedWorkout(
        exercises=[
            workout_input.ParsedExercise(
                name="Brustpresse", kind="strength", source_lines=[1],
                sets=[workout_input.ParsedSet(weight=35.0, reps=15) for _ in range(4)]
            ),
            workout_input.ParsedExercise(
                name="Latzug", kind="strength", source_lines=[2],
                sets=[workout_input.ParsedSet(weight=40.0, reps=15) for _ in range(3)]
            ),
            workout_input.ParsedExercise(
                name="Rudermaschine", kind="strength", source_lines=[3],
                sets=[workout_input.ParsedSet(weight=45.0, reps=12) for _ in range(3)]
            ),
        ],
        clarifications=[], comments=[],
    )
    w2 = await svc.start(db, user)
    await svc.finish_parsed_workout(db, w2, user, parsed2, 'ru')

    # Mock focus text
    monkeypatch.setattr(
        svc, "generate_workout_focus",
        AsyncMock(return_value="Сохраните текущую нагрузку и отметьте, насколько тяжело дались последние подходы.")
    )

    report = await svc.build_workout_report(db, w2, user, 'ru')

    # Check Section 7 requirements
    assert "✅ Тренировка сохранена" in report
    assert "Сегодня" in report
    assert "Brustpresse — 35 кг, 4×15" in report
    assert "Latzug — 40 кг, 3×15" in report
    assert "Rudermaschine — 45 кг, 3×12" in report

    assert "По сравнению с прошлым разом" in report
    assert "Brustpresse: 30 → 35 кг, подходы и повторы те же." in report
    assert "Latzug: без изменений." in report
    assert "Для остальных упражнений предыдущих сопоставимых записей нет." in report

    assert "В следующий раз" in report
    assert "Сохраните текущую нагрузку" in report
    # The full next workout plan should NOT be dumped here!
    assert "Предложение следующей тренировки" not in report


async def test_finish_chat_workout_edit_fallback_when_telegram_forbids_edit(db_session, monkeypatch):
    """If Telegram rejects edit_text (e.g. Bad Request: message can't be edited),
    finish_chat_workout gracefully falls back to message.answer without raising."""
    from aiogram.exceptions import TelegramBadRequest

    db = db_session
    user = await make_user(db, 106)
    msg, state = message(uid=user.telegram_id), SimpleNamespace(clear=AsyncMock())

    await handler.add_workout_from_add_menu(msg, state, 'ru', user)
    w = await svc.current(db, user.telegram_id)

    raw_msg = message(text="Жим лёжа 50 кг 3 подхода по 10 раз", uid=user.telegram_id)
    raw_msg.message_id = 901
    await handler.manual_workout_input(raw_msg, 'ru')

    mock_parsed = workout_input.ParsedWorkout(
        exercises=[
            workout_input.ParsedExercise(
                name="Жим лёжа", kind="strength", source_lines=[1],
                sets=[workout_input.ParsedSet(weight=50.0, reps=10) for _ in range(3)]
            )
        ],
        clarifications=[],
        comments=[],
    )
    monkeypatch.setattr(workout_input, "parse_lines", AsyncMock(return_value=mock_parsed))

    # Configure status_msg to raise TelegramBadRequest on edit_text
    status_msg = message(uid=user.telegram_id)
    status_msg.edit_text.side_effect = TelegramBadRequest(method="editMessageText", message="Bad Request: message can't be edited")
    msg.answer.return_value = status_msg

    # Should not raise exception
    await handler.finish_chat_workout(msg, 'ru', user)

    await db.refresh(w)
    assert w.status == 'finished'
    # msg.answer should have been called as fallback for report and menu
    assert msg.answer.call_count >= 2


async def test_parse_lines_rule_based_fallback_on_429_rate_limit(monkeypatch):
    """When LLM provider fails with 429 RateLimit, rule-based fallback correctly parses lines."""
    import httpx
    from src.services import gemini

    lines = [
        "Ellipsentrainer 20 минут кросс тренинг",
        "Brustpresse 35кг x 4 подхода по 15 раз",
        "Latzug 40кг 3 подхода по 15 раз",
        "Rudermaschine 45 кг 3 подхода по 12 раз",
        "Beinstrecker 40 кг 3 подхода по 12 раз",
        "Abduktion и  Adduktion 40 kg x 3 x 15",
        "Bizepsmaschine 15x3x15kg.",
    ]

    # Mock gemini to raise 429 Too Many Requests (like OpenRouter daily limit)
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(429, request=req, text='{"error":{"message":"Rate limit exceeded: free-models-per-day"}}')
    monkeypatch.setattr(gemini, "call_gemini_with_retry", AsyncMock(side_effect=httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp)))

    parsed = await workout_input.parse_lines(lines, 'ru', 12345, [])
    assert len(parsed.exercises) == 6
    assert len(parsed.clarifications) == 1
    assert parsed.clarifications[0].id == "split_abduktion_adduktion"

    # Verify that applying clarification yields 8 exercises
    resolved = workout_input.apply_clarification(parsed, "split_abduktion_adduktion", "yes")
    assert len(resolved.exercises) == 8
    names = [e.name for e in resolved.exercises]
    assert "Abduktion" in names and "Adduktion" in names


async def test_template_checklist_preview_toggle_and_save(db_session):
    """When a user selects a template via 'Выбрать шаблон':
    1. Shows template choices with wot:preview:{id}
    2. Preview opens interactive checklist (all exercises checked by default)
    3. User can toggle exercises (e.g. uncheck exercise 1)
    4. User taps Save Completed: saves only checked exercises, finishes workout, and shows report.
    5. Cancel button deletes checklist prompt.
    """
    db = db_session
    user = await make_user(db, 107)
    msg, state = message(uid=user.telegram_id), SimpleNamespace(clear=AsyncMock())

    # Create 2 exercises and a template with 2 items
    ex1 = await svc.add_exercise(db, user.telegram_id, "Brustpresse", "strength")
    ex2 = await svc.add_exercise(db, user.telegram_id, "Latzug", "strength")
    items = [
        {"exercise_id": ex1.id, "kind": "strength", "sets": [{"weight": 35.0, "reps": 15} for _ in range(3)]},
        {"exercise_id": ex2.id, "kind": "strength", "sets": [{"weight": 40.0, "reps": 12} for _ in range(3)]},
    ]
    template = WorkoutTemplate(
        user_id=user.telegram_id,
        name="Моя верхняя тренировка",
        items=items,
        revision=1,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)

    # User clicks [ 📋 Выбрать шаблон ]
    await handler.choose_template_for_chat(msg, 'ru', user)
    assert msg.answer.call_count == 1
    # Check that template is listed
    preview_markup = msg.answer.call_args.kwargs['reply_markup']
    preview_btn = next(b for row in preview_markup.inline_keyboard for b in row if b.callback_data == f'wot:preview:{template.id}')
    assert preview_btn is not None

    # User taps the template button
    chat_msg = message(uid=user.telegram_id)
    chat_msg.edit_reply_markup = AsyncMock()
    cb_preview = callback(f'wot:preview:{template.id}', chat_msg, uid=user.telegram_id)
    await handler.template_action(cb_preview, state, 'ru', user)

    # Checklist is rendered: 2 exercise checkboxes (both checked ☑️), Save completed, Cancel
    checklist_markup = chat_msg.edit_text.call_args.kwargs['reply_markup']
    chk_buttons = [b for row in checklist_markup.inline_keyboard for b in row if b.callback_data.startswith('wot:chk:')]
    assert len(chk_buttons) == 2
    assert "☑️" in chk_buttons[0].text
    assert "☑️" in chk_buttons[1].text
    assert any("Сохранить выполненное" in b.text for row in checklist_markup.inline_keyboard for b in row)

    # User unchecks item 1 (Latzug)
    cb_toggle = callback(f'wot:chk:{template.id}:1', chat_msg, uid=user.telegram_id)
    await handler.template_action(cb_toggle, state, 'ru', user)
    # edit_reply_markup called with toggled state
    toggled_markup = chat_msg.edit_reply_markup.call_args.kwargs['reply_markup']
    toggled_buttons = [b for row in toggled_markup.inline_keyboard for b in row if b.callback_data.startswith('wot:chk:')]
    assert "☑️" in toggled_buttons[0].text
    assert "⬜" in toggled_buttons[1].text

    # User taps "Сохранить выполненное"
    cb_save = callback(f'wot:save_chk:{template.id}', chat_msg, uid=user.telegram_id)
    await handler.template_action(cb_save, state, 'ru', user)

    # Check workout was finished
    w = await svc.current(db, user.telegram_id)
    assert w is None  # no active workout
    last = await db.scalar(select(Workout).where(Workout.user_id == user.telegram_id, Workout.status == 'finished').order_by(Workout.id.desc()))
    assert last is not None
    assert last.status == 'finished'

    # Check that only item 0 (Brustpresse) was saved
    entries = await svc.entries(db, last)
    assert len(entries) == 1
    assert entries[0].exercise_id == ex1.id
    assert len(entries[0].actual) == 3

    # Check report was shown
    report = chat_msg.edit_text.call_args.args[0]
    assert "Brustpresse" in report
    assert "Latzug" not in report

    # Test cancel deletes message
    delete_msg = message(uid=user.telegram_id)
    delete_msg.delete = AsyncMock()
    cb_cancel = callback('wot:cancel_chk', delete_msg, uid=user.telegram_id)
    await handler.template_action(cb_cancel, state, 'ru', user)
    assert delete_msg.delete.called

