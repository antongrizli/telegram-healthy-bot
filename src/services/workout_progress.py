"""Owned workout history, per-exercise progress and shared achievement awards."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from src.database.models import (
    Achievement,
    CanonicalExercise,
    Exercise,
    TrainingProfile,
    User,
    Workout,
    WorkoutEntry,
    WorkoutSession,
    WorkoutSessionEntry,
)
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
    legacy_stamps = (await db.scalars(select(Workout.finished_at).where(
        Workout.user_id == user.telegram_id, Workout.status == 'finished', Workout.finished_at.is_not(None)))).all()
    v2_stamps = (await db.scalars(select(func.coalesce(WorkoutSession.finished_at, WorkoutSession.performed_at)).where(
        WorkoutSession.user_id == user.telegram_id, WorkoutSession.status == 'finished'))).all()
    stamps = [d for d in (*legacy_stamps, *v2_stamps) if d is not None]
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


def _serialize_v2_sets(entry: WorkoutSessionEntry, ex_kind: str, draft: bool = False):
    raw_sets = sorted(entry.workout_sets, key=lambda s: s.idx)
    if not draft:
        done_sets = [s for s in raw_sets if s.completed]
        if done_sets:
            raw_sets = done_sets
    out = []
    if ex_kind == 'cardio':
        for s in raw_sets:
            mins = round(s.duration_sec / 60.0, 1) if s.duration_sec else None
            out.append({'kind': 'cardio', 'minutes': mins, 'intensity': None})
        if not out and entry.duration_sec:
            out.append({'kind': 'cardio', 'minutes': round(entry.duration_sec / 60.0, 1), 'intensity': None})
    elif ex_kind == 'bodyweight':
        for s in raw_sets:
            w = float(s.added_weight_kg) if s.added_weight_kg else None
            out.append({'load_type': 'bodyweight', 'weight': w, 'reps': s.reps, 'confirmed': bool(s.completed)})
        if not out and entry.sets:
            for _ in range(entry.sets):
                out.append({'load_type': 'bodyweight', 'weight': None, 'reps': entry.reps, 'confirmed': not draft})
    else:
        for s in raw_sets:
            w = float(s.weight_kg) if s.weight_kg is not None else None
            out.append({'weight': w, 'reps': s.reps, 'confirmed': bool(s.completed)})
        if not out and entry.sets:
            w = float(entry.weight_kg) if entry.weight_kg is not None else None
            for _ in range(entry.sets):
                out.append({'weight': w, 'reps': entry.reps, 'confirmed': not draft})
    return out


def serialize_v2_entry(entry: WorkoutSessionEntry, draft: bool = False):
    ce = entry.canonical_exercise
    ex_kind = ce.kind if ce else 'strength'
    web_kind = 'cardio' if ex_kind == 'cardio' else 'strength'
    load_type = 'bodyweight' if ex_kind == 'bodyweight' else 'weights'
    values = _serialize_v2_sets(entry, ex_kind, draft=draft)
    return {
        'id': entry.id,
        'exercise_id': -(ce.id) if ce else -entry.id,
        'name': ce.display_name if ce else entry.raw_input,
        'kind': web_kind,
        'load_type': load_type,
        'sets': values,
        'saved': not draft,
        'unsaved_changes': False,
        'metrics': svc.metrics(values) if values and not draft else None,
    }


def serialize_v2_session(session: WorkoutSession, draft: bool = False):
    started = session.started_at or session.performed_at
    finished = session.finished_at or (session.performed_at if session.status == 'finished' else None)
    entries = sorted(session.entries, key=lambda e: e.position)
    return {
        'id': session.id,
        'status': session.status,
        'started_at': started.isoformat() + 'Z' if started else None,
        'finished_at': finished.isoformat() + 'Z' if finished else None,
        'name': ', '.join(session.focus[:2]) if session.focus else None,
        'available_minutes': None,
        'feedback': {'effort': session.feeling} if session.feeling else None,
        'analysis': {'summary': session.note} if session.note else None,
        'schedule': [],
        'entries': [serialize_v2_entry(e, draft=draft) for e in entries],
    }


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
    legacy_history = (await db.scalars(select(Workout).where(Workout.user_id == uid, Workout.status == 'finished')
                                      .order_by(Workout.finished_at.desc(), Workout.id.desc())
                                      .limit((page + 1) * 5))).all()
    v2_history = (await db.scalars(
        select(WorkoutSession)
        .options(
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
            selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
        )
        .where(WorkoutSession.user_id == uid, WorkoutSession.status == 'finished')
        .order_by(func.coalesce(WorkoutSession.finished_at, WorkoutSession.performed_at).desc(), WorkoutSession.id.desc())
        .limit((page + 1) * 5)
    )).all()

    merged_history = []
    for w in legacy_history:
        ts = w.finished_at or w.started_at
        merged_history.append((ts, w.id, 0, await card(db, w)))
    for s in v2_history:
        ts = s.finished_at or s.performed_at or s.started_at
        merged_history.append((ts, s.id, 1, serialize_v2_session(s)))
    merged_history.sort(key=lambda item: (item[0] or datetime.min, item[1], item[2]), reverse=True)
    history_cards = [item[3] for item in merged_history[page * 5:(page + 1) * 5]]

    active = await svc.current(db, uid)
    active_card = await card(db, active, draft=True) if active else None
    if active_card is None:
        v2_active = (await db.scalars(
            select(WorkoutSession)
            .options(
                selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.workout_sets),
                selectinload(WorkoutSession.entries).selectinload(WorkoutSessionEntry.canonical_exercise),
            )
            .where(WorkoutSession.user_id == uid, WorkoutSession.status.in_(('active', 'draft')))
            .order_by(WorkoutSession.id.desc())
            .limit(1)
        )).first()
        if v2_active:
            active_card = serialize_v2_session(v2_active, draft=True)

    library = (await db.scalars(select(Exercise).where(Exercise.user_id == uid).order_by(Exercise.id))).all()
    v2_library = (await db.scalars(
        select(CanonicalExercise)
        .where(CanonicalExercise.user_id == uid, CanonicalExercise.is_hidden == False)
        .order_by(CanonicalExercise.display_name.asc())
    )).all()

    progress = []
    if exercise_id is not None:
        if exercise_id >= 0:
            await svc.owned(db, Exercise, exercise_id, uid)
            rows = (await db.execute(select(WorkoutEntry, Workout.finished_at).join(Workout).where(
                WorkoutEntry.user_id == uid, WorkoutEntry.exercise_id == exercise_id,
                Workout.status == 'finished', WorkoutEntry.actual.is_not(None)
            ).order_by(Workout.finished_at.desc(), Workout.id.desc()).limit(60))).all()
            progress = [{'date': date.isoformat() + 'Z', 'metrics': svc.metrics(e.actual)} for e, date in reversed(rows)]
        else:
            ce = await db.get(CanonicalExercise, -exercise_id)
            if ce is None or ce.user_id != uid:
                raise svc.StaleWorkout('Missing resource')
            v2_rows = (await db.scalars(
                select(WorkoutSessionEntry)
                .join(WorkoutSession, WorkoutSessionEntry.session_id == WorkoutSession.id)
                .options(selectinload(WorkoutSessionEntry.workout_sets), selectinload(WorkoutSessionEntry.session))
                .where(
                    WorkoutSession.user_id == uid,
                    WorkoutSession.status == 'finished',
                    WorkoutSessionEntry.canonical_exercise_id == ce.id,
                )
                .order_by(func.coalesce(WorkoutSession.finished_at, WorkoutSession.performed_at).desc(), WorkoutSession.id.desc())
                .limit(60)
            )).all()
            for entry in reversed(v2_rows):
                serialized = _serialize_v2_sets(entry, ce.kind, draft=False)
                if not serialized:
                    continue
                dt = entry.session.finished_at or entry.session.performed_at
                if dt:
                    progress.append({'date': dt.isoformat() + 'Z', 'metrics': svc.metrics(serialized)})

    owned = {a.achievement_key: a.unlocked_at for a in (await db.scalars(select(Achievement).where(
        Achievement.user_id == uid, Achievement.achievement_key.in_(MEDALS)))).all()}
    library_items = [{'id': e.id, 'name': e.name, 'kind': e.kind, 'load_type': e.load_type} for e in library]
    library_items.extend(
        {
            'id': -ce.id,
            'name': ce.display_name,
            'kind': 'cardio' if ce.kind == 'cardio' else 'strength',
            'load_type': 'bodyweight' if ce.kind == 'bodyweight' else 'weights',
        }
        for ce in v2_library
    )
    preferences = wt.training_preferences(user)
    profile = await db.get(TrainingProfile, uid)
    if profile is not None:
        if not preferences.get('sessions_per_week') and profile.days_per_week:
            preferences['sessions_per_week'] = int(profile.days_per_week)
        if not preferences.get('minutes') and profile.session_minutes:
            preferences['minutes'] = int(profile.session_minutes)

    return {'timezone': str(zone), 'language': user.language,
            'labels': {k: COPY[k][LANGS.index(user.language) if user.language in LANGS else 0] for k in COPY},
            'preferences': preferences, 'page': page, 'pages': max(1, (len(stamps) + 4) // 5),
            'totals': {'sessions': len(stamps), 'days': len(days), 'weeks': len(weeks),
                       'this_week': sum(monday <= d.replace(tzinfo=UTC).astimezone(zone).date() <= today for d in stamps),
                       'this_week_days': sum(monday <= d <= today for d in days)},
            'weekly': counts, 'history': history_cards,
            'active': active_card,
            'library': library_items,
            'progress': progress,
            'medals': [{'key': k, 'icon': icon, 'name': tr(name, user.language), 'description': tr('medal_desc', user.language),
                        'unlocked': k in owned, 'unlocked_at': owned[k].isoformat() + 'Z' if k in owned else None,
                        'progress': min(len(weeks) if k == 'workout_4weeks' else len(days), threshold), 'target': threshold}
                       for k, (icon, name, threshold) in MEDALS.items()]}
