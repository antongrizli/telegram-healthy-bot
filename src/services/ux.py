"""Shared daily summary, safe numeric input and coaching preferences."""
import copy
import math
from datetime import datetime, UTC, timedelta, time
from zoneinfo import ZoneInfo
from src.database import crud
from src.utils.i18n_locales import get_text

def is_food_entry(message):
    from src.utils.i18n_locales import LOCALES
    text = (message.text or '').strip()
    labels = {value for locale in LOCALES.values() for key, value in locale.items()
              if key.startswith(('btn_', 'ux_', 'meal_type_'))}
    legacy_prefixes = ('/', '❌', '⬅', '👑', '✅', '🔙', '✏', '🗑', '📥', '⚙', 'ℹ', '⚖', '📝', '📅', '🗓')
    return bool(message.photo or (text and text not in labels and not text.startswith(legacy_prefixes)))

def zone(user):
    try:
        return ZoneInfo(user.timezone or 'UTC')
    except (ValueError, KeyError):
        return ZoneInfo('UTC')

def numeric(value, minimum, maximum):
    result = float(str(value).strip().replace(',', '.'))
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError('Out of range')
    return result

def infer_meal_type(user, when):
    hour = when.astimezone(zone(user)).hour
    return 'breakfast' if 5 <= hour < 11 else 'lunch' if 11 <= hour < 16 else 'dinner' if 16 <= hour < 23 else 'snack'

def preferences(user):
    return {'frequency': 'daily', 'quiet_start': '22:00', 'quiet_end': '08:00', **(user.ux_preferences or {})}

def coaching_allowed(user, kind='daily', now=None):
    if user.is_blocked or not user.notifications_enabled:
        return False
    prefs = preferences(user)
    if prefs['frequency'] == 'off' or (prefs['frequency'] == 'weekly' and kind != 'weekly'):
        return False
    current = (now or datetime.now(UTC)).astimezone(zone(user)).time().replace(tzinfo=None)
    start, end = time.fromisoformat(prefs['quiet_start']), time.fromisoformat(prefs['quiet_end'])
    quiet = start <= current < end if start < end else (current >= start or current < end) if start > end else False
    return not quiet

async def today_data(db, user, now=None):
    now = now or datetime.now(UTC)
    local = now.astimezone(zone(user))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).replace(tzinfo=None)
    end = now.astimezone(UTC).replace(tzinfo=None)
    meals = await crud.get_food_logs(db, user.telegram_id, start, end)
    values = dict(date=local.date().isoformat(), zone=str(zone(user)), count=len(meals),
        cal=round(sum(m.calories for m in meals)), target=user.target_calories,
        protein=round(sum(m.proteins for m in meals)), protein_target=user.target_protein,
        fat=round(sum(m.fats for m in meals)), carb=round(sum(m.carbs for m in meals)),
        water=await crud.water_total(db, user.telegram_id, start, end))
    text = get_text('ux_summary', user.language, **values)
    text += '\n' + get_text('ux_remaining', user.language, remaining=max(0, values['target'] - values['cal']))
    text += '\n\n' + next_action(user, values)
    return dict(summary=text, values=values, meals=[dict(id=m.id, name=' · '.join(i.get('name', '') for i in m.items_json),
        calories=m.calories, protein=m.proteins, fat=m.fats, carb=m.carbs, meal_type=m.meal_type,
        time=m.logged_at.replace(tzinfo=UTC).astimezone(zone(user)).strftime('%H:%M')) for m in meals])

def next_action(user, values, days=None):
    """One transparent coaching action from logged data, never treating gaps as fasting."""
    if not values['count']:
        return get_text('ux_empty', user.language)
    if days and values['count'] < days * 0.5:
        return get_text('ux_tip_missing_days', user.language, count=values['count'])
    cal_ratio = values['cal'] / max(1, user.target_calories)
    protein_ratio = values['protein'] / max(1, user.target_protein)
    params = dict(cal_pct=round(cal_ratio * 100), protein_pct=round(protein_ratio * 100))
    if cal_ratio >= 1.15:
        key = 'ux_tip_review'
    elif protein_ratio < 0.9 and cal_ratio - protein_ratio >= 0.2:
        key = 'ux_tip_protein'
    elif cal_ratio >= 0.8 and protein_ratio >= 0.9 and user.goal != 'maintain':
        key = 'ux_tip_weight'
        goal_key = {'lose_weight': 'goal_lose', 'gain_weight': 'goal_gain_w', 'gain_muscle': 'goal_gain_m'}.get(user.goal, 'goal_maintain')
        params['goal'] = get_text(goal_key, user.language)
    else:
        key = 'ux_tip_balanced'
    return get_text(key, user.language, **params)


def streak_text(user, streaks, lang):
    names = {'food_logging': 'btn_log_food', 'weight_logging': 'btn_log_weight',
             'calorie_target_hit': 'ux_calories', 'protein_target_hit': 'ux_protein'}
    rows = [get_text('ux_streak_row', lang, name=get_text(names.get(s.streak_type, 'btn_streak_status'), lang),
                     current=s.current_count, best=s.longest_count) for s in streaks]
    return '\n'.join([get_text('btn_streak_status', lang),
        '\n'.join(rows) if rows else get_text('ux_streak_empty', lang),
        get_text('ux_freezes', lang, count=user.streak_freezes_left)])


def adjust_locally(analysis, action, value):
    result = copy.deepcopy(analysis)
    if action == 'portion':
        factor = numeric(value, 0.05, 20)
        for item in result['food_items']:
            for key in ('calories', 'protein', 'fat', 'carb'):
                item[key] = round(item[key] * factor, 1)
            item['calories'] = round(item['calories'])
            item['portion'] = f"{item['portion']} × {factor:g}"
    elif action == 'remove':
        index = int(value)
        if not 0 <= index < len(result['food_items']) or len(result['food_items']) == 1:
            raise ValueError('Cannot remove last item')
        result['food_items'].pop(index)
    elif action == 'manual':
        parts = str(value).split()
        if len(parts) not in (1, 4):
            raise ValueError('Enter kcal or kcal P F C')
        totals = [numeric(v, 0, 20000 if i == 0 else 2000) for i, v in enumerate(parts)]
        if len(totals) == 1:
            result['total_calories'] = round(totals[0])
            # Keep macros explicitly unchanged for calorie-only corrections.
            result['food_items'] = [dict(name=' · '.join(i['name'] for i in result['food_items']), portion='1',
                calories=round(totals[0]), protein=result['total_protein'], fat=result['total_fat'], carb=result['total_carb'])]
            return result
        result['food_items'] = [dict(name=' · '.join(i['name'] for i in result['food_items']), portion='1',
            calories=round(totals[0]), protein=totals[1], fat=totals[2], carb=totals[3])]
    else:
        raise ValueError('Unknown action')
    for total, item_key in [('total_calories', 'calories'), ('total_protein', 'protein'), ('total_fat', 'fat'), ('total_carb', 'carb')]:
        result[total] = round(sum(i[item_key] for i in result['food_items']), 1)
    result['total_calories'] = round(result['total_calories'])
    return result
