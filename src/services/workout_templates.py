"""User-owned mixed templates and transparent duration estimates (not measured time)."""
import copy

from sqlalchemy import select

from src.database.models import Exercise, Workout, WorkoutEntry, WorkoutTemplate

PRIORITIES = ('maintain_strength', 'endurance', 'avoid_fatigue')
INTENSITIES = ('easy', 'moderate', 'hard')
INTENSITY_MAP = {
    'easy': 'easy', 'легко': 'easy', 'lekko': 'easy', 'leicht': 'easy', 'kolay': 'easy', 'fácil': 'easy',
    'moderate': 'moderate', 'умеренно': 'moderate', 'средне': 'moderate', 'средняя': 'moderate',
    'medium': 'moderate', 'normal': 'moderate', 'umiarkowanie': 'moderate', 'mäßig': 'moderate',
    'orta': 'moderate', 'moderado': 'moderate',
    'hard': 'hard', 'тяжело': 'hard', 'тяжёлая': 'hard', 'ciężko': 'hard', 'schwer': 'hard',
    'zor': 'hard', 'difícil': 'hard',
}
LAST_WORKOUT_TEMPLATE = '__last_completed_workout__'


def training_preferences(user):
    raw = user.training_preferences or {}
    primary = raw.get('primary') if raw.get('primary') in PRIORITIES else None
    return {'primary': primary, 'additional': [g for g in raw.get('additional', [])
                                              if g in PRIORITIES and g != primary],
            'minutes': raw.get('minutes'), **({'sessions_per_week': raw['sessions_per_week']} if raw.get('sessions_per_week') else {})}


async def templates(db, uid):
    return list((await db.scalars(select(WorkoutTemplate).where(
        WorkoutTemplate.user_id == uid).order_by(WorkoutTemplate.id))).all())


def snapshot(template):
    return {'id': template.id, 'revision': template.revision, 'name': template.name, 'items': copy.deepcopy(template.items),
            'minutes_min': template.minutes_min, 'minutes_max': template.minutes_max}


def scheduled_snapshot(template, budget):
    result = snapshot(template)
    fitted = fit_budget(result['items'], budget)
    result['budget_adjusted'] = fitted != result['items']
    result['items'] = fitted
    result['estimated_minutes'] = estimate_minutes(fitted)
    return result


async def recommended(db, uid):
    choices = await templates(db, uid)
    if not choices:
        return None
    last = await db.scalar(select(Workout).where(
        Workout.user_id == uid, Workout.status == 'finished', Workout.template_snapshot['id'].as_integer().is_not(None)
    ).order_by(Workout.finished_at.desc(), Workout.id.desc()).limit(1))
    ids = [t.id for t in choices]
    previous_id = last.template_snapshot.get('id') if last else None
    return choices[(ids.index(previous_id) + 1) % len(choices)] if previous_id in ids else choices[0]


async def import_example(db, uid):
    """Explicit opt-in, idempotent, and never updates existing exercises or templates."""
    from src.services.workouts import add_exercise
    specs = {
        'A': [('Brustpresse', 30, 30, 12), ('Latzug', 30, 35, 12),
              ('Beinbeuger sitzend', 30, 30, 12), ('Abduktion', 30, 35, 15), ('Bizepsmaschine', 10, 15, 12)],
        'B': [('Rudermaschine', 30, 35, 12), ('Brustpresse', 30, 30, 12),
              ('Beinstrecker', 30, 30, 12), ('Adduktion', 30, 30, 12), ('Latzug', 30, 35, 12)],
    }
    current = {t.name for t in await templates(db, uid) if t.name != LAST_WORKOUT_TEMPLATE}
    if len(current | set(specs)) > 20:
        raise ValueError('Maximum 20 templates')
    for name, lifts in specs.items():
        if name in current:
            continue
        cardio = await add_exercise(db, uid, 'Ellipsentrainer', kind='cardio')
        if cardio.kind != 'cardio':
            raise ValueError('Existing exercise has a different type')
        items = [{'exercise_id': cardio.id, 'kind': 'cardio', 'sets': [
            {'kind': 'cardio', 'minutes': None, 'minutes_min': 10, 'minutes_max': 15, 'intensity': 'easy'}]}]
        for label, low, high, reps in lifts:
            exercise = await add_exercise(db, uid, label)
            if exercise.kind != 'strength':
                raise ValueError('Existing exercise has a different type')
            row = {'weight': low if low == high else None, 'reps': reps, 'confirmed': False}
            if low != high:
                row.update(weight_min=low, weight_max=high)
            items.append({'exercise_id': exercise.id, 'kind': 'strength', 'sets': [dict(row), dict(row)]})
        db.add(WorkoutTemplate(user_id=uid, name=name, items=items, minutes_min=40, minutes_max=50))
    await db.flush()


