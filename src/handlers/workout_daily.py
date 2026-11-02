"""Daily plan preferences, generation and explicit acceptance."""
import logging

from aiogram import F, Router
from aiogram.filters import BaseFilter, StateFilter
from sqlalchemy import select

from src.database.connection import AsyncSessionLocal
from src.database.models import DailyWorkoutPlan, Exercise, User
from src.handlers.workouts import emit, format_set, library
from src.services import workout_templates as wt
from src.services import workout_today as daily
from src.services import workouts as svc
from src.utils.workout_locales import tr

router = Router()
logger = logging.getLogger(__name__)


def dbtn(plan, op, value, label):
    return label, f'wd:{plan.id}:{plan.revision}:{op}:{value}'


async def panel(db, message, plan, user, lang, edit=False):
    text = tr('today_plan', lang) + ' · ' + plan.local_date.isoformat()
    p, rows = plan.preferences, []
    if plan.goals != svc.goals(user) and plan.status != 'running':
        text += '\n' + tr('changed', lang)
        if plan.status != 'accepted':
            await emit(message, text, [[dbtn(plan, 'edit', '0', tr('edit', lang))],
                                       [(tr('back', lang), 'woh:choose')]], edit)
            return
    if not wt.training_preferences(user).get('sessions_per_week'):
        text += '\n' + tr('frequency_prompt', lang)
        rows = [[dbtn(plan, 'frequency', n, str(n)) for n in range(1, 5)],
                [dbtn(plan, 'frequency', n, str(n)) for n in range(5, 8)]]
    elif plan.status == 'running':
        text += '\n' + tr('busy', lang)
        rows.append([dbtn(plan, 'generate', '0', tr('analyze', lang))])
    elif plan.status in ('proposed', 'accepted'):
        text += '\n' + tr('plan_accepted' if plan.status == 'accepted' else 'proposal', lang) + '\n' + plan.summary
        for item in plan.items:
            exercise = await svc.owned(db, Exercise,
                                       item['exercise_id'], user.telegram_id)
            text += '\n' + exercise.name + ': ' + '; '.join(format_set(s, lang) for s in item['sets'])
        if plan.items:
            text += '\n' + tr('estimated', lang, minutes=wt.estimate_minutes(plan.items))
        if plan.status == 'proposed':
            if plan.items:
                rows.append([dbtn(plan, 'accept', '0', tr('accept', lang))])
            rows.append([dbtn(plan, 'edit', '0', tr('edit', lang))])
    elif not p.get('focus') or (p.get('focus') == 'custom' and not p.get('wishes')):
        text += '\n' + tr('focus_prompt', lang)
        rows += [[dbtn(plan, 'focus', f, tr('focus_' + f, lang)) for f in daily.FOCI[i:i + 2]] for i in range(0, 6, 2)]
    elif not p.get('minutes_confirmed'):
        text += '\n' + tr('budget_prompt', lang)
        values = sorted({20, 30, 40, 50, 60, *([p['minutes']] if p.get('minutes') else [])})
        rows += [[dbtn(plan, 'minutes', n, str(n)) for n in values[i:i + 3]] for i in range(0, len(values), 3)]
        rows.append([dbtn(plan, 'inputminutes', '0', tr('custom_value', lang))])
    elif p.get('effort') is None:
        text += '\n' + tr('wish', lang)
        rows.append([dbtn(plan, 'effort', 'normal', tr('normal', lang)), dbtn(plan, 'effort', 'easy', tr('lighter', lang))])
    else:
        text += '\n' + tr('focus_' + p['focus'], lang) + '\n' + tr('budget', lang, minutes=p.get('minutes') or '?')
        if p.get('wishes'):
            text += '\n' + p['wishes']
        if plan.status == 'failed':
            text += '\n' + tr('generation_error', lang)
        rows.append([dbtn(plan, 'generate', '0', tr('generate', lang))])
        rows.append([dbtn(plan, 'inputwishes', '0', tr('comment', lang))])
    rows.append([(tr('back', lang), 'woh:choose')])
    await emit(message, text, rows, edit)


@router.callback_query(F.data.startswith('wf:'))
async def frequency(callback, state, user_language):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    await state.clear()
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.telegram_id == callback.from_user.id).with_for_update())
        value = callback.data[3:]
        if value != 'open':
            try:
                value = svc.number(value, 1, 7, integer=True)
            except ValueError:
                await callback.message.answer(tr('invalid', user_language))
                return
            user.training_preferences = {**(user.training_preferences or {}), 'sessions_per_week': value}
            await db.commit()
        selected = wt.training_preferences(user).get('sessions_per_week')
        rows = [[(('✓ ' if selected == n else '') + str(n), f'wf:{n}') for n in range(1, 5)],
                [(('✓ ' if selected == n else '') + str(n), f'wf:{n}') for n in range(5, 8)],
                [(tr('back', user_language), 'woh:choose')]]
        await emit(callback.message, tr('frequency_prompt', user_language), rows, edit=True)


