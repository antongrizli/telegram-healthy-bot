"""Authenticated workout cards, history, progress, preferences and daily proposals."""
import logging
import re
from datetime import timedelta

from aiohttp import web
from sqlalchemy import select

from src.database.connection import AsyncSessionLocal
from src.database.models import (
    CanonicalExercise,
    DailyWorkoutPlan,
    Exercise,
    TrainingPlan,
    TrainingProfile,
    WorkoutSession,
)
from src.services import workout_progress as progress
from src.services import workout_templates as wt
from src.services import workout_today as daily
from src.services import workouts as svc
from src.services.training import library, llm, planner, sessions
from src.services.training.focus import FOCUS_GROUPS, KEYWORD_MAP, resolve_focus
from src.services.training.plan_context import load_plan_context
from src.utils.workout_locales import tr
from src.webapp.auth import validate_init_data

logger = logging.getLogger(__name__)

_WEB_TO_V2_FOCUS = {
    'legs': ['legs'],
    'back': ['back'],
    'abs': ['abs'],
    'upper': ['chest', 'back', 'arms', 'shoulders'],
    'combined': ['fullbody'],
    'custom': None,
}


def serialize_plan(plan, user):
    return {'id': plan.id, 'revision': plan.revision, 'date': plan.local_date.isoformat(), 'status': plan.status,
            'preferences': plan.preferences, 'items': plan.items, 'summary': plan.summary,
            'stale': plan.goals != svc.goals(user),
            'retry_allowed': bool(plan.status == 'running' and plan.generation_started_at
                                  and plan.generation_started_at < svc.now() - timedelta(seconds=180))} if plan else None


def _v2_focus_to_web(focus_list, stored_pref_focus=None):
    if stored_pref_focus in _WEB_TO_V2_FOCUS:
        return stored_pref_focus
    groups = set(str(g) for g in (focus_list or []))
    if not groups:
        return 'combined'
    if groups.issubset(FOCUS_GROUPS['legs']):
        return 'legs'
    if groups.issubset(FOCUS_GROUPS['back']):
        return 'back'
    if groups.issubset(FOCUS_GROUPS['abs']):
        return 'abs'
    upper_muscles = FOCUS_GROUPS['chest'] | FOCUS_GROUPS['back'] | FOCUS_GROUPS['arms'] | FOCUS_GROUPS['shoulders']
    if groups.issubset(upper_muscles):
        return 'upper'
    return 'combined'


def serialize_v2_plan(plan):
    if not plan:
        return None
    constraints = dict(plan.constraints or {})
    web_prefs = dict(constraints.get('web_preferences') or {})
    status_map = {
        'draft': 'draft',
        'proposed': 'proposed',
        'accepted': 'accepted',
        'in_progress': 'accepted',
        'done': 'accepted',
    }
    items = []
    for raw in list(plan.items or []):
        ex_id = raw.get('exercise_id')
        web_ex_id = -abs(int(ex_id)) if ex_id else 0
        raw_kind = str(raw.get('kind') or 'strength')
        web_kind = 'cardio' if raw_kind == 'cardio' else 'strength'
        working = list(raw.get('working') or [])
        sets = []
        if web_kind == 'cardio':
            dur_sec = int((working[0].get('duration_sec') if working else 0) or 480)
            sets.append({'kind': 'cardio', 'minutes': max(1, round(dur_sec / 60)), 'intensity': 'moderate'})
        else:
            for s in working:
                row = {
                    'weight': float(s['weight_kg']) if s.get('weight_kg') is not None else (0.0 if raw_kind == 'bodyweight' else None),
                    'reps': int(s['reps']) if s.get('reps') is not None else None,
                    'confirmed': False,
                }
                if raw_kind == 'bodyweight':
                    row['load_type'] = 'bodyweight'
                sets.append(row)
        items.append({
            'exercise_id': web_ex_id,
            'name': str(raw.get('name') or '?'),
            'kind': web_kind,
            'sets': sets,
        })
    energy = str(constraints.get('energy') or 'normal')
    preferences = {
        'focus': _v2_focus_to_web(plan.focus, web_prefs.get('focus')),
        'minutes': web_prefs.get('minutes') if 'minutes' in web_prefs else constraints.get('minutes', 45),
        'effort': web_prefs.get('effort') or ('easy' if energy == 'low' else 'normal'),
        'wishes': str(web_prefs.get('wishes') or ''),
    }
    return {
        'id': plan.id,
        'revision': plan.revision,
        'date': plan.local_date.isoformat(),
        'status': status_map.get(plan.status or 'proposed', 'proposed'),
        'preferences': preferences,
        'items': items,
        'summary': plan.rationale or '',
        'stale': False,
        'retry_allowed': False,
    }


