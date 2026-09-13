"""Owned, authenticated daily actions; shared translations with Telegram."""
from datetime import datetime, UTC, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from aiohttp import web
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.webapp.auth import validate_init_data
from src.services import ux, gamification, rate_limiter
from src.utils.i18n_locales import LOCALES

BOT_KEY = web.AppKey('bot', object)

async def ux_api(request):
    from src.webapp.server import require_active_user
    user_id = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        user = await require_active_user(user_id, db)
        kind = request.match_info.get('kind', 'today')
        try:
            if request.method == 'GET':
                data = await ux.today_data(db, user)
                drafts = await crud.get_pending_meals(db, user_id)
                data['drafts'] = [dict(id=d.id, analysis=d.payload['analysis']) for d in drafts if d.request_type == 'meal_draft']
                data['settings'] = dict(timezone=user.timezone, notifications_enabled=user.notifications_enabled, **ux.preferences(user))
                data['queue'] = await crud.food_queue_status(db, user_id)
                data['labels'] = {k: v for k, v in LOCALES[user.language].items() if k.startswith(('ux_', 'btn_', 'med_', 'food_', 'err_'))}
                await crud.record_event(db, user_id, 'active')
                return web.json_response(data)
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError()
            item_id = int(request.match_info['id']) if 'id' in request.match_info else None
            if kind == 'settings':
                timezone = str(data['timezone'])
                ZoneInfo(timezone)
                if data.get('frequency') not in ('off', 'daily', 'weekly') or not isinstance(data.get('notifications_enabled'), bool):
                    raise ValueError()
                for key in ('quiet_start', 'quiet_end'):
                    parsed = time.fromisoformat(data[key])
                    if len(data[key]) != 5 or parsed.tzinfo:
                        raise ValueError()
                user = await crud.save_ux_settings(db, user_id, data)
                from src.services.scheduler import reschedule_user_jobs
                reschedule_user_jobs(request.app[BOT_KEY], user)
            elif kind == 'water':
                await crud.add_water_log(db, user_id, int(ux.numeric(data['amount'], 1, 5000)))
            elif kind == 'weight':
                await crud.save_weight_entry(db, user_id, ux.numeric(data['weight'], 20.01, 500))
                await gamification.process_weight_log_streak(db, user)
                await gamification.check_new_achievements(db, user_id)
            elif kind == 'analyze':
                description = str(data.get('description', '')).strip()
                if not 1 <= len(description) <= 4000:
                    raise ValueError()
                now = datetime.now(UTC)
                queue_id = await rate_limiter.add_to_queue(db, user_id, user_id, 'analyze_food_input',
                    dict(text_description=description, language=user.language, logged_at=now.isoformat(), meal_type=ux.infer_meal_type(user, now)))
                await crud.record_event(db, user_id, 'meal_submitted')
                return web.json_response({'id': queue_id, 'queued': True}, status=202)
            elif kind == 'drafts':
                action = data.get('action')
                if action not in ('accept', 'cancel'):
                    raise ValueError()
                saved = await crud.finish_meal_draft(db, item_id, user_id, action == 'accept')
                if not saved:
                    raise web.HTTPNotFound()
                if action == 'accept':
                    await gamification.process_food_log_streak(db, user)
                    await gamification.check_new_achievements(db, user_id)
            elif kind == 'meals':
                name = str(data.get('name', '')).strip()
                if not 1 <= len(name) <= 500:
                    raise ValueError()
                values = {k: ux.numeric(data[k], 0, 20000 if k == 'calories' else 2000) for k in ('calories', 'protein', 'fat', 'carb')}
                values['calories'] = round(values['calories'])
                items = [dict(name=name, portion='1', **values)]
                args = dict(items_json=items, calories=values['calories'], proteins=values['protein'], fats=values['fat'], carbs=values['carb'])
                if item_id:
                    if not await crud.update_food_log(db, item_id, user_id, **args):
                        raise web.HTTPNotFound()
                else:
                    await crud.add_food_log(db, user_id, **args, meal_type=ux.infer_meal_type(user, datetime.now(UTC)))
                    await gamification.process_food_log_streak(db, user)
                    await gamification.check_new_achievements(db, user_id)
                    await crud.record_event(db, user_id, 'meal_manual_saved')
            elif kind == 'events' and data.get('name') in ('meal_opened', 'report_opened'):
                await crud.record_event(db, user_id, data['name'])
            else:
                raise web.HTTPNotFound()
            return web.json_response({'ok': True})
        except (ValueError, TypeError, KeyError, ZoneInfoNotFoundError) as exc:
            raise web.HTTPBadRequest(text=LOCALES[user.language]['ux_invalid']) from exc

def register_routes(app):
    app.router.add_get('/api/today', ux_api)
    for kind in ('settings', 'water', 'weight', 'meals', 'analyze', 'events'):
        app.router.add_post('/api/ux/{kind:' + kind + '}', ux_api)
    app.router.add_patch('/api/ux/{kind:meals}/{id:\\d+}', ux_api)
    app.router.add_post('/api/ux/{kind:drafts}/{id:\\d+}', ux_api)