@router.callback_query(F.data.startswith('wd:'))
async def daily_action(callback, state, user_language):
    await callback.answer()
    if not callback.message or callback.message.chat.id != callback.from_user.id:
        return
    await state.clear()
    lang = user_language
    try:
        async with AsyncSessionLocal() as db:
            user = await db.get(User, callback.from_user.id)
            user.ux_preferences = {**(user.ux_preferences or {}), 'daily_workout_input': None}
            if callback.data == 'wd:open':
                plan = await daily.today(db, user)
                await panel(db, callback.message, plan, user, lang, edit=True)
                return
            _, pid, rev, op, value = callback.data.split(':')
            plan = await svc.owned(db, DailyWorkoutPlan, int(pid), user.telegram_id)
            rev = int(rev)
            if plan.revision != rev:
                raise svc.StaleWorkout()
            if op == 'frequency':
                count = svc.number(value, 1, 7, integer=True)
                await db.refresh(user, with_for_update=True)
                user.training_preferences = {**(user.training_preferences or {}), 'sessions_per_week': count}
                await daily.configure(db, plan, user, rev, {'focus': None})
            elif op in ('focus', 'minutes', 'effort'):
                await daily.configure(db, plan, user, rev, {op: value})
                if op == 'focus' and value == 'custom':
                    user.ux_preferences = {**user.ux_preferences, 'daily_workout_input': {'id': plan.id, 'revision': plan.revision, 'field': 'wishes'}}
                    await db.commit()
                    await emit(callback.message, tr('wishes_prompt', lang), [[dbtn(plan, 'open', '0', tr('back', lang))]], edit=True)
                    return
            elif op in ('inputminutes', 'inputwishes'):
                if plan.status == 'accepted':
                    raise svc.StaleWorkout()
                field = 'minutes' if op == 'inputminutes' else 'wishes'
                user.ux_preferences = {**user.ux_preferences, 'daily_workout_input': {'id': plan.id, 'revision': rev, 'field': field}}
                await db.commit()
                await emit(callback.message, tr('budget_prompt' if field == 'minutes' else 'wishes_prompt', lang),
                           [[dbtn(plan, 'open', '0', tr('back', lang))]], edit=True)
                return
            elif op == 'generate':
                status = await callback.message.answer(tr('busy', lang))
                try:
                    await daily.generate(db, plan, user, rev, lang)
                except svc.StaleWorkout:
                    await status.edit_text(tr('stale', lang))
                    return
                except Exception:
                    logger.exception('Daily workout generation failed')
                    await db.refresh(user)
                await db.refresh(plan)
                await panel(db, status, plan, user, lang, edit=True)
                return
            elif op == 'accept':
                workout = await daily.accept(db, plan, user, rev, lang)
                await library(db, callback.message, workout, lang, edit=True)
                return
            elif op == 'edit':
                await daily.configure(db, plan, user, rev, {'focus': None})
            elif op != 'open':
                raise svc.StaleWorkout()
            await panel(db, callback.message, plan, user, lang, edit=True)
    except (ValueError, TypeError, KeyError):
        await callback.message.answer(tr('stale', lang))


class DailyInput(BaseFilter):
    async def __call__(self, message):
        if not message.text or message.text.startswith('/') or message.chat.id != message.from_user.id:
            return False
        async with AsyncSessionLocal() as db:
            user = await db.get(User, message.from_user.id)
            value = (user.ux_preferences or {}).get('daily_workout_input') if user else None
            return {'daily_input': value} if value else False


@router.message(StateFilter(None), DailyInput())
async def daily_input(message, daily_input, user_language):
    try:
        async with AsyncSessionLocal() as db:
            user = await db.get(User, message.from_user.id)
            plan = await svc.owned(db, DailyWorkoutPlan, daily_input['id'], user.telegram_id)
            await daily.configure(db, plan, user, daily_input['revision'], {daily_input['field']: message.text})
            user.ux_preferences = {**user.ux_preferences, 'daily_workout_input': None}
            await db.commit()
            await panel(db, message, plan, user, user_language)
    except (ValueError, TypeError, KeyError):
        await message.answer(tr('invalid', user_language))
