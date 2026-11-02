"""Authenticated workout cards, history, progress, preferences and daily proposals."""
import logging
from datetime import timedelta

from aiohttp import web
from sqlalchemy import select

from src.database.connection import AsyncSessionLocal
from src.database.models import DailyWorkoutPlan
from src.services import workout_progress as progress
from src.services import workout_today as daily
from src.services import workouts as svc
from src.webapp.auth import validate_init_data

logger = logging.getLogger(__name__)


def serialize_plan(plan, user):
    return {'id': plan.id, 'revision': plan.revision, 'date': plan.local_date.isoformat(), 'status': plan.status,
            'preferences': plan.preferences, 'items': plan.items, 'summary': plan.summary,
            'stale': plan.goals != svc.goals(user),
            'retry_allowed': bool(plan.status == 'running' and plan.generation_started_at
                                  and plan.generation_started_at < svc.now() - timedelta(seconds=180))} if plan else None


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
                await db.commit()
                return web.json_response({'ok': True})
            if kind == 'configure':
                if set(data) != {'revision', 'preferences'} or not isinstance(data['preferences'], dict):
                    raise ValueError('Invalid proposal')
                plan = await daily.today(db, user)
                await daily.configure(db, plan, user, int(data['revision']), data['preferences'])
            elif kind in ('generate', 'accept'):
                plan = await svc.owned(db, DailyWorkoutPlan, int(request.match_info['id']), uid)
                revision = svc.number(data['revision'], 0, 1000000, integer=True)
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
