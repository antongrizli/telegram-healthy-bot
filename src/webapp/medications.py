"""Authenticated medication API; database operations live in crud.py."""
import base64
import binascii
from datetime import datetime, timedelta, UTC
from aiohttp import web
from src.webapp.auth import validate_init_data
from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.services import medications as meds, rate_limiter


def serialize_reminder(r):
    return dict(id=r.id, medication_id=r.medication_id, weekdays=r.weekdays,
                time=r.reminder_time.strftime('%H:%M'), dose=r.dose,
                start_date=r.start_date.isoformat(), end_date=r.end_date.isoformat() if r.end_date else None)


async def medication_api(request):
    from src.webapp.server import require_active_user
    user_id = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        user = await require_active_user(user_id, db)
        now = datetime.now(UTC)
        today = now.astimezone(meds.user_zone(user)).date()
        kind = request.match_info.get('kind', '')
        try:
            item_id = int(request.match_info['id']) if 'id' in request.match_info else None
            if request.method == 'GET' and kind == 'photos':
                item = await crud.get_medication_photo_request(db, user_id, item_id)
                if not item:
                    raise web.HTTPNotFound()
                return web.json_response(dict(id=item.id, status=item.status, result=item.payload.get('result')))
            if request.method == 'GET':
                reminders = await meds.materialize_intakes(db, user, now)
                start = datetime.combine(today - timedelta(days=29), datetime.min.time(), tzinfo=meds.user_zone(user)).astimezone(UTC).replace(tzinfo=None)
                rows = await crud.get_medication_intakes(db, user_id, start, now.replace(tzinfo=None))
                counts = {s: sum(r.status == s for r in rows) for s in ('taken', 'skipped', 'unmarked')}
                counts['total'] = len(rows)
                counts['adherence_percent'] = round(100 * counts['taken'] / len(rows)) if rows else None
                daily = {}
                for offset in range(29, -1, -1):
                    daily[(today - timedelta(days=offset)).isoformat()] = dict(taken=0, skipped=0, unmarked=0)
                for r in rows:
                    if r.local_date.isoformat() in daily:
                        daily[r.local_date.isoformat()][r.status] += 1
                return web.json_response(dict(timezone=user.timezone, notifications_enabled=user.notifications_enabled,
                    medications=[dict(id=m.id, name=m.name, category=m.category, details=m.details)
                                 for m in await crud.list_medications(db, user_id)],
                    reminders=[serialize_reminder(r) for r in reminders], statistics=counts, daily=daily,
                    intakes=[dict(id=r.id, name=r.reminder.medication.name, dose=r.reminder.dose,
                        scheduled_at=r.scheduled_at.replace(tzinfo=UTC).isoformat(), status=r.status,
                        delivery_status=r.delivery_status) for r in rows]))
            if request.method == 'DELETE':
                if kind not in ('items', 'reminders'):
                    raise web.HTTPNotFound()
                saved = await crud.delete_medication_item(db, user_id, item_id, reminder=kind == 'reminders')
                if not saved:
                    raise web.HTTPNotFound()
                return web.json_response({'ok': True})
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError('Invalid object')
            if kind == 'items':
                item = await crud.save_medication(db, user_id, meds.medication_values(data), item_id)
            elif kind == 'reminders':
                # Preserve already due occurrences before changing the future schedule.
                await meds.materialize_intakes(db, user, now)
                item = await crud.save_medication_reminder(db, user_id, meds.reminder_values(data, today), item_id)
            elif kind == 'intakes' and request.method == 'PATCH':
                if not await crud.mark_medication_intake(db, user_id, item_id, data.get('status')):
                    raise web.HTTPNotFound()
                return web.json_response({'ok': True})
            elif kind == 'photos' and request.method == 'POST':
                mime = data.get('mime_type')
                if mime not in ('image/jpeg', 'image/png', 'image/webp'):
                    raise ValueError('Use JPEG, PNG or WebP')
                raw = base64.b64decode(data.get('image', ''), validate=True)
                if not 1 <= len(raw) <= 4 * 1024 * 1024:
                    raise ValueError('Image must be smaller than 4 MB')
                # Always queue OCR: the existing worker applies global limits and durable retries.
                queue_id = await rate_limiter.add_to_queue(db, user_id, user_id, 'medication_photo',
                    dict(image=data['image'], mime_type=mime))
                return web.json_response({'id': queue_id, 'status': 'pending'}, status=202)
            else:
                raise web.HTTPNotFound()
            if item is None:
                raise web.HTTPNotFound()
            return web.json_response({'id': item.id}, status=200 if item_id else 201)
        except (ValueError, TypeError, KeyError, binascii.Error) as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc


def register_routes(app):
    app.router.add_get('/api/medications', medication_api)
    for kind in ('items', 'reminders'):
        app.router.add_post('/api/medications/{kind:' + kind + '}', medication_api)
        app.router.add_patch('/api/medications/{kind:' + kind + '}/{id:\\d+}', medication_api)
        app.router.add_delete('/api/medications/{kind:' + kind + '}/{id:\\d+}', medication_api)
    app.router.add_patch('/api/medications/{kind:intakes}/{id:\\d+}', medication_api)
    app.router.add_post('/api/medications/{kind:photos}', medication_api)
    app.router.add_get('/api/medications/{kind:photos}/{id:\\d+}', medication_api)
