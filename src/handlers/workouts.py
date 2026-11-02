"""Private-chat workout cards. All mutable state is in SQL, never only in FSM."""
import logging

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import delete, select

from src.database.connection import AsyncSessionLocal
from src.database.models import Exercise, User, Workout, WorkoutEntry, WorkoutPlan, WorkoutTemplate
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
        value = f"{minutes:g}" if minutes is not None else f"{row.get('minutes_min', '?')}–{row.get('minutes_max', '?')}?"
        return f"{value} {tr('minute_unit', lang)} · {tr(row['intensity'], lang)}"
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
        lines.append(f"{exercise.name}: {m['sets']} / {m['max_weight']:g} / {m['confirmed_volume']:g}"
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
                        parts.append(f"{label}: {c['minutes_delta']:+g} {tr('minutes', lang)}")
                        continue
                    if c['max_weight_delta'] is None:
                        value = '?' if c['known_reps_delta'] is None else f"{c['known_reps_delta']:+d}"
                        parts.append(f"{label}: {value} {tr('repetitions', lang)}")
                        continue
                    volume = '?' if c['volume_delta'] is None else f"{c['volume_delta']:+g}"
                    parts.append(f"{label}: {c['sets_delta']:+d} {tr('sets', lang)}, {c['max_weight_delta']:+g} kg, {volume} kg×{tr('repetitions', lang)}")
            if parts:
                lines.append(item['name'] + ': ' + '; '.join(parts))
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
    text += '\n\n' + data.get('summary', '')
    if workout.feedback.get('pain'):
        text += '\n' + tr('safety', lang)
    text += '\n\n' + tr('plan', lang)
    if not (svc.goals(user)['primary'] or svc.goals(user).get('training', {}).get('primary')):
        text += '\n' + tr('no_goal', lang)
    for item in plan.items:
        ex = await svc.owned(db, Exercise, item['exercise_id'], user.telegram_id)
        text += '\n' + ex.name + ': ' + '; '.join(format_set(s, lang) for s in item['sets'])
    if plan.target_template:
        text += '\n' + plan.target_template['name']
    if plan.items:
        text += '\n' + tr('estimated', lang, minutes=wt.estimate_minutes(plan.items))
        text += '\n' + tr('budget', lang, minutes=data.get('time_budget') or '?')
    text += '\n' + plan.explanation
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
    status = await message.answer(tr('busy', lang))
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
        from src.keyboards.inline import get_workout_hub_inline
        await message.answer(tr('title', user_language), reply_markup=get_workout_hub_inline(
            user_language, wt.training_preferences(user).get('sessions_per_week')))
        workout = await svc.current(db, user.telegram_id)
        if workout:
            if workout.goals != svc.goals(user):
                await message.answer(tr('changed', user_language))
            # Reopen unsaved card after restart, including pending numeric input.
            rows = await svc.entries(db, workout)
            entry = next((e for e in rows if e.draft != e.actual), None)
            if entry:
                await card(db, message, workout, entry, user_language)
                if workout.pending:
                    await message.answer(tr(workout.pending.get('mode', 'weight'), user_language))
            else:
                await library(db, message, workout, user_language)
        else:
            last = await db.scalar(select(Workout).where(Workout.user_id == user.telegram_id, Workout.status == 'finished')
                                   .order_by(Workout.id.desc()).limit(1))
            if last:
                plan = await db.scalar(select(WorkoutPlan).where(WorkoutPlan.workout_id == last.id))
                if last.analysis_state == 'done' and plan:
                    await plan_card(db, message, plan, user, user_language)
                else:
                    await feedback_card(db, message, last, user, user_language)
            else:
                await emit(message, tr('title', user_language), [[(tr('new', user_language), 'woh:choose')]])


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
                    await svc.finish(db, w)
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
    rows = [[(t.name, f'wot:details:{t.id}')] for t in choices[page * 8:(page + 1) * 8]]
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
        recommended = await wt.recommended(db, callback.from_user.id)
        text = tr('choose', user_language)
        if recommended:
            text += '\n' + tr('recommended', user_language, name=recommended.name)
        rows = [[(tr('free', user_language), 'wot:start:0')]]
        rows += [[(('★ ' if recommended and t.id == recommended.id else '') + t.name, f'wot:start:{t.id}')]
                 for t in choices[:20]]
        rows.append([(tr('templates', user_language), 'wot:list:0')])
        await emit(callback.message, text, rows, edit=True)


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
            if op == 'import' and len(parts) == 2:
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
                if op == 'edit':
                    workout.feedback = {**workout.feedback, 'template_edit': True}
                    source = await svc.owned(db, WorkoutTemplate, tid, user.telegram_id)
                    workout.template_snapshot = wt.snapshot(source)
                    for item in workout.template_snapshot['items']:
                        await svc.open_entry(db, workout, item['exercise_id'], user)
                    await db.commit()
                await library(db, callback.message, workout, lang, edit=True)
            elif op in ('details', 'delete', 'remove') and len(parts) in (3, 4):
                template = await svc.owned(db, WorkoutTemplate, int(parts[2]), user.telegram_id)
                if op == 'details':
                    text = template.name
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
