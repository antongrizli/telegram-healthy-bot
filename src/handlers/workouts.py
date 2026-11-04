"""Private-chat workout cards. All mutable state is in SQL, never only in FSM."""
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo
from sqlalchemy import delete, select, update

from src.database.connection import AsyncSessionLocal
from src.database.models import Exercise, User, Workout, WorkoutEntry, WorkoutPlan, WorkoutTemplate
from src.keyboards import reply
from src.config import settings
from src.services import workout_templates as wt
from src.services import workouts as svc
from src.utils.i18n_locales import get_all_translations, get_text
from src.utils.telegram_edit import try_edit
from src.utils.workout_locales import tr

router = Router()
logger = logging.getLogger(__name__)
GOAL_KEYS = dict(zip(svc.GOALS, ('goal_lose', 'goal_maintain', 'goal_gain_w', 'goal_gain_m'), strict=True))


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in rows])


def button(workout, action, label, target=0, value=0):
    data = f'wo:{workout.id}:{workout.revision}:{action}:{target}:{value}'
    assert len(data.encode()) <= 64
    return label, data


def template_name(template, lang):
    return tr('auto_template', lang) if template.name == wt.LAST_WORKOUT_TEMPLATE else template.name


async def emit(message, text, rows, edit=False):
    while len(text) > 3900:
        await message.answer(text[:3900], parse_mode=None)
        text = text[3900:]
        edit = False
    kwargs = {'text': text, 'reply_markup': keyboard(rows), 'parse_mode': None}
    if edit and await try_edit(message.edit_text, **kwargs):
        return
    await message.answer(**kwargs)


def format_set(row, lang):
    if row.get('kind') == 'cardio':
        minutes = row.get('minutes')
        if minutes is not None:
            value = f"{minutes:g}"
        elif 'minutes_min' in row:
            value = f"{row['minutes_min']}–{row['minutes_max']}?"
        else:
            value = '?'
        return f"{value} {tr('minute_unit', lang)} · {tr(row.get('intensity') or 'unknown_intensity', lang)}"
    if row.get('load_type') == 'bodyweight':
        reps = '?' if row.get('reps') is None else str(row['reps']) + ('' if row.get('confirmed') else '?')
        return tr('bodyweight', lang) + ' × ' + reps
    value = row.get('weight')
    weight = f"{value:g}" if value is not None else (f"{row['weight_min']}–{row['weight_max']}?" if 'weight_min' in row else '?')
    reps = '?' if row.get('reps') is None else str(row['reps']) + ('' if row.get('confirmed') else '?')
    return f"{weight} kg × {reps}"


async def cardio_card(message, workout, entry, exercise, lang, edit):
    row = entry.draft[0]
    text = (tr('editing', lang) + '\n' if workout.feedback.get('template_edit') else '') + exercise.name + '\n' + format_set(row, lang)
    rows = [[button(workout, 'minutedelta', f'{n:+d}', entry.id, n) for n in (-5, -1, 1, 5)],
            [button(workout, 'inputminutes', tr('minutes', lang), entry.id)],
            [button(workout, 'intensity', ('✓ ' if row['intensity'] == k else '') + tr(k, lang), entry.id, k)
             for k in wt.INTENSITIES],
            [button(workout, 'save', tr('save', lang), entry.id), button(workout, 'cancel', tr('cancel', lang), entry.id)],
            [button(workout, 'page', tr('back', lang))]]
    if row.get('minutes') is None:
        text += '\n' + tr('range', lang)
        if 'minutes_min' in row:
            rows.insert(0, [button(workout, 'minutes', str(row[k]), entry.id, row[k]) for k in ('minutes_min', 'minutes_max')])
    await emit(message, text, rows, edit)