async def _should_use_v2_planner(db, user_id):
    has_v2_ex = (await db.scalar(select(CanonicalExercise.id).where(CanonicalExercise.user_id == user_id).limit(1))) is not None
    has_v2_prof = (await db.scalar(select(TrainingProfile.user_id).where(TrainingProfile.user_id == user_id).limit(1))) is not None
    has_v2_plan = (await db.scalar(select(TrainingPlan.id).where(TrainingPlan.user_id == user_id).limit(1))) is not None
    has_legacy_ex = (await db.scalar(select(Exercise.id).where(Exercise.user_id == user_id).limit(1))) is not None
    return bool(has_v2_ex or has_v2_prof or has_v2_plan or not has_legacy_ex)


def _validate_v2_web_preferences(patch, base=None):
    if set(patch) - {'focus', 'minutes', 'effort', 'wishes'}:
        raise ValueError('Unknown preference')
    values = dict(base or {'focus': None, 'minutes': 45, 'effort': 'normal', 'wishes': ''})
    if 'focus' in patch:
        if patch['focus'] is not None and patch['focus'] not in daily.FOCI:
            raise ValueError('Invalid focus')
        values['focus'] = patch['focus']
    if 'minutes' in patch:
        minutes = svc.number(patch['minutes'] or 0, 0, 240, integer=True)
        if 0 < minutes < 5:
            raise ValueError('Minimum time is 5 minutes')
        values['minutes'] = minutes or None
    if 'effort' in patch:
        if patch['effort'] not in ('normal', 'easy'):
            raise ValueError('Invalid effort')
        values['effort'] = patch['effort']
    if 'wishes' in patch:
        if not isinstance(patch['wishes'], str) or len(patch['wishes']) > 1000:
            raise ValueError('Invalid wishes')
        values['wishes'] = patch['wishes'].strip()
    return values


async def _latest_v2_plan_today(db, user, statuses=None):
    stmt = select(TrainingPlan).where(
        TrainingPlan.user_id == user.telegram_id,
        TrainingPlan.local_date == daily.local_today(user),
    )
    if statuses is not None:
        stmt = stmt.where(TrainingPlan.status.in_(statuses))
    stmt = stmt.order_by(TrainingPlan.created_at.desc(), TrainingPlan.id.desc()).limit(1)
    return await db.scalar(stmt)


async def _configure_v2(db, user, revision, patch):
    prefs = _validate_v2_web_preferences(patch)
    if not prefs.get('focus') or prefs.get('effort') not in ('normal', 'easy'):
        raise ValueError('Invalid proposal preferences')
    uid = user.telegram_id
    today = daily.local_today(user)
    existing = await _latest_v2_plan_today(db, user, statuses=('draft', 'proposed', 'accepted', 'in_progress'))
    if existing and existing.status in ('accepted', 'in_progress'):
        raise svc.StaleWorkout('Daily plan is already used or expired')
    focus_groups = _WEB_TO_V2_FOCUS[prefs['focus']] or ['fullbody']
    energy = 'low' if prefs['effort'] == 'easy' else 'normal'
    constraints = {
        'minutes': prefs['minutes'] or 45,
        'energy': energy,
        'limitations': [],
        'web_preferences': prefs,
    }
    if existing is not None:
        if existing.revision != revision:
            raise svc.StaleWorkout('Old daily-plan button')
        existing.focus = focus_groups
        existing.constraints = constraints
        existing.status = 'draft'
        existing.items = []
        existing.rationale = ''
        existing.revision = revision + 1
        plan = existing
    else:
        plan = TrainingPlan(
            user_id=uid,
            local_date=today,
            status='draft',
            focus=focus_groups,
            constraints=constraints,
            items=[],
            rationale='',
            generator='rules+llm',
            revision=0,
        )
        db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return plan


