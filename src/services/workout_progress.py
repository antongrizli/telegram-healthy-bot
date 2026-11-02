"""Owned workout history, per-exercise progress and shared achievement awards."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.database.models import Achievement, Exercise, User, Workout, WorkoutEntry
from src.services import workout_templates as wt
from src.services import workouts as svc
from src.utils.workout_locales import COPY, LANGS, tr

MEDALS = {'workout_first': ('🥇', 'medal_first', 1), 'workout_10': ('🥉', 'medal_10', 10),
          'workout_25': ('🥈', 'medal_25', 25), 'workout_4weeks': ('🏅', 'medal_weeks', 4)}


def install_achievements(registry, locales):
    for key, (icon, text_key, _) in MEDALS.items():
        registry[key] = {'icon': icon, 'name_key': key + '_name', 'desc_key': key + '_desc'}
        for lang in LANGS:
            locales[lang][key + '_name'] = tr(text_key, lang)
            locales[lang][key + '_desc'] = tr('medal_desc', lang)


async def dates(db, user):
    stamps = (await db.scalars(select(Workout.finished_at).where(
        Workout.user_id == user.telegram_id, Workout.status == 'finished', Workout.finished_at.is_not(None)))).all()
    zone = ZoneInfo(user.timezone or 'UTC')
    return stamps, {d.replace(tzinfo=UTC).astimezone(zone).date() for d in stamps}


async def award(db, user):
    await db.scalar(select(User).where(User.telegram_id == user.telegram_id).with_for_update())
    _, days = await dates(db, user)
    weeks = {d - timedelta(days=d.weekday()) for d in days}
    unlocked = set((await db.scalars(select(Achievement.achievement_key).where(
        Achievement.user_id == user.telegram_id, Achievement.achievement_key.in_(MEDALS)))).all())
    for key, (_, _, threshold) in MEDALS.items():
        count = len(weeks) if key == 'workout_4weeks' else len(days)
        if count >= threshold and key not in unlocked:
            db.add(Achievement(user_id=user.telegram_id, achievement_key=key))
    await db.commit()


def serialize_entry(entry, exercise, draft=False):
    values = entry.draft if draft else entry.actual
    return {'id': entry.id, 'exercise_id': exercise.id, 'name': exercise.name, 'kind': exercise.kind,
            'load_type': exercise.load_type, 'sets': values, 'saved': entry.actual is not None,
            'unsaved_changes': entry.actual != entry.draft,
            'metrics': svc.metrics(values) if values and not draft else None}


async def card(db, workout, draft=False):
    rows = (await db.execute(select(WorkoutEntry, Exercise).join(Exercise).where(
        WorkoutEntry.workout_id == workout.id, WorkoutEntry.user_id == workout.user_id,
        Exercise.user_id == workout.user_id).order_by(WorkoutEntry.id))).all()
    return {'id': workout.id, 'status': workout.status, 'started_at': workout.started_at.isoformat() + 'Z',
            'finished_at': workout.finished_at.isoformat() + 'Z' if workout.finished_at else None,
            'name': (workout.template_snapshot or {}).get('name'), 'available_minutes': workout.available_minutes,
            'feedback': workout.feedback, 'analysis': workout.analysis, 'schedule': (workout.template_snapshot or {}).get('items', []),
            'entries': [serialize_entry(e, ex, draft) for e, ex in rows if draft or e.actual]}


async def dashboard(db, user, page=0, exercise_id=None):
    await award(db, user)
    uid, zone = user.telegram_id, ZoneInfo(user.timezone or 'UTC')
    stamps, days = await dates(db, user)
    today = datetime.now(UTC).astimezone(zone).date()
    monday = today - timedelta(days=today.weekday())
    weeks = {d - timedelta(days=d.weekday()) for d in days}
    counts = [{'date': (monday - timedelta(weeks=i)).isoformat(),
               'count': sum(monday - timedelta(weeks=i) <= d < monday - timedelta(weeks=i) + timedelta(days=7) for d in days)}
              for i in reversed(range(8))]
    page = max(0, min(page, max(0, (len(stamps) - 1) // 5)))
    history = (await db.scalars(select(Workout).where(Workout.user_id == uid, Workout.status == 'finished')
                               .order_by(Workout.finished_at.desc(), Workout.id.desc()).offset(page * 5).limit(5))).all()
    active = await svc.current(db, uid)
    library = (await db.scalars(select(Exercise).where(Exercise.user_id == uid).order_by(Exercise.id))).all()
    progress = []
    if exercise_id is not None:
        await svc.owned(db, Exercise, exercise_id, uid)
        rows = (await db.execute(select(WorkoutEntry, Workout.finished_at).join(Workout).where(
            WorkoutEntry.user_id == uid, WorkoutEntry.exercise_id == exercise_id,
            Workout.status == 'finished', WorkoutEntry.actual.is_not(None)
        ).order_by(Workout.finished_at.desc(), Workout.id.desc()).limit(60))).all()
        progress = [{'date': date.isoformat() + 'Z', 'metrics': svc.metrics(e.actual)} for e, date in reversed(rows)]
    owned = {a.achievement_key: a.unlocked_at for a in (await db.scalars(select(Achievement).where(
        Achievement.user_id == uid, Achievement.achievement_key.in_(MEDALS)))).all()}
    return {'timezone': str(zone), 'language': user.language,
            'labels': {k: COPY[k][LANGS.index(user.language) if user.language in LANGS else 0] for k in COPY},
            'preferences': wt.training_preferences(user), 'page': page, 'pages': max(1, (len(stamps) + 4) // 5),
            'totals': {'sessions': len(stamps), 'days': len(days), 'weeks': len(weeks),
                       'this_week': sum(monday <= d.replace(tzinfo=UTC).astimezone(zone).date() <= today for d in stamps),
                       'this_week_days': sum(monday <= d <= today for d in days)},
            'weekly': counts, 'history': [await card(db, w) for w in history],
            'active': await card(db, active, draft=True) if active else None,
            'library': [{'id': e.id, 'name': e.name, 'kind': e.kind, 'load_type': e.load_type} for e in library],
            'progress': progress,
            'medals': [{'key': k, 'icon': icon, 'name': tr(name, user.language), 'description': tr('medal_desc', user.language),
                        'unlocked': k in owned, 'unlocked_at': owned[k].isoformat() + 'Z' if k in owned else None,
                        'progress': min(len(weeks) if k == 'workout_4weeks' else len(days), threshold), 'target': threshold}
                       for k, (icon, name, threshold) in MEDALS.items()]}