async def library(db, message, workout, lang, page=0, edit=False):
    exercises = list((await db.scalars(select(Exercise).where(Exercise.user_id == workout.user_id)
                                      .order_by(Exercise.id))).all())
    page = max(0, min(page, max(0, (len(exercises) - 1) // 8)))
    source = workout.template_snapshot or {}
    order = {i['exercise_id']: n for n, i in enumerate(source.get('items', []))}
    exercises.sort(key=lambda e: (order.get(e.id, 1000), e.id))
    saved = {e.exercise_id for e in await svc.entries(db, workout) if e.actual}
    rows = [[button(workout, 'open', ('✓ ' if e.id in saved else '○ ' if e.id in order else '') + e.name[:50], e.id)]
            for e in exercises[page * 8:(page + 1) * 8]]
    nav = []
    if page:
        nav.append(button(workout, 'page', '‹', page - 1))
    if (page + 1) * 8 < len(exercises):
        nav.append(button(workout, 'page', '›', page + 1))
    if nav:
        rows.append(nav)
    rows.extend([[button(workout, 'newexercise', tr('add', lang))],
                 [button(workout, 'savetemplate', tr('save_template', lang))],
                 [button(workout, 'inputbudget', tr('budget', lang, minutes=workout.available_minutes or '?'))],
                 [(tr('templates', lang), 'wot:list:0')]])
    if workout.feedback.get('template_edit'):
        rows.append([button(workout, 'closeeditor', tr('cancel', lang))])
    else:
        rows.append([button(workout, 'finish', tr('finish', lang))])
    if workout.pending:
        rows.insert(0, [button(workout, 'cancelinput', tr('cancel', lang))])
    title = (tr('editing', lang) if workout.feedback.get('template_edit') else tr('title', lang)) + (' · ' + source['name'] if source else '') + f' · {len(saved)} ✓'
    if source:
        title += '\n' + tr('scheduled', lang)
    if source.get('estimated_minutes') is not None:
        title += '\n' + tr('estimated', lang, minutes=source['estimated_minutes'])
    if source.get('budget_adjusted'):
        title += '\n' + tr('time_adjusted', lang)
    if await svc.previous_pain(db, workout):
        title += '\n' + tr('safety', lang)
    await emit(message, title, rows, edit)


async def card(db, message, workout, entry, lang, edit=False):
    exercise = await svc.owned(db, Exercise, entry.exercise_id, workout.user_id)
    if exercise.kind == 'cardio':
        await cardio_card(message, workout, entry, exercise, lang, edit)
        return
    lines = ([tr('editing', lang)] if workout.feedback.get('template_edit') else []) + [exercise.name]
    for i, row in enumerate(entry.draft):
        lines.append(f"{'▶ ' if i == entry.selected else ''}{i + 1}. " + format_set(row, lang))
    lines.extend(['', tr('quality', lang)])
    if await svc.previous_pain(db, workout):
        lines.extend(['', tr('safety', lang)])
    choose = [button(workout, 'select', ('✓ ' if i == entry.selected else '') + str(i + 1), entry.id, i)
              for i in range(len(entry.draft))]
    rows = [choose[i:i + 6] for i in range(0, len(choose), 6)]
    rows += [[button(workout, 'delta', f'{n * exercise.weight_step:+g} kg', entry.id, n)
              for n in (-2, -1, 1, 2)],
             [button(workout, 'repdelta', '−1', entry.id, -1), button(workout, 'repdelta', '+1', entry.id, 1)],
             [button(workout, 'add', tr('setadd', lang), entry.id),
              button(workout, 'remove', tr('setremove', lang), entry.id)],
             [button(workout, 'inputweight', tr('weight', lang), entry.id),
              button(workout, 'inputreps', tr('reps', lang), entry.id)],
             [button(workout, 'inputstep', tr('step', lang), entry.id),
              button(workout, 'confirm', tr('confirm', lang), entry.id)],
             [button(workout, 'save', tr('save', lang), entry.id),
              button(workout, 'cancel', tr('cancel', lang), entry.id)],
             [button(workout, 'page', tr('back', lang))]]
    if exercise.load_type == 'bodyweight':
        excluded = {'delta', 'inputweight', 'inputstep'}
        rows = [[b for b in row if b[1].split(':')[3] not in excluded] for row in rows]
        rows = [row for row in rows if row]
    selected = entry.draft[entry.selected]
    if selected.get('weight') is None and 'weight_min' in selected:
        lines.append(tr('range', lang))
        rows.insert(0, [button(workout, 'weight', str(selected[k]) + ' kg', entry.id, selected[k]) for k in ('weight_min', 'weight_max')])
    await emit(message, '\n'.join(lines), rows, edit)


async def numeric_summary(db, workout, lang):
    rows = [e for e in await svc.entries(db, workout) if e.actual]
    lines = [tr('summary', lang, n=len(rows))]
    for entry in rows:
        exercise = await svc.owned(db, Exercise, entry.exercise_id, workout.user_id)
        m = svc.metrics(entry.actual)
        if exercise.kind == 'cardio':
            lines.append(exercise.name + ': ' + format_set(entry.actual[0], lang))
            continue
        if exercise.load_type == 'bodyweight':
            lines.append(exercise.name + ': ' + tr('bodyweight', lang) + f" · {m['sets']} " + tr('sets', lang)
                         + f" · {m['known_reps']} " + tr('repetitions', lang)
                         + ('' if m['confirmed_sets'] == m['sets'] else ' ?'))
            continue
        max_weight = '?' if m['max_weight'] is None else f"{m['max_weight']:g}"
        volume = '?' if m['confirmed_volume'] is None else f"{m['confirmed_volume']:g}"
        lines.append(f"{exercise.name}: {m['sets']} / {max_weight} / {volume}"
                     + ('' if m['volume_complete'] else ' ?'))
    lines.append(tr('quality', lang))
    if workout.finished_at:
        user = await db.get(User, workout.user_id)
        ctx = await svc.context(db, workout, user)
        for item in ctx['exercises']:
            parts = []
            for label, key in [(tr('last', lang), 'comparison'), (tr('baseline', lang), 'since_baseline')]:
                if key in item:
                    c = item[key]
                    if 'minutes_delta' in c:
                        delta = c['minutes_delta']
                        parts.append(f"{label}: {delta:+g} {tr('minutes', lang)}" if delta is not None
                                     else f"{label}: ? {tr('minutes', lang)}")
                        continue
                    if c['max_weight_delta'] is None:
                        value = '?' if c['known_reps_delta'] is None else f"{c['known_reps_delta']:+d}"
                        parts.append(f"{label}: {value} {tr('repetitions', lang)}")
                        continue
                    volume = '?' if c['volume_delta'] is None else f"{c['volume_delta']:+g}"
                    parts.append(f"{label}: {c['sets_delta']:+d} {tr('sets', lang)}, {c['max_weight_delta']:+g} kg, {volume} kg×{tr('repetitions', lang)}")
            if parts:
                lines.append(item['name'] + ': ' + '; '.join(parts))
            else:
                lines.append(item['name'] + ': ' + tr('no_previous', lang))
    return '\n'.join(lines)


async def feedback_card(db, message, workout, user, lang, edit=False):
    text = await numeric_summary(db, workout, lang)
    rows = []
    f = workout.feedback
    if 'effort' not in f:
        text += '\n\n' + tr('effort', lang)
        rows = [[button(workout, 'effort', tr(k, lang), value=k) for k in ('easy', 'normal')],
                [button(workout, 'effort', tr(k, lang), value=k) for k in ('hard', 'too_hard')],
                [button(workout, 'effort', tr('skip', lang), value='unknown')]]
    elif f.get('effort') in ('hard', 'too_hard') and not f.get('marked'):
        text += '\n\n' + tr('markhard', lang)
        marked = f.get('hard_exercises', [])
        for e in await svc.entries(db, workout):
            if e.actual:
                ex = await svc.owned(db, Exercise, e.exercise_id, workout.user_id)
                rows.append([button(workout, 'markhard', ('✓ ' if ex.id in marked else '') + ex.name[:45], ex.id)])
        rows.append([button(workout, 'marked', tr('next', lang))])
    elif 'wish' not in f:
        text += '\n\n' + tr('wish', lang)
        rows = [[button(workout, 'wish', tr(k, lang), value=k) for k in ('same', 'lighter')],
                [button(workout, 'wish', tr('shorter', lang), value='shorter'),
                 button(workout, 'inputcomment', tr('comment', lang))],
                [button(workout, 'wish', tr('skip', lang), value='unknown')]]
    else:
        rows = [[button(workout, 'analyze', tr('analyze', lang))],
                [button(workout, 'inputcomment', tr('comment', lang))]]
    rows.append([button(workout, 'inputbudget', tr('budget', lang, minutes=workout.available_minutes or '?'))])
    rows.append([button(workout, 'savetemplate', tr('save_template', lang))])
    rows.append([button(workout, 'pain', ('✓ ' if f.get('pain') else '') + tr('pain', lang))])
    if f.get('pain'):
        text += '\n\n' + tr('safety', lang)
    if workout.pending:
        text += '\n\n' + tr(workout.pending['mode'], lang)
        rows.append([button(workout, 'cancelinput', tr('cancel', lang))])
    rows.append([(tr('new', lang), 'woh:choose')])
    await emit(message, text, rows, edit)


async def plan_card(db, message, plan, user, lang, edit=False):
    workout = await svc.owned(db, Workout, plan.workout_id, user.telegram_id)
    data = workout.analysis or {}
    text = await numeric_summary(db, workout, lang)
    text += '\n\n' + tr('workout_report', lang)
    if data.get('summary'):
        text += '\n' + data['summary']
    text += '\n' + tr('auto_template_saved', lang)
    if workout.feedback.get('pain'):
        text += '\n' + tr('safety', lang)
    if data.get('explanation'):
        text += '\n\n' + tr('next_focus', lang) + '\n' + data['explanation']
    text += '\n\n' + tr('plan', lang)
    if not (svc.goals(user)['primary'] or svc.goals(user).get('training', {}).get('primary')):
        text += '\n' + tr('no_goal', lang)
    for item in plan.items:
        ex = await svc.owned(db, Exercise, item['exercise_id'], user.telegram_id)
        text += '\n' + ex.name + ': ' + '; '.join(format_set(s, lang) for s in item['sets'])
    if plan.target_template:
        name = plan.target_template['name']
        text += '\n' + (tr('auto_template', lang) if name == wt.LAST_WORKOUT_TEMPLATE else name)
    if plan.items:
        text += '\n' + tr('estimated', lang, minutes=wt.estimate_minutes(plan.items))
        text += '\n' + tr('budget', lang, minutes=data.get('time_budget') or '?')
    rows = []
    if plan.goals != svc.goals(user):
        text += '\n' + tr('changed', lang)
        rows.append([button(workout, 'reconsider', tr('analyze', lang))])
    elif plan.status == 'proposed' and plan.items:
        def pb(action, label):
            return label, f'wop:{plan.id}:{plan.revision}:{action}'
        rows = [[pb('accept', tr('accept', lang)), pb('lighter', tr('lighter', lang))],
                [button(workout, 'inputcomment', tr('edit', lang))]]
    elif plan.status == 'accepted':
        text += '\n' + tr('saved', lang)
    rows.append([button(workout, 'pain', ('✓ ' if workout.feedback.get('pain') else '') + tr('pain', lang))])
    rows.append([(tr('new', lang), 'woh:choose')])
    # Split long content, keep actions attached to final piece.
    while len(text) > 3900:
        await message.answer(text[:3900], parse_mode=None)
        text = text[3900:]
        edit = False
    await emit(message, text, rows, edit)


async def run_analysis(db, message, workout, user, lang):
    workout_id = workout.id
    status = await message.answer(tr('busy', lang), reply_markup=reply.get_workout_menu(lang))
    try:
        await svc.analyze(db, workout, user, lang)
        await db.refresh(workout)
        if workout.analysis_state != 'done':
            return
        plan = await db.scalar(select(WorkoutPlan).where(WorkoutPlan.workout_id == workout.id))
        await plan_card(db, status, plan, user, lang, edit=True)
    except Exception:
        logger.exception('Workout analysis failed workout=%s', workout_id)
        await db.refresh(workout)
        await status.edit_text(tr('error', lang), reply_markup=keyboard([
            [button(workout, 'analyze', tr('analyze', lang))]]))


@router.message(Command('workout'))
@router.message(F.text.in_(get_all_translations('btn_workout')))
async def home(message, state, user_language, db_user):
    if message.chat.id != message.from_user.id:
        return
    await state.clear()
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        freq = wt.training_preferences(user).get('sessions_per_week')
        workout = await svc.current(db, user.telegram_id)
        menu_mode = 'draft' if workout and not workout.feedback.get('template_edit') else 'home'
        await message.answer(tr('title', user_language), reply_markup=reply.get_workout_menu(
            user_language, menu_mode, frequency=freq))
        if workout:
            if not workout.feedback.get('template_edit') and not workout.pending:
                await svc.bump(db, workout, workout.revision, 'active')
                workout.pending = {'mode': 'manual_entries'}
                await db.commit()
            if workout.goals != svc.goals(user):
                await message.answer(tr('changed', user_language))
            if workout.pending.get('mode') != 'manual_entries':
                # Reopen an in-progress inline card after restart when applicable.
                rows = await svc.entries(db, workout)
                entry = next((e for e in rows if e.draft != e.actual), None)
                if entry:
                    await card(db, message, workout, entry, user_language)
                    if workout.pending:
                        await message.answer(tr(workout.pending.get('mode', 'weight'), user_language))
                else:
                    await library(db, message, workout, user_language)
        else:
            # Clean workout hub entry: no spamming of old finished workout cards or template selection menus.
            pass


@router.message(F.text.in_(get_all_translations('btn_add_workout')))
async def add_workout_from_add_menu(message, state, user_language, db_user):
    if message.chat.id != message.from_user.id:
        return
    await state.clear()
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        workout = await svc.start(db, user)
        if workout.feedback.get('template_edit'):
            await message.answer(tr('stale', user_language))
            return
        if workout.pending.get('mode') != 'manual_entries':
            await svc.bump(db, workout, workout.revision, 'active')
            workout.pending = {'mode': 'manual_entries'}
            await db.commit()
        await message.answer(tr('manual_entries', user_language), reply_markup=reply.get_workout_menu(user_language, 'draft'))


@router.message(F.text.in_(get_all_translations('btn_workout_done')))
async def finish_chat_workout(message, user_language, db_user):
    if message.chat.id != message.from_user.id:
        return
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        freq = wt.training_preferences(user).get('sessions_per_week')
        workout = await svc.current(db, user.telegram_id)
        if not workout:
            await message.answer(tr('workout_empty', user_language), reply_markup=reply.get_workout_menu(user_language, 'home', frequency=freq))
            return
        if workout.pending.get('mode') in ('parsing', 'persisting'):
            await message.answer(tr('workout_parsing', user_language), reply_markup=reply.get_workout_menu(user_language, 'draft'))
            return

        raw_lines = workout.feedback.get('raw_exercises', [])
        if not raw_lines and workout.pending.get('messages'):
            raw_lines = [
                line.strip()
                for m in workout.pending['messages']
                for line in m.get('text', '').splitlines()
                if line.strip()
            ]

        if not raw_lines:
            entries = await svc.entries(db, workout)
            if not any(entry.actual for entry in entries):
                await message.answer(tr('workout_empty', user_language), reply_markup=reply.get_workout_menu(user_language, 'draft'))
                return
            try:
                await svc.bump(db, workout, workout.revision, 'active')
                await svc.finish(db, workout)
                await wt.auto_save_template(db, workout, user_language)
            except ValueError:
                feedback_key = 'workout_empty' if not any(entry.actual for entry in entries) else 'invalid'
                await message.answer(tr(feedback_key, user_language), reply_markup=reply.get_workout_menu(user_language, 'draft'))
                return
            from src.services.workout_progress import award
            await award(db, user)
            await db.commit()
            report_text = await svc.build_workout_report(db, workout, user, user_language)
            await message.answer(report_text, reply_markup=reply.get_workout_menu(user_language, 'home', frequency=freq))
            return

        # Parsing raw text via LLM
        try:
            await svc.bump(db, workout, workout.revision, 'active')
        except svc.StaleWorkout:
            await message.answer(tr('workout_parsing', user_language),
                                 reply_markup=reply.get_workout_menu(user_language, 'draft'))
            return

        workout.pending = {'mode': 'parsing', 'raw_exercises': raw_lines}
        parse_revision = workout.revision
        await db.commit()

        status_msg = await message.answer(tr('workout_parsing', user_language))

        async def _safe_edit_or_answer(text: str, reply_markup=None):
            try:
                await status_msg.edit_text(text, reply_markup=reply_markup)
            except Exception:
                logger.warning('Failed to edit status message, sending new answer: %s', text[:50])
                await message.answer(text, reply_markup=reply_markup)

        try:
            from src.services.workout_input import parse_lines
            known_names = (await db.scalars(select(Exercise.name).where(
                Exercise.user_id == user.telegram_id).order_by(Exercise.id))).all()
            parsed = await parse_lines(raw_lines, user_language, user.telegram_id, known_names)
        except Exception:
            logger.exception('Workout input parsing failed workout=%s', workout.id)
            result = await db.execute(update(Workout).where(
                Workout.id == workout.id, Workout.user_id == user.telegram_id,
                Workout.status == 'active', Workout.revision == parse_revision,
            ).values(pending={'mode': 'manual_entries'}, revision=parse_revision + 1)
                .execution_options(synchronize_session=False))
            await db.commit()
            if result.rowcount:
                await _safe_edit_or_answer(tr('workout_parse_error', user_language))
            else:
                await _safe_edit_or_answer(tr('stale', user_language))
            return

        # Check if clarification is required
        if parsed.clarifications:
            cl = parsed.clarifications[0]
            await db.refresh(workout)
            workout.pending = {
                'mode': 'needs_clarification',
                'parsed': parsed.model_dump(),
                'clarification_id': cl.id,
            }
            workout.revision += 1
            await db.commit()

            rows = [
                [(tr('clarify_btn_yes', user_language), f"woc:clarify:{workout.id}:{cl.id}:yes")],
                [(tr('clarify_btn_edit', user_language), f"woc:clarify:{workout.id}:{cl.id}:edit")],
            ]
            await _safe_edit_or_answer(cl.question, reply_markup=keyboard(rows))
            return

        # Atomically save facts
        try:
            await db.refresh(workout)
            await svc.finish_parsed_workout(db, workout, user, parsed, user_language)
        except svc.StaleWorkout:
            await db.rollback()
            await _safe_edit_or_answer(tr('stale', user_language))
            return
        except Exception:
            logger.exception('Workout parsed facts could not be saved workout=%s', workout.id)
            await db.rollback()
            await db.execute(update(Workout).where(
                Workout.id == workout.id, Workout.user_id == user.telegram_id,
                Workout.status == 'active', Workout.revision == parse_revision,
            ).values(pending={'mode': 'manual_entries'}, revision=parse_revision + 1)
                .execution_options(synchronize_session=False))
            await db.commit()
            await _safe_edit_or_answer(tr('workout_parse_error', user_language))
            return

        report_text = await svc.build_workout_report(db, workout, user, user_language)
        await _safe_edit_or_answer(report_text)
        await message.answer(tr('title', user_language), reply_markup=reply.get_workout_menu(user_language, 'home', frequency=freq))


@router.callback_query(F.data.startswith('woc:clarify:'))
async def handle_workout_clarification(callback, user_language, db_user):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return

    parts = callback.data.split(':')
    if len(parts) < 5:
        return
    workout_id = int(parts[2])
    clarification_id = parts[3]
    action = parts[4]

    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        freq = wt.training_preferences(user).get('sessions_per_week')
        workout = await db.scalar(select(Workout).where(Workout.id == workout_id, Workout.user_id == user.telegram_id))
        async def _safe_edit_or_answer(text: str, reply_markup=None):
            try:
                await callback.message.edit_text(text, reply_markup=reply_markup)
            except Exception:
                logger.warning('Failed to edit callback message, sending new answer: %s', text[:50])
                await callback.message.answer(text, reply_markup=reply_markup)

        if not workout or workout.status != 'active' or workout.pending.get('mode') != 'needs_clarification':
            await _safe_edit_or_answer(tr('stale', user_language))
            return

        from src.services.workout_input import ParsedWorkout, apply_clarification

        if action == 'edit':
            workout.pending = {'mode': 'manual_entries'}
            workout.revision += 1
            await db.commit()
            await _safe_edit_or_answer(tr('manual_entries', user_language))
            return

        if action == 'yes':
            raw_parsed = workout.pending.get('parsed')
            if not raw_parsed:
                await _safe_edit_or_answer(tr('stale', user_language))
                return
            parsed = ParsedWorkout.model_validate(raw_parsed)
            parsed = apply_clarification(parsed, clarification_id, 'yes')

            if parsed.clarifications:
                next_cl = parsed.clarifications[0]
                workout.pending = {
                    'mode': 'needs_clarification',
                    'parsed': parsed.model_dump(),
                    'clarification_id': next_cl.id,
                }
                workout.revision += 1
                await db.commit()
                rows = [
                    [(tr('clarify_btn_yes', user_language), f"woc:clarify:{workout.id}:{next_cl.id}:yes")],
                    [(tr('clarify_btn_edit', user_language), f"woc:clarify:{workout.id}:{next_cl.id}:edit")],
                ]
                await _safe_edit_or_answer(next_cl.question, reply_markup=keyboard(rows))
                return

            try:
                await svc.finish_parsed_workout(db, workout, user, parsed, user_language)
            except Exception:
                logger.exception('Failed to save clarified workout facts workout=%s', workout.id)
                await db.rollback()
                await _safe_edit_or_answer(tr('workout_parse_error', user_language))
                return

            report_text = await svc.build_workout_report(db, workout, user, user_language)
            await _safe_edit_or_answer(report_text)
            await callback.message.answer(tr('title', user_language), reply_markup=reply.get_workout_menu(user_language, 'home', frequency=freq))


@router.message(F.text.in_(get_all_translations('btn_workout_cancel')))
async def cancel_chat_workout(message, user_language, db_user):
    if message.chat.id != message.from_user.id:
        return
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        freq = wt.training_preferences(user).get('sessions_per_week')
        workout = await svc.current(db, db_user.telegram_id)
        if workout:
            await svc.cancel_workout(db, workout, workout.revision)
    await message.answer(tr('workout_cancelled', user_language), reply_markup=reply.get_workout_menu(user_language, 'home', frequency=freq))


@router.message(F.text.in_(get_all_translations('btn_workout_template')))
async def choose_template_for_chat(message, user_language, db_user):
    if message.chat.id != message.from_user.id:
        return
    async with AsyncSessionLocal() as db:
        workout = await svc.current(db, db_user.telegram_id)
        raw_lines = (workout.feedback.get('raw_exercises', []) if workout else [])
        if not raw_lines and workout and workout.pending.get('messages'):
            raw_lines = [m.get('text', '') for m in workout.pending['messages'] if m.get('text')]
        if raw_lines:
            rows = [
                [(tr('continue_draft', user_language), 'wot:draft:keep')],
                [(tr('replace_draft', user_language), 'wot:draft:replace')],
            ]
            await emit(message, tr('template_has_draft_prompt', user_language), rows)
            return

        choices = await wt.templates(db, db_user.telegram_id)
        choices.sort(key=lambda template: template.name != wt.LAST_WORKOUT_TEMPLATE)
        if not choices:
            await message.answer(tr('no_templates_found', user_language))
            return

        rows = [[(template_name(template, user_language), f'wot:preview:{template.id}')] for template in choices[:20]]
        rows.append([(tr('cancel', user_language), 'wot:cancel_preview')])
        await emit(message, tr('choose_template_to_load', user_language), rows)


@router.message(F.text.in_(get_all_translations('btn_workout_today')))
async def open_today_workout(message, state, user_language, db_user):
    await state.clear()
    if message.chat.id != message.from_user.id:
        return
    from src.services import workout_today as daily
    from src.handlers.workout_daily import panel
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        plan = await daily.today(db, user)
        await panel(db, message, plan, user, user_language)


@router.message(F.text.in_(get_all_translations('btn_workout_templates')))
async def open_workout_templates(message, state, user_language, db_user):
    await state.clear()
    if message.chat.id != message.from_user.id:
        return
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        await template_menu(db, message, user, user_language)


class WorkoutFrequencyButtonFilter(BaseFilter):
    async def __call__(self, message) -> bool:
        if not message.text or message.chat.id != message.from_user.id:
            return False
        text = message.text.strip()
        all_static = (
            get_all_translations('btn_workout_frequency')
            + get_all_translations('btn_workout_frequency_default')
        )
        if text in all_static:
            return True
        pattern = r'^(?:🔁\s*)?(?:\d+\s+(?:в неделю|на тиждень|w tygodniu|pro Woche|per week|por semana)|Haftada\s+\d+|Частота).*'
        return bool(re.match(pattern, text, re.IGNORECASE))


@router.message(WorkoutFrequencyButtonFilter())
async def open_workout_frequency(message, state, user_language, db_user):
    await state.clear()
    if message.chat.id != message.from_user.id:
        return
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        selected = wt.training_preferences(user).get('sessions_per_week')
        user.ux_preferences = {**(user.ux_preferences or {}), 'workout_frequency_input': True}
        await db.commit()
    text = tr('frequency_prompt', user_language)
    if selected:
        text += '\n' + tr('frequency', user_language, count=selected)
    await message.answer(text, reply_markup=reply.get_workout_menu(user_language, 'frequency'))


@router.message(F.text.in_(get_all_translations('btn_workout_history')))
async def open_workout_history(message, state, user_language):
    await state.clear()
    if message.chat.id != message.from_user.id:
        return
    parts = urlsplit(settings.WEBAPP_URL)
    query = dict(parse_qsl(parts.query))
    query['tab'] = 'workouts'
    url = urlunsplit(parts._replace(query=urlencode(query), fragment=''))
    label = tr('btn_workout_history', user_language)
    await message.bot.set_chat_menu_button(chat_id=message.chat.id,
        menu_button=MenuButtonWebApp(text=label, web_app=WebAppInfo(url=url)))
    from src.middlewares.i18n import _user_menu_button_cache
    _user_menu_button_cache.pop(message.chat.id, None)
    from src.utils.i18n_locales import get_text as locale_text
    await message.answer(locale_text('ux_open_menu', user_language, section=label),
                         reply_markup=reply.get_workout_menu(user_language))


class WorkoutFrequencyInput(BaseFilter):
    async def __call__(self, message):
        if not message.text or not re.fullmatch(r'[1-7]', message.text):
            return False
        async with AsyncSessionLocal() as db:
            user = await db.get(User, message.from_user.id)
            return bool(user and (user.ux_preferences or {}).get('workout_frequency_input'))


@router.message(StateFilter(None), WorkoutFrequencyInput())
async def save_workout_frequency(message, user_language, db_user):
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        prefs = dict(user.ux_preferences or {})
        count = int(message.text)
        user.training_preferences = {**(user.training_preferences or {}), 'sessions_per_week': count}
        user.ux_preferences = {**prefs, 'workout_frequency_input': False}
        await db.commit()
    await message.answer(tr('frequency', user_language, count=count),
                         reply_markup=reply.get_workout_menu(user_language, 'home', frequency=count))


class ManualWorkoutInput(BaseFilter):
    async def __call__(self, message):
        if not message.text or message.text.startswith('/') or message.chat.id != message.from_user.id:
            return False
        async with AsyncSessionLocal() as db:
            workout = await svc.current(db, message.from_user.id)
            return bool(workout and workout.pending.get('mode') in ('manual_entries', 'collecting', 'parsing')
                        and not workout.feedback.get('template_edit'))


@router.message(StateFilter(None), ManualWorkoutInput())
async def manual_workout_input(message, user_language):
    async with AsyncSessionLocal() as db:
        workout = await svc.current(db, message.from_user.id)
        if not workout:
            return
        if workout.pending.get('mode') in ('parsing', 'persisting'):
            return
        lines = [line.strip() for line in message.text.splitlines() if line.strip()]
        if not lines:
            return
        raw = list(workout.feedback.get('raw_exercises', []))
        if len(raw) + len(lines) > 30 or any(len(line) > 500 for line in lines):
            await message.answer(tr('workout_draft_limit', user_language),
                                 reply_markup=reply.get_workout_menu(user_language, 'draft'))
            return
        try:
            await svc.bump(db, workout, workout.revision, 'active')
            messages = list(workout.pending.get('messages', []))
            existing_msg = next((m for m in messages if m.get('message_id') == message.message_id), None)
            if existing_msg:
                existing_msg['text'] = message.text
            else:
                messages.append({'message_id': message.message_id, 'text': message.text, 'order': len(messages)})
            workout.feedback = {**workout.feedback, 'raw_exercises': [*raw, *lines]}
            workout.pending = {'mode': 'manual_entries', 'messages': messages}
            await db.commit()
            # Bot stays silent during recording: Section 5.1: "Бот молчит во время ввода"
        except ValueError:
            return


@router.callback_query(F.data == 'woh:new')
async def new_workout(callback, state, user_language, db_user):
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        await callback.answer()
        return
    await callback.answer()
    await state.clear()
    async with AsyncSessionLocal() as db:
        user = await db.get(User, db_user.telegram_id)
        workout = await svc.start(db, user)
        if not workout.feedback.get('template_edit') and workout.pending.get('mode') != 'manual_entries':
            await svc.bump(db, workout, workout.revision, 'active')
            workout.pending = {'mode': 'manual_entries'}
            await db.commit()
        await library(db, callback.message, workout, user_language, edit=True)


@router.callback_query(F.data.startswith('wo:'))
async def action(callback, state, user_language, db_user):
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        await callback.answer()
        return
    await callback.answer()
    await state.clear()
    lang, uid = user_language, callback.from_user.id
    try:
        _, wid, revision, op, target, value = callback.data.split(':')
        wid, revision, target = int(wid), int(revision), int(target)
        async with AsyncSessionLocal() as db:
            user = await db.get(User, uid)
            w = await svc.owned(db, Workout, wid, uid)
            if w.analysis_state == 'running' and w.analysis_started_at and (svc.now() - w.analysis_started_at).total_seconds() < 180:
                await callback.message.answer(tr('busy', lang))
                return
            if w.status not in ('active', 'finished'):
                raise svc.StaleWorkout()
            await svc.bump(db, w, revision)
            entry = None
            if w.status == 'active':
                if op == 'open':
                    entry = await svc.open_entry(db, w, target, user)
                    w.pending = {}
                elif op in ('page', 'cancelinput'):
                    w.pending = {}
                elif op == 'newexercise':
                    w.pending = {'mode': 'name_prompt', 'kind': 'strength'}
                elif op == 'type' and value in ('strength', 'cardio', 'bodyweight'):
                    w.pending = {'mode': 'name_prompt', 'kind': value}
                elif op == 'savetemplate':
                    w.pending = {'mode': 'template_name'}
                elif op == 'inputbudget':
                    w.pending = {'mode': 'budget_prompt'}
                elif op.startswith('input') and op in ('inputweight', 'inputreps', 'inputstep', 'inputminutes'):
                    e = await svc.owned(db, WorkoutEntry, target, uid)
                    if e.workout_id != w.id:
                        raise svc.StaleWorkout()
                    w.pending = {'mode': op[5:], 'entry_id': target, 'selected': e.selected}
                elif op == 'closeeditor' and w.feedback.get('template_edit'):
                    w.status, w.pending = 'template', {}
                elif op == 'finish':
                    if w.feedback.get('raw_exercises'):
                        w.pending = {'mode': 'manual_entries'}
                        await db.commit()
                        await callback.message.answer(tr('workout_input_prompt', lang),
                            reply_markup=reply.get_workout_menu(lang, 'draft'))
                        return
                    await svc.finish(db, w)
                    await wt.save_last_workout(db, w)
                elif op in ('select', 'delta', 'repdelta', 'add', 'remove', 'confirm', 'save', 'cancel', 'minutes', 'minutedelta', 'intensity', 'weight'):
                    entry = await svc.mutate_entry(db, w, target, op, value)
                    w.pending = {}
                    if op == 'cancel' and entry.actual is None:
                        await db.delete(entry)
                        entry = None
                    elif op in ('cancel', 'save'):
                        entry = None
                else:
                    raise svc.StaleWorkout()
                if (w.status == 'active' and not w.feedback.get('template_edit') and not w.pending
                        and entry is None and op in ('page', 'cancelinput', 'save', 'cancel')):
                    w.pending = {'mode': 'manual_entries'}
            else:
                if op == 'effort' and value in ('easy', 'normal', 'hard', 'too_hard', 'unknown'):
                    w.feedback = {**w.feedback, 'effort': value}
                elif op == 'wish' and value in ('same', 'lighter', 'shorter', 'unknown'):
                    w.feedback = {**w.feedback, 'wish': value}
                elif op == 'marked':
                    w.feedback = {**w.feedback, 'marked': True}
                elif op == 'markhard':
                    valid = {e.exercise_id for e in await svc.entries(db, w) if e.actual}
                    if target not in valid:
                        raise svc.StaleWorkout()
                    marked = set(w.feedback.get('hard_exercises', []))
                    marked.symmetric_difference_update({target})
                    w.feedback = {**w.feedback, 'hard_exercises': sorted(marked)}
                elif op == 'pain':
                    w.feedback = {**w.feedback, 'pain': not w.feedback.get('pain', False)}
                elif op in ('savetemplate', 'inputbudget'):
                    w.pending = {'mode': 'template_name' if op == 'savetemplate' else 'budget_prompt'}
                elif op == 'inputcomment':
                    w.pending = {'mode': 'comment_prompt'}
                elif op == 'cancelinput':
                    w.pending = {}
                elif op == 'reconsider':
                    w.analysis_state = 'idle'
                elif op != 'analyze':
                    raise svc.StaleWorkout()
                if op not in ('analyze', 'cancelinput', 'savetemplate'):
                    w.analysis_state = 'idle'
                    plan = await db.scalar(select(WorkoutPlan).where(WorkoutPlan.workout_id == w.id))
                    if plan:
                        plan.status = 'superseded'
            await db.commit()
            if op == 'finish':
                from src.services.workout_progress import award
                await award(db, user)
                await feedback_card(db, callback.message, w, user, lang, edit=True)
                return
            if w.status == 'template':
                await template_menu(db, callback.message, user, lang, edit=True)
            elif w.status == 'finished':
                if op in ('analyze', 'wish'):
                    await run_analysis(db, callback.message, w, user, lang)
                else:
                    await feedback_card(db, callback.message, w, user, lang, edit=True)
            elif w.pending:
                prompt = tr(w.pending['mode'], lang)
                prompt_rows = [[button(w, 'cancelinput', tr('cancel', lang))]]
                if w.pending['mode'] == 'name_prompt':
                    prompt_rows.insert(0, [button(w, 'type', tr(k, lang), value=k) for k in ('strength', 'cardio', 'bodyweight')])
                await emit(callback.message, prompt, prompt_rows, edit=True)
            elif entry:
                await card(db, callback.message, w, entry, lang, edit=True)
            else:
                await library(db, callback.message, w, lang, page=target if op == 'page' else 0, edit=True)
    except svc.StaleWorkout:
        await callback.message.answer(tr('stale', lang))
    except (ValueError, TypeError):
        await callback.message.answer(tr('invalid', lang))


class PendingInput(BaseFilter):
    async def __call__(self, message):
        if not message.text or message.text.startswith('/') or message.chat.id != message.from_user.id:
            return False
        async with AsyncSessionLocal() as db:
            user = await db.get(User, message.from_user.id)
            if user and (user.ux_preferences or {}).get('workout_budget_input'):
                return {'workout_input_id': 0}
            rows = (await db.scalars(select(Workout).where(Workout.user_id == message.from_user.id)
                                     .order_by(Workout.id.desc()).limit(2))).all()
            pending = next((w for w in rows if w.pending), None)
            return {'workout_input_id': pending.id} if pending else False


@router.message(StateFilter(None), PendingInput())
async def text_input(message, workout_input_id, user_language):
    lang = user_language
    try:
        async with AsyncSessionLocal() as db:
            if workout_input_id == 0:
                user = await db.get(User, message.from_user.id)
                if not (user.ux_preferences or {}).get('workout_budget_input'):
                    raise svc.StaleWorkout()
                minutes = parse_budget(message.text)
                user.training_preferences = {**(user.training_preferences or {}), 'minutes': minutes}
                user.ux_preferences = {**user.ux_preferences, 'workout_budget_input': False}
                await db.commit()
                await goals_card(message, user, lang)
                return
            w = await svc.owned(db, Workout, workout_input_id, message.from_user.id)
            user = await db.get(User, message.from_user.id)
            pending = w.pending
            await svc.bump(db, w, w.revision)
            entry = None
            if pending['mode'] == 'name_prompt' and w.status == 'active':
                ex = await svc.add_exercise(db, w.user_id, message.text, kind=pending.get('kind', 'strength'))
                entry = await svc.open_entry(db, w, ex.id, user)
            elif pending['mode'] == 'template_name':
                await wt.save_template(db, w, message.text)
                if w.feedback.get('template_edit'):
                    w.status = 'template'
            elif pending['mode'] == 'budget_prompt':
                w.available_minutes = parse_budget(message.text)
                w.feedback = {**w.feedback, 'time_override': True}
                if w.status == 'finished':
                    w.analysis_state = 'idle'
                    plan = await db.scalar(select(WorkoutPlan).where(WorkoutPlan.workout_id == w.id))
                    if plan:
                        plan.status = 'superseded'
            elif pending['mode'] == 'comment_prompt' and w.status == 'finished':
                if len(message.text) > 1000:
                    raise ValueError('Comment too long')
                w.feedback = {**w.feedback, 'comment': message.text, 'wish': 'custom',
                              'pain': w.feedback.get('pain', False) or svc.mentions_pain(message.text)}
                w.analysis_state = 'idle'
            elif w.status == 'active' and pending['mode'] in ('weight', 'reps', 'step', 'minutes'):
                entry = await svc.mutate_entry(db, w, pending['entry_id'], pending['mode'], message.text)
            else:
                raise svc.StaleWorkout()
            w.pending = {}
            if w.status == 'active' and not w.feedback.get('template_edit'):
                w.pending = {'mode': 'manual_entries'}
            await db.commit()
            if entry:
                await card(db, message, w, entry, lang)
            elif w.status == 'active':
                await library(db, message, w, lang)
            elif w.status == 'template':
                await template_menu(db, message, user, lang)
            else:
                await feedback_card(db, message, w, user, lang)
    except svc.StaleWorkout:
        await message.answer(tr('stale', lang))
    except (ValueError, TypeError):
        await message.answer(tr('invalid', lang))


@router.callback_query(F.data.startswith('wop:'))
async def plan_action(callback, user_language, db_user):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    try:
        _, pid, revision, op = callback.data.split(':')
        async with AsyncSessionLocal() as db:
            user = await db.get(User, callback.from_user.id)
            plan = await svc.owned(db, WorkoutPlan, int(pid), user.telegram_id)
            await svc.change_plan(db, plan, int(revision), user, op)
            if op == 'lighter':
                plan.explanation = tr('reduced_mixed' if any(i.get('kind') == 'cardio' for i in plan.items) else 'reduced', user_language)
                await db.commit()
            await plan_card(db, callback.message, plan, user, user_language, edit=True)
    except (ValueError, TypeError):
        await callback.message.answer(tr('stale', user_language))


async def goals_card(message, user, lang, edit=False):
    current = svc.goals(user)
    title = tr('goals', lang) + '\n★ ' + (get_text(GOAL_KEYS[current['primary']], lang)
                                                   if current['primary'] in GOAL_KEYS else tr('no_goal', lang))
    rows = [[(('✓ ' if g in current['additional'] else '') + get_text(GOAL_KEYS[g], lang),
              f'wog:{g}:{"remove" if g in current["additional"] else "add"}')]
            for g in svc.GOALS if g != current['primary']]
    prefs = wt.training_preferences(user)
    title += '\n\n' + tr('training', lang)
    for priority in wt.PRIORITIES:
        main = prefs['primary'] == priority
        extra = priority in prefs['additional']
        rows.append([(('★ ' if main else '') + tr('primary', lang) + ': ' + tr(priority, lang), f'wotr:primary:{priority}'),
                     (('✓ ' if extra else '+ ') + tr(priority, lang), f'wotr:extra:{priority}:{"remove" if extra else "add"}')])
    rows.append([(tr('budget', lang, minutes=prefs['minutes'] or '?'), 'wotr:time')])
    rows.append([(tr('frequency', lang, count=prefs.get('sessions_per_week') or '?'), 'wf:open')])
    await emit(message, title, rows, edit)


@router.message(Command('workout_goals'))
@router.message(F.text.in_(get_all_translations('btn_workout_goals')))
async def profile_goals(message, state, user_language, db_user):
    if message.chat.id != message.from_user.id:
        return
    await state.clear()
    await goals_card(message, db_user, user_language)


@router.callback_query(F.data.startswith('wog:'))
async def toggle_goal(callback, user_language):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    try:
        _, goal, op = callback.data.split(':')
        if goal not in svc.GOALS or op not in ('add', 'remove'):
            raise ValueError()
        async with AsyncSessionLocal() as db:
            user = await db.scalar(select(User).where(User.telegram_id == callback.from_user.id).with_for_update())
            selected = set(user.additional_goals or [])
            if goal != user.goal:
                if op == 'add':
                    selected.add(goal)
                else:
                    selected.discard(goal)
            selected.discard(user.goal)
            user.additional_goals = [g for g in svc.GOALS if g in selected]
            await db.commit()
            await goals_card(callback.message, user, user_language, edit=True)
    except (ValueError, TypeError):
        await callback.message.answer(tr('stale', user_language))


def parse_budget(value):
    minutes = svc.number(value, 0, 240, integer=True)
    if 0 < minutes < 5:
        raise ValueError('Minimum budget is 5 minutes')
    return minutes or None


async def template_menu(db, message, user, lang, page=0, edit=False):
    choices = await wt.templates(db, user.telegram_id)
    page = max(0, min(page, max(0, (len(choices) - 1) // 8)))
    rows = [[(template_name(t, lang), f'wot:details:{t.id}')]
            for t in choices[page * 8:(page + 1) * 8]]
    nav = []
    if page:
        nav.append(('‹', f'wot:list:{page - 1}'))
    if (page + 1) * 8 < len(choices):
        nav.append(('›', f'wot:list:{page + 1}'))
    if nav:
        rows.append(nav)
    rows.extend([[(tr('example', lang), 'wot:import')], [(tr('back', lang), 'woh:choose')]])
    await emit(message, tr('templates', lang) + '\n' + tr('example_note', lang), rows, edit)


@router.callback_query(F.data == 'woh:choose')
async def choose_workout(callback, state, user_language, db_user):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    await state.clear()
    async with AsyncSessionLocal() as db:
        choices = await wt.templates(db, callback.from_user.id)
        choices.sort(key=lambda template: template.name != wt.LAST_WORKOUT_TEMPLATE)
        recommended = await wt.recommended(db, callback.from_user.id)
        text = tr('choose', user_language)
        if recommended:
            text += '\n' + tr('recommended', user_language, name=template_name(recommended, user_language))
        rows = [[(tr('free', user_language), 'wot:start:0')]]
        rows += [[(('★ ' if recommended and t.id == recommended.id else '') + template_name(t, user_language), f'wot:start:{t.id}')]
                 for t in choices[:20]]
        rows.append([(tr('templates', user_language), 'wot:list:0')])
        await emit(callback.message, text, rows, edit=True)


async def render_template_checklist(db, template, checked_indices: list[int], lang: str):
    t_name = template_name(template, lang)
    text = f"📋 {t_name}\n{tr('checklist_prompt', lang)}"
    rows = []
    for idx, item in enumerate(template.items):
        ex = await db.get(Exercise, item['exercise_id'])
        ex_name = ex.name if ex else "Упражнение"
        line_str = svc.format_exercise_line(ex_name, item.get('kind', 'strength'), item['sets'], lang)
        icon = "☑️" if idx in checked_indices else "⬜"
        rows.append([(f"{icon} {line_str}", f"wot:chk:{template.id}:{idx}")])

    rows.append([(tr('btn_save_completed', lang), f"wot:save_chk:{template.id}")])
    rows.append([(tr('cancel', lang), "wot:cancel_chk")])
    return text, rows


@router.callback_query(F.data.startswith('wot:'))
async def template_action(callback, state, user_language, db_user):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    await state.clear()
    lang = user_language
    try:
        parts = callback.data.split(':')
        op = parts[1]
        async with AsyncSessionLocal() as db:
            # Serializes import, editor starts and deletion per owner across workers.
            user = await db.scalar(select(User).where(User.telegram_id == callback.from_user.id).with_for_update())
            if op in ('cancel_preview', 'cancel_chk'):
                await callback.message.delete()
                return
            elif op == 'preview' and len(parts) == 3:
                tid = int(parts[2])
                template = await svc.owned(db, WorkoutTemplate, tid, user.telegram_id)
                if not template or not template.items:
                    await callback.message.edit_text(tr('stale', lang))
                    return

                workout = await svc.current(db, user.telegram_id)
                if not workout:
                    workout = await svc.start(db, user)

                checked = list(range(len(template.items)))
                workout.feedback = {
                    **(workout.feedback or {}),
                    'template_checklist': {
                        'template_id': template.id,
                        'checked': checked,
                    }
                }
                await db.commit()

                card_text, rows = await render_template_checklist(db, template, checked, lang)
                await emit(callback.message, card_text, rows, edit=True)
                return
            elif op == 'chk' and len(parts) == 4:
                tid = int(parts[2])
                idx = int(parts[3])
                template = await svc.owned(db, WorkoutTemplate, tid, user.telegram_id)
                workout = await svc.current(db, user.telegram_id)
                if not template or not workout:
                    await callback.message.edit_text(tr('stale', lang))
                    return

                chk_data = (workout.feedback or {}).get('template_checklist') or {'template_id': tid, 'checked': []}
                checked = list(chk_data.get('checked', []))

                if idx in checked:
                    checked.remove(idx)
                else:
                    checked.append(idx)
                    checked.sort()

                workout.feedback = {
                    **(workout.feedback or {}),
                    'template_checklist': {
                        'template_id': tid,
                        'checked': checked,
                    }
                }
                await db.commit()

                _, rows = await render_template_checklist(db, template, checked, lang)
                await callback.message.edit_reply_markup(reply_markup=keyboard(rows))
                return
            elif op == 'save_chk' and len(parts) == 3:
                tid = int(parts[2])
                template = await svc.owned(db, WorkoutTemplate, tid, user.telegram_id)
                workout = await svc.current(db, user.telegram_id)
                if not template or not workout or workout.status != 'active':
                    await callback.message.edit_text(tr('stale', lang))
                    return

                chk_data = (workout.feedback or {}).get('template_checklist') or {'template_id': tid, 'checked': []}
                checked = chk_data.get('checked', [])
                if not checked:
                    await callback.answer(tr('workout_empty', lang), show_alert=True)
                    return

                checked_items = [template.items[i] for i in checked if i < len(template.items)]
                workout.template_snapshot = wt.snapshot(template)
                await svc.finish_template_items(db, workout, user, checked_items, lang)

                report_text = await svc.build_workout_report(db, workout, user, lang)
                await callback.message.edit_text(report_text)
                freq = wt.training_preferences(user).get('sessions_per_week')
                await callback.message.answer(
                    tr('title', lang),
                    reply_markup=reply.get_workout_menu(lang, 'home', frequency=freq)
                )
                return
            elif op == 'draft' and len(parts) == 3:
                action = parts[2]
                if action == 'keep':
                    await callback.message.edit_text(tr('manual_entries', lang))
                    return
                elif action == 'replace':
                    active = await svc.current(db, user.telegram_id)
                    if active:
                        active.feedback = {k: v for k, v in active.feedback.items() if k not in ('raw_exercises', 'template_checklist')}
                        active.pending = {'mode': 'manual_entries', 'messages': []}
                        await db.commit()
                    choices = await wt.templates(db, user.telegram_id)
                    choices.sort(key=lambda template: template.name != wt.LAST_WORKOUT_TEMPLATE)
                    if not choices:
                        await callback.message.edit_text(tr('no_templates_found', lang))
                        return
                    rows = [[(template_name(template, lang), f'wot:preview:{template.id}')] for template in choices[:20]]
                    rows.append([(tr('cancel', lang), 'wot:cancel_preview')])
                    await emit(callback.message, tr('choose_template_to_load', lang), rows, edit=True)
                    return
            elif op == 'import' and len(parts) == 2:
                await wt.import_example(db, user.telegram_id)
                await db.commit()
                await template_menu(db, callback.message, user, lang, edit=True)
            elif op == 'list' and len(parts) == 3:
                await template_menu(db, callback.message, user, lang, page=int(parts[2]), edit=True)
            elif op in ('start', 'edit') and len(parts) == 3:
                tid = int(parts[2]) or None
                if op == 'edit' and not tid:
                    raise svc.StaleWorkout()
                active = await svc.current(db, user.telegram_id)
                if op == 'edit' and active and await svc.entries(db, active):
                    raise svc.StaleWorkout('Finish current workout first')
                workout = await svc.start(db, user, tid)
                if op == 'start' and not workout.feedback.get('template_edit'):
                    await svc.bump(db, workout, workout.revision, 'active')
                    workout.pending = {'mode': 'manual_entries'}
                    await db.commit()
                    await callback.message.answer(tr('workout_input_prompt', lang),
                        reply_markup=reply.get_workout_menu(lang, 'draft'))
                if op == 'edit':
                    workout.feedback = {**workout.feedback, 'template_edit': True}
                    source = await svc.owned(db, WorkoutTemplate, tid, user.telegram_id)
                    workout.template_snapshot = wt.snapshot(source)
                    for item in workout.template_snapshot['items']:
                        await svc.open_entry(db, workout, item['exercise_id'], user)
                    await db.commit()
                if op == 'edit':
                    await library(db, callback.message, workout, lang, edit=True)
            elif op in ('details', 'delete', 'remove') and len(parts) in (3, 4):
                template = await svc.owned(db, WorkoutTemplate, int(parts[2]), user.telegram_id)
                if op == 'details':
                    text = template_name(template, lang)
                    if template.minutes_max:
                        text += '\n' + tr('budget', lang, minutes=template.minutes_max)
                    for item in template.items:
                        ex = await svc.owned(db, Exercise, item['exercise_id'], user.telegram_id)
                        text += '\n' + ex.name + ': ' + '; '.join(format_set(s, lang) for s in item['sets'])
                    rows = [[(tr('new', lang), f'wot:start:{template.id}'),
                             (tr('edit', lang), f'wot:edit:{template.id}')],
                            [(tr('delete', lang), f'wot:delete:{template.id}:{template.revision}')],
                            [(tr('back', lang), 'wot:list:0')]]
                    await emit(callback.message, text, rows, edit=True)
                elif len(parts) != 4 or int(parts[3]) != template.revision:
                    raise svc.StaleWorkout()
                elif op == 'delete':
                    await emit(callback.message, tr('delete_confirm', lang), [
                        [(tr('delete', lang), f'wot:remove:{template.id}:{template.revision}')],
                        [(tr('back', lang), f'wot:details:{template.id}')]], edit=True)
                else:
                    result = await db.execute(delete(WorkoutTemplate).where(
                        WorkoutTemplate.id == template.id, WorkoutTemplate.user_id == user.telegram_id,
                        WorkoutTemplate.revision == int(parts[3])))
                    if result.rowcount != 1:
                        raise svc.StaleWorkout()
                    await db.commit()
                    await template_menu(db, callback.message, user, lang, edit=True)
            else:
                raise svc.StaleWorkout()
    except (ValueError, TypeError, IndexError):
        await callback.message.answer(tr('stale', lang))


@router.callback_query(F.data.startswith('wotr:'))
async def training_profile(callback, state, user_language):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    await state.clear()
    try:
        parts = callback.data.split(':')
        op = parts[1]
        async with AsyncSessionLocal() as db:
            user = await db.scalar(select(User).where(User.telegram_id == callback.from_user.id).with_for_update())
            prefs = wt.training_preferences(user)
            if op == 'time' and len(parts) == 2:
                user.ux_preferences = {**(user.ux_preferences or {}), 'workout_budget_input': True}
                await db.commit()
                await emit(callback.message, tr('budget_prompt', user_language), [
                    [(tr('cancel', user_language), 'wotr:cancel')]], edit=True)
                return
            if op == 'cancel' and len(parts) == 2:
                user.ux_preferences = {**(user.ux_preferences or {}), 'workout_budget_input': False}
            elif op == 'primary' and len(parts) == 3 and parts[2] in wt.PRIORITIES:
                prefs['primary'] = parts[2]
                prefs['additional'] = [g for g in prefs['additional'] if g != parts[2]]
            elif op == 'extra' and len(parts) == 4 and parts[2] in wt.PRIORITIES and parts[3] in ('add', 'remove'):
                selected = set(prefs['additional'])
                if parts[3] == 'add' and parts[2] != prefs['primary']:
                    selected.add(parts[2])
                else:
                    selected.discard(parts[2])
                prefs['additional'] = [p for p in wt.PRIORITIES if p in selected]
            else:
                raise svc.StaleWorkout()
            user.training_preferences = prefs
            await db.commit()
            await goals_card(callback.message, user, user_language, edit=True)
    except (ValueError, TypeError, IndexError):
        await callback.message.answer(tr('stale', user_language))