async def _generate_v2(db, plan, user, revision):
    uid = user.telegram_id
    lang = user.language or 'ru'
    if plan.local_date != daily.local_today(user) or plan.status in ('accepted', 'in_progress', 'done'):
        raise svc.StaleWorkout('Daily plan is already used or expired')
    if plan.status == 'proposed' and plan.revision == revision:
        return plan
    if plan.revision != revision:
        raise svc.StaleWorkout('Old daily-plan button')

    if await library.tag_untagged(db, uid, lang=lang):
        await db.commit()
    ctx = await load_plan_context(db, uid)
    has_freq = bool(wt.training_preferences(user).get('sessions_per_week') or (ctx.profile and ctx.profile.days_per_week))
    if not has_freq:
        raise svc.StaleWorkout('Confirm current preferences first')

    constraints = dict(plan.constraints or {})
    web_prefs = dict(constraints.get('web_preferences') or {})
    focus_key = str(web_prefs.get('focus') or 'combined')
    wishes = str(web_prefs.get('wishes') or '')
    mins = int(web_prefs.get('minutes') or constraints.get('minutes') or getattr(ctx.profile, 'session_minutes', 45) or 45)
    effort = str(web_prefs.get('effort') or 'normal')
    energy = 'low' if effort == 'easy' else 'normal'

    if svc.mentions_pain(wishes):
        plan.status = 'proposed'
        plan.items = []
        plan.rationale = tr('safety', lang)
        await db.commit()
        await db.refresh(plan)
        return plan

    if not ctx.library:
        plan.status = 'proposed'
        plan.items = []
        plan.rationale = tr('empty_library', lang)
        await db.commit()
        await db.refresh(plan)
        return plan

    selected_groups = _WEB_TO_V2_FOCUS.get(focus_key)
    if focus_key == 'custom' and wishes:
        text_lower = wishes.lower()
        has_kw = any(re.search(pat, text_lower) for pats in KEYWORD_MAP.values() for pat in pats)
        if not has_kw:
            ai_groups = await llm.classify_focus(wishes, lang=lang)
            if ai_groups:
                selected_groups = ai_groups

    focus_obj = resolve_focus(selected_groups=selected_groups, text=wishes or None, minutes=mins, energy=energy)
    plan_items = planner.build_plan(
        profile=ctx.profile,
        library_exercises=ctx.library,
        history_index=ctx.history_index,
        focus=focus_obj,
        minutes=mins,
        energy=energy,
    )
    if not plan_items:
        plan_items = planner.build_fallback_plan(
            profile=ctx.profile,
            library_exercises=ctx.library,
            history_index=ctx.history_index,
            focus=focus_obj,
            minutes=mins,
            energy=energy,
        )

    if not plan_items:
        plan.status = 'proposed'
        plan.items = []
        plan.rationale = tr('empty_library', lang)
    else:
        default_rationale = tr('focus_' + focus_key, lang) if focus_key != 'custom' else wishes[:120]
        plan_items, final_rationale = await llm.polish_plan(plan_items, default_rationale, lang=lang)
        plan.focus = focus_obj.primary_muscles
        plan.items = [i.model_dump() for i in plan_items]
        plan.rationale = final_rationale
        plan.status = 'proposed'
        constraints['limitations'] = focus_obj.limitations
        constraints['minutes'] = mins
        constraints['energy'] = energy
        plan.constraints = constraints

    await db.commit()
    await db.refresh(plan)
    return plan


async def _accept_v2(db, plan, user, revision):
    uid = user.telegram_id
    if plan.local_date != daily.local_today(user):
        raise svc.StaleWorkout('The day or profile changed')
    if plan.status == 'in_progress' and plan.revision == revision + 1:
        existing_ws = await db.scalar(select(WorkoutSession).where(
            WorkoutSession.user_id == uid,
            WorkoutSession.plan_id == plan.id,
            WorkoutSession.status == 'active',
        ).order_by(WorkoutSession.id.desc()).limit(1))
        if existing_ws:
            return existing_ws
        raise svc.StaleWorkout('This plan was already started')
    if plan.revision != revision or plan.status != 'proposed' or not plan.items:
        raise svc.StaleWorkout('No proposal to accept')

    active_v2 = await sessions.get_active_session(db, uid)
    if active_v2 and any(s.completed for e in active_v2.entries for s in e.workout_sets):
        raise svc.StaleWorkout('Finish the current draft first')
    legacy_current = await svc.current(db, uid)
    if legacy_current and (await svc.entries(db, legacy_current) or legacy_current.feedback.get('template_edit')):
        raise svc.StaleWorkout('Finish the current draft first')

    ws = await sessions.start_from_plan(db, uid, plan.id)
    plan.revision = revision + 1
    await db.commit()
    await db.refresh(plan)
    return ws