async def save_template(db, workout, name):
    name = name.strip()
    if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Invalid template name')
    rows = list((await db.scalars(select(WorkoutEntry).where(
        WorkoutEntry.workout_id == workout.id, WorkoutEntry.user_id == workout.user_id
    ).order_by(WorkoutEntry.id))).all())
    if not rows or any(e.actual is None or e.actual != e.draft for e in rows):
        raise ValueError('Save all exercises first')
    existing = await db.scalar(select(WorkoutTemplate).where(
        WorkoutTemplate.user_id == workout.user_id, WorkoutTemplate.name == name))
    if existing:
        from src.services.workouts import StaleWorkout
        source = workout.template_snapshot or {}
        if existing.id != source.get('id') or existing.revision != source.get('revision'):
            raise StaleWorkout('Template changed; save under a new name')
    elif sum(t.name != LAST_WORKOUT_TEMPLATE for t in await templates(db, workout.user_id)) >= 20:
        raise ValueError('Maximum 20 templates')
    items = []
    for e in rows:
        exercise = await db.get(Exercise, e.exercise_id)
        sets = copy.deepcopy(e.actual)
        for row in sets:
            if 'confirmed' in row:
                row['confirmed'] = False
        items.append({'exercise_id': exercise.id, 'kind': exercise.kind, 'sets': sets})
    if existing:
        from sqlalchemy import update
        result = await db.execute(update(WorkoutTemplate).where(
            WorkoutTemplate.id == existing.id, WorkoutTemplate.revision == existing.revision
        ).values(items=items, minutes_max=workout.available_minutes, revision=existing.revision + 1)
                                 .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise StaleWorkout('Stale template')
        await db.refresh(existing)
        template = existing
        workout.template_snapshot = snapshot(template)
    else:
        template = WorkoutTemplate(user_id=workout.user_id, name=name, items=items,
                                   minutes_max=workout.available_minutes)
        db.add(template)
    await db.flush()
    return template


async def save_last_workout(db, workout):
    """Keep one rolling, reusable template from the user's latest completed workout."""
    rows = list((await db.scalars(select(WorkoutEntry).where(
        WorkoutEntry.workout_id == workout.id, WorkoutEntry.user_id == workout.user_id
    ).order_by(WorkoutEntry.id))).all())
    items = []
    for entry in rows:
        if not entry.actual:
            continue
        exercise = await db.get(Exercise, entry.exercise_id)
        sets = copy.deepcopy(entry.actual)
        for row in sets:
            if 'confirmed' in row:
                row['confirmed'] = False
        items.append({'exercise_id': exercise.id, 'kind': exercise.kind, 'sets': sets})
    if not items:
        return None
    template = await db.scalar(select(WorkoutTemplate).where(
        WorkoutTemplate.user_id == workout.user_id,
        WorkoutTemplate.name == LAST_WORKOUT_TEMPLATE,
    ))
    if template:
        template.items = items
        template.minutes_min = None
        template.minutes_max = workout.available_minutes
        template.revision += 1
    else:
        template = WorkoutTemplate(user_id=workout.user_id, name=LAST_WORKOUT_TEMPLATE,
                                   items=items, minutes_max=workout.available_minutes)
        db.add(template)
    await db.flush()
    return template


async def auto_save_template(db, workout, lang='ru'):
    """Auto-saves completed workout composition: updates existing composition or creates date-named template."""
    from src.utils.workout_locales import tr
    await save_last_workout(db, workout)

    rows = list((await db.scalars(select(WorkoutEntry).where(
        WorkoutEntry.workout_id == workout.id, WorkoutEntry.user_id == workout.user_id
    ).order_by(WorkoutEntry.id))).all())
    valid_entries = [e for e in rows if e.actual]
    if not valid_entries:
        return None, False

    items = []
    current_exercise_ids = []
    for entry in valid_entries:
        exercise = await db.get(Exercise, entry.exercise_id)
        sets = copy.deepcopy(entry.actual)
        for row in sets:
            if 'confirmed' in row:
                row['confirmed'] = False
        items.append({'exercise_id': exercise.id, 'kind': exercise.kind, 'sets': sets})
        current_exercise_ids.append(exercise.id)

    user_templates = [t for t in await templates(db, workout.user_id) if t.name != LAST_WORKOUT_TEMPLATE]
    matching_template = None
    for t in user_templates:
        t_exercise_ids = [i.get('exercise_id') for i in t.items]
        if t_exercise_ids == current_exercise_ids:
            matching_template = t
            break

    if matching_template:
        matching_template.items = items
        matching_template.minutes_max = workout.available_minutes
        matching_template.revision += 1
        await db.flush()
        return matching_template, False

    from datetime import datetime, UTC
    date_str = (workout.finished_at or datetime.now(UTC).replace(tzinfo=None)).strftime('%d.%m')
    template_name = tr('auto_template_date', lang, date=date_str)

    if len(user_templates) >= 20:
        return None, True

    existing_names = {t.name for t in user_templates}
    final_name = template_name
    counter = 2
    while final_name in existing_names:
        final_name = f"{template_name} ({counter})"
        counter += 1

    new_template = WorkoutTemplate(
        user_id=workout.user_id,
        name=final_name,
        items=items,
        minutes_max=workout.available_minutes,
    )
    db.add(new_template)
    await db.flush()
    return new_template, False


def estimate_minutes(items):
    """Planning heuristic: strength set incl. rest = 2 min; transition = 1 min."""
    total = 0
    for item in items:
        sets = item['sets']
        if item.get('kind') == 'cardio' or sets[0].get('kind') == 'cardio':
            total += sum(s.get('minutes') or s.get('minutes_max') or 0 for s in sets) + 1
        else:
            total += len(sets) * 2 + 1
    return round(total, 3)


def fit_budget(items, budget):
    result = copy.deepcopy(items)
    if budget is None:
        return result
    while result and estimate_minutes(result) > budget:
        multi = next((i for i in reversed(result) if i.get('kind', 'strength') == 'strength' and len(i['sets']) > 1), None)
        if multi:
            multi['sets'].pop()
            continue
        cardio = next((i for i in result if i.get('kind') == 'cardio'
                       and (i['sets'][0].get('minutes') or i['sets'][0].get('minutes_max') or 0) > 1), None)
        if cardio:
            row = cardio['sets'][0]
            if row.get('minutes') is not None:
                row['minutes'] = max(1, round(row['minutes'] - 1, 3))
            else:
                row['minutes_max'] = max(1, row['minutes_max'] - 1)
                row['minutes_min'] = min(row.get('minutes_min', 1), row['minutes_max'])
            continue
        result.pop()
    return result


async def planning_basis(db, workout, user):
    """Only completed workouts advance the template cycle; choose history per exercise."""
    next_template = await recommended(db, user.telegram_id) if (workout.template_snapshot or {}).get('id') else None
    if not next_template:
        return None
    basis = []
    for item in next_template.items:
        exercise = await db.scalar(select(Exercise).where(
            Exercise.id == item['exercise_id'], Exercise.user_id == user.telegram_id))
        if not exercise:
            raise ValueError('Missing template exercise')
        last = await db.scalar(select(WorkoutEntry).join(Workout).where(
            WorkoutEntry.user_id == user.telegram_id, WorkoutEntry.exercise_id == exercise.id,
            Workout.status == 'finished', WorkoutEntry.actual.is_not(None)
        ).order_by(Workout.finished_at.desc(), Workout.id.desc()).limit(1))
        sets = copy.deepcopy(last.actual if last else item['sets'])
        basis.append({'exercise_id': exercise.id, 'name': exercise.name, 'kind': exercise.kind,
                      'actual': sets, 'source': 'history' if last else 'user_template', 'step': exercise.weight_step})
    return snapshot(next_template), basis


def validate_template_sets(sets):
    from src.services.workouts import number, validate_sets
    if not isinstance(sets, list) or not 1 <= len(sets) <= 12:
        raise ValueError('Invalid template sets')
    if sets[0].get('kind') == 'cardio':
        row = sets[0]
        if row.get('minutes') is not None:
            return validate_sets(sets)
        if row.get('minutes_min') is None and row.get('minutes_max') is None:
            if len(sets) != 1 or (row.get('intensity') is not None and row.get('intensity') not in INTENSITIES):
                raise ValueError('Invalid cardio')
            return [{'kind': 'cardio', 'minutes': None, 'intensity': row.get('intensity')}]
        low, high = number(row.get('minutes_min'), 1, 240), number(row.get('minutes_max'), 1, 240)
        if (len(sets) != 1 or low > high
                or (row.get('intensity') is not None and row.get('intensity') not in INTENSITIES)):
            raise ValueError('Invalid cardio range')
        return [{'kind': 'cardio', 'minutes': None, 'minutes_min': low, 'minutes_max': high,
                 'intensity': row.get('intensity')}]
    result = []
    for row in sets:
        if row.get('weight') is not None:
            result.extend(validate_sets([row]))
            continue
        if row.get('weight_min') is None and row.get('weight_max') is None:
            result.append({'weight': None,
                           'reps': None if row.get('reps') is None else number(row['reps'], 1, 100, True),
                           'confirmed': False})
            continue
        low, high = number(row.get('weight_min'), 0, 1000), number(row.get('weight_max'), 0, 1000)
        if low > high:
            raise ValueError('Invalid weight range')
        result.append({'weight': None, 'weight_min': low, 'weight_max': high,
                       'reps': None if row.get('reps') is None else number(row['reps'], 1, 100, True), 'confirmed': False})
    return result
