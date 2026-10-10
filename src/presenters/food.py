"""Food presentation and message formatting utilities."""
from typing import Any
from src.utils import i18n_locales
from src.utils.escape import clean_md


def format_food_analysis(analysis: Any, lang: str) -> str:
    """
    Formats a FoodAnalysisResponse (or dict representation) into localized text with
    item breakdown and total nutritional values.
    """
    items = []
    food_items = getattr(analysis, "food_items", None)
    if food_items is None and isinstance(analysis, dict):
        food_items = analysis.get("food_items", [])

    for item in (food_items or []):
        name = clean_md(getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else ""))
        portion = clean_md(getattr(item, "portion", None) or (item.get("portion") if isinstance(item, dict) else ""))
        cal = getattr(item, "calories", None) or (item.get("calories") if isinstance(item, dict) else 0)
        p = getattr(item, "protein", None) or (item.get("protein") if isinstance(item, dict) else 0)
        f = getattr(item, "fat", None) or (item.get("fat") if isinstance(item, dict) else 0)
        c = getattr(item, "carb", None) or (item.get("carb") if isinstance(item, dict) else 0)
        items.append(f"- **{name}** ({portion}): {cal} kcal | P: {p}g, F: {f}g, C: {c}g")

    items_str = "\n".join(items)
    if items_str:
        items_str += "\n"

    total_calories = getattr(analysis, "total_calories", None) or (analysis.get("total_calories") if isinstance(analysis, dict) else 0)
    total_protein = getattr(analysis, "total_protein", None) or (analysis.get("total_protein") if isinstance(analysis, dict) else 0)
    total_fat = getattr(analysis, "total_fat", None) or (analysis.get("total_fat") if isinstance(analysis, dict) else 0)
    total_carb = getattr(analysis, "total_carb", None) or (analysis.get("total_carb") if isinstance(analysis, dict) else 0)

    return i18n_locales.get_text(
        "food_analysis_result",
        lang,
        items=items_str,
        calories=total_calories,
        protein=total_protein,
        fat=total_fat,
        carb=total_carb,
    )


def format_draft_card_text(payload: dict, user_language: str) -> str:
    data = payload.get("analysis", {})
    def _item_str(item):
        name = item.get('name', '')
        portion = item.get('portion') or (f"{item['weight_grams']}g" if 'weight_grams' in item else "")
        return f"- {name} ({portion})" if portion else f"- {name}"
    items = "\n".join(_item_str(item) for item in data.get("food_items", []))
    text = i18n_locales.get_text(
        "food_analysis_result",
        user_language,
        items=items,
        calories=data.get("total_calories", 0),
        protein=data.get("total_protein", 0),
        fat=data.get("total_fat", 0),
        carb=data.get("total_carb", 0)
    )
    text += '\n' + i18n_locales.get_text('meal_type_' + payload.get('meal_type', 'food'), user_language)
    text += '\n' + i18n_locales.get_text('ux_estimate', user_language)
    return text


def format_drafts_list(drafts: list, page: int, total_pages: int, user_language: str, total_count=None) -> str:
    title = i18n_locales.get_text("food_drafts_title", user_language)
    paged = total_count is not None
    total_count = len(drafts) if total_count is None else total_count

    if total_pages > 1:
        header = f"{title} ({total_count} · {page}/{total_pages})"
    else:
        header = f"{title} ({total_count})"

    start_idx = (page - 1) * 5
    page_drafts = drafts if paged else drafts[start_idx:start_idx + 5]

    kcal_str = "ккал" if user_language in ("ru", "uk") else "kcal"

    entries = []
    for i, draft in enumerate(page_drafts, start=start_idx + 1):
        payload = getattr(draft, "payload", {}) or {}
        meal_type_key = "meal_type_" + payload.get("meal_type", "food")
        type_label = i18n_locales.get_text(meal_type_key, user_language)

        analysis = payload.get("analysis") or {}
        calories = analysis.get("total_calories", 0)

        food_items = analysis.get("food_items") or []
        item_names = [it.get("name") for it in food_items if it.get("name")]
        items_str = ", ".join(item_names)
        if len(items_str) > 60:
            items_str = items_str[:57] + "..."

        entry = f"{i}. {type_label} · {calories} {kcal_str}"
        if items_str:
            entry += f"\n   {items_str}"
        entries.append(entry)

    prompt = i18n_locales.get_text("food_drafts_select_prompt", user_language)
    body = "\n".join(entries)
    return f"{header}\n\n{body}\n\n{prompt}"