async def api(request):
    from src.webapp.server import require_active_user
    uid = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        user = await require_active_user(uid, db)
        try:
            if request.method == 'GET':
                if 'id' in request.match_info:
                    from src.database.models import Workout
                    workout = await svc.owned(db, Workout, int(request.match_info['id']), uid)
                    return web.json_response(await progress.card(db, workout, draft=workout.status != 'finished'))
                page = svc.number(request.query.get('page', 0), 0, 1000000, integer=True)
                exercise_id = int(request.query['exercise_id']) if 'exercise_id' in request.query else None
                data = await progress.dashboard(db, user, page=page, exercise_id=exercise_id)
                v2_plan = await _latest_v2_plan_today(db, user, statuses=('draft', 'proposed', 'accepted', 'in_progress', 'done'))
                if v2_plan is not None:
                    data['today_plan'] = serialize_v2_plan(v2_plan)
                else:
                    plan = await db.scalar(select(DailyWorkoutPlan).where(
                        DailyWorkoutPlan.user_id == uid, DailyWorkoutPlan.local_date == daily.local_today(user)))
                    data['today_plan'] = serialize_plan(plan, user)
                return web.json_response(data)
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError('Expected object')
            kind = request.match_info.get('kind')
            if kind == 'preferences':
                if set(data) != {'sessions_per_week'}:
                    raise ValueError('Invalid preference')
                count = svc.number(data['sessions_per_week'], 1, 7, integer=True)
                await db.refresh(user, with_for_update=True)
                user.training_preferences = {**(user.training_preferences or {}), 'sessions_per_week': count}
                profile = await db.get(TrainingProfile, uid)
                if profile is not None:
                    profile.days_per_week = count
                await db.commit()
                return web.json_response({'ok': True})
            use_v2 = await _should_use_v2_planner(db, uid)
            if kind == 'configure':
                if set(data) != {'revision', 'preferences'} or not isinstance(data['preferences'], dict):
                    raise ValueError('Invalid proposal')
                if use_v2:
                    plan = await _configure_v2(db, user, int(data['revision']), data['preferences'])
                    return web.json_response(serialize_v2_plan(plan))
                plan = await daily.today(db, user)
                await daily.configure(db, plan, user, int(data['revision']), data['preferences'])
            elif kind in ('generate', 'accept'):
                plan_id = int(request.match_info['id'])
                revision = svc.number(data['revision'], 0, 1000000, integer=True)
                if use_v2:
                    v2_plan = await db.get(TrainingPlan, plan_id)
                    if v2_plan is None or v2_plan.user_id != uid:
                        raise svc.StaleWorkout('Missing resource')
                    if kind == 'generate':
                        try:
                            v2_plan = await _generate_v2(db, v2_plan, user, revision)
                        except svc.StaleWorkout:
                            raise
                        except Exception:
                            logger.exception('Web v2 workout generation failed')
                            return web.json_response({'error': 'generation_failed'}, status=503)
                        return web.json_response(serialize_v2_plan(v2_plan))
                    ws = await _accept_v2(db, v2_plan, user, revision)
                    return web.json_response({'ok': True, 'workout_id': ws.id})
                plan = await svc.owned(db, DailyWorkoutPlan, plan_id, uid)
                if kind == 'generate':
                    try:
                        await daily.generate(db, plan, user, revision, user.language)
                    except svc.StaleWorkout:
                        raise
                    except Exception:
                        logger.exception('Web workout generation failed')
                        return web.json_response({'error': 'generation_failed'}, status=503)
                else:
                    workout = await daily.accept(db, plan, user, revision, user.language)
                    return web.json_response({'ok': True, 'workout_id': workout.id})
            else:
                raise web.HTTPNotFound()
            await db.refresh(plan)
            return web.json_response(serialize_plan(plan, user), status=202 if plan.status == 'running' else 200)
        except svc.StaleWorkout as exc:
            raise web.HTTPConflict(text='workout_state_changed') from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise web.HTTPBadRequest(text='invalid_workout_input') from exc


def register_routes(app):
    app.router.add_get('/api/workouts', api)
    app.router.add_get(r'/api/workouts/history/{id:\d+}', api)
    app.router.add_post('/api/workouts/{kind:preferences|configure}', api)
    app.router.add_post(r'/api/workouts/today/{id:\d+}/{kind:generate|accept}', api)
