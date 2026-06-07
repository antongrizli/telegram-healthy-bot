import json
import asyncio
import logging
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from src.config import settings

logger = logging.getLogger(__name__)

def extract_json(text: str) -> str:
    if not text:
        return ""
    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return text[start_idx:end_idx + 1]
    return text.strip()

# 1. Pydantic Models for Structured Outputs
class FoodItem(BaseModel):
    name: str = Field(description="Name of the food item in the selected language")
    portion: str = Field(description="Estimated weight or portion description, e.g., '150g' or '1 cup'")
    calories: int = Field(description="Calories in kcal")
    protein: float = Field(description="Protein in grams")
    fat: float = Field(description="Fat in grams")
    carb: float = Field(description="Carbohydrates in grams")

class FoodAnalysisResponse(BaseModel):
    food_items: List[FoodItem] = Field(description="List of all identified food items in the input")
    total_calories: int = Field(description="Sum of all calories in kcal")
    total_protein: float = Field(description="Sum of all protein in grams")
    total_fat: float = Field(description="Sum of all fat in grams")
    total_carb: float = Field(description="Sum of all carbohydrates in grams")

# Initialize the Gemini Client
client = genai.Client(api_key=settings.GEMINI_API_KEY)

async def call_gemini_with_retry(
    contents,
    config=None,
    model="gemini-2.5-flash",
    max_retries=4,
    initial_delay=0.1,
    backoff_factor=2.0
):
    """
    Calls Gemini generate_content in a non-blocking thread,
    retrying with exponential backoff on transient errors (like 503 UNAVAILABLE or 429).
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model,
                contents=contents,
                config=config
            )
            return response
        except Exception as e:
            err_str = str(e)
            is_transient = (
                "503" in err_str or 
                "UNAVAILABLE" in err_str or 
                "ResourceExhausted" in err_str or 
                "429" in err_str or
                getattr(e, "code", None) == 503 or
                getattr(e, "status_code", None) == 503
            )
            if is_transient and attempt < max_retries - 1:
                logger.warning(
                    f"Gemini API transient error (attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {delay:.2f}s..."
                )
                await asyncio.sleep(delay)
                delay *= backoff_factor
            else:
                raise e

async def analyze_food_input(
    text_description: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    language: str = "en"
) -> Optional[FoodAnalysisResponse]:
    """
    Sends text or image food input to Gemini 2.5 Flash and returns structured nutritional facts.
    """
    lang_name = "Russian" if language == "ru" else "English"
    prompt = (
        f"You are a professional nutrition expert. Analyze the food described in the text or image. "
        f"Estimate the name, portion size, calories, protein, fat, and carbs. "
        f"Provide the response in the language: {lang_name}. "
        f"Make sure to sum up the values correctly."
    )
    
    contents = []
    if image_bytes:
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/jpeg"
        )
        contents.append(image_part)
    
    if text_description:
        contents.append(text_description)
        
    contents.append(prompt)
    
    try:
        response = await call_gemini_with_retry(
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FoodAnalysisResponse,
                temperature=0.2
            )
        )
        print(f"AI raw response: {response.text}")
        raw_json = extract_json(response.text)
        data = json.loads(raw_json)
        return FoodAnalysisResponse(**data)
    except Exception as e:
        print(f"Error calling AI: {e}")
        return None

async def adjust_food_analysis(
    original_data: dict,
    correction_text: str,
    language: str = "en"
) -> Optional[FoodAnalysisResponse]:
    """
    Re-evaluates a food analysis based on the user's text corrections.
    """
    lang_name = "Russian" if language == "ru" else "English"
    prompt = (
        f"You are a professional nutrition expert. The user previously logged food, and it was analyzed as follows:\n"
        f"{json.dumps(original_data, indent=2)}\n\n"
        f"The user has now provided the following corrections:\n"
        f"'{correction_text}'\n\n"
        f"Please adjust the food items list, portion sizes, calories, and macros based on these corrections. "
        f"Provide the output in the language: {lang_name}."
    )
    
    try:
        response = await call_gemini_with_retry(
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=FoodAnalysisResponse,
                temperature=0.2
            )
        )
        print(f"AI raw response (correction): {response.text}")
        raw_json = extract_json(response.text)
        data = json.loads(raw_json)
        return FoodAnalysisResponse(**data)
    except Exception as e:
        print(f"Error calling AI for correction adjustment: {e}")
        return None

async def generate_report(
    profile: dict,
    food_logs: list,
    weight_logs: list,
    report_type: str, # "daily", "weekly", "monthly"
    language: str = "en"
) -> str:
    """
    Generates a personalized text report using Gemma.
    """
    goal_mapping = {
        "lose_weight": "Lose Weight",
        "maintain": "Maintain Weight",
        "gain_weight": "Gain Weight",
        "gain_muscle": "Gain Muscle"
    }
    activity_mapping = {
        "sedentary": "Sedentary (No exercise)",
        "light": "Lightly Active (1-3 days/wk)",
        "moderate": "Moderately Active (3-5 days/wk)",
        "active": "Very Active (6-7 days/wk)"
    }
    raw_goal = profile.get("goal")
    raw_activity = profile.get("activity_level")
    
    profile_text = (
        f"Name: {profile.get('name')}\n"
        f"Sex: {profile.get('sex')}\n"
        f"Age: {profile.get('age')}\n"
        f"Height: {profile.get('height_cm')} cm\n"
        f"Current Weight: {profile.get('weight_kg')} kg\n"
        f"Activity Level: {activity_mapping.get(raw_activity, raw_activity)}\n"
        f"Goal: {goal_mapping.get(raw_goal, raw_goal)}\n"
        f"Targets: Calories: {profile.get('target_calories')} kcal, "
        f"Protein: {profile.get('target_protein')}g, Fat: {profile.get('target_fat')}g, Carb: {profile.get('target_carb')}g\n"
    )

    food_text = ""
    for log in food_logs:
        logged_time = log.logged_at.strftime('%Y-%m-%d %H:%M')
        items_str = ", ".join([f"{i.get('name')} ({i.get('portion')})" for i in log.items_json])
        food_text += f"- [{logged_time}] {items_str} | Cal: {log.calories} kcal, P: {log.proteins}g, F: {log.fats}g, C: {log.carbs}g\n"
        
    if not food_text:
        food_text = "No meals logged during this period.\n"

    weight_text = ""
    for log in weight_logs:
        logged_time = log.logged_at.strftime('%Y-%m-%d %H:%M')
        weight_text += f"- [{logged_time}] {log.weight} kg\n"
        
    if not weight_text:
        weight_text = f"No weights logged during this period. Profile weight is {profile.get('weight_kg')} kg.\n"

    lang_name = "Russian" if language == "ru" else "English"
    
    if report_type == "daily":
        prompt = (
            f"You are a professional nutrition and fitness coach. "
            f"Generate a daily report for the user in the language: {lang_name}. "
            f"Format the output using Telegram-compatible Markdown (bolding, lists, code blocks for tables).\n\n"
            f"--- USER PROFILE ---\n{profile_text}\n"
            f"--- FOOD LOGS FOR TODAY ---\n{food_text}\n"
            f"--- WEIGHT LOGS ---\n{weight_text}\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Explain what they have eaten today and analyze their meals.\n"
            f"2. Give them a list of specific things they need to focus on more (e.g., eating more protein, choosing healthier fats, staying hydrated, getting active, adjusting calorie/macro intake relative to targets).\n"
            f"3. Keep the tone encouraging, professional, and clear. Keep the text concise and under 2500 characters so it fits within Telegram limits. Avoid long introductions. Start directly with the report."
        )
    elif report_type == "weekly":
        prompt = (
            f"You are a professional nutrition and fitness coach. "
            f"Generate a weekly report for the user in the language: {lang_name}. "
            f"Format the output using Telegram-compatible Markdown (bolding, lists, code blocks for tables).\n\n"
            f"--- USER PROFILE ---\n{profile_text}\n"
            f"--- FOOD LOGS FOR PREVIOUS WEEK ---\n{food_text}\n"
            f"--- WEIGHT LOGS ---\n{weight_text}\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Explain what they have eaten during the previous week and analyze their meals.\n"
            f"2. Give them a list of specific things they need to focus on more (e.g., eating more protein, choosing healthier fats, staying hydrated, getting active, adjusting calorie/macro intake relative to targets).\n"
            f"3. Keep the tone encouraging, professional, and clear. Keep the text concise and under 2500 characters so it fits within Telegram limits. Avoid long introductions. Start directly with the report."
        )
    else:
        prompt = (
            f"You are a professional nutrition and fitness coach. "
            f"Generate a {report_type} report for the user in the language: {lang_name}. "
            f"Format the output using Telegram-compatible Markdown (bolding, lists, code blocks for tables).\n\n"
            f"--- USER PROFILE ---\n{profile_text}\n"
            f"--- FOOD LOGS FOR PERIOD ---\n{food_text}\n"
            f"--- WEIGHT LOGS FOR PERIOD ---\n{weight_text}\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Summarize total calories and macronutrients consumed versus user targets. "
            f"Calculate the average daily values and percentages of completion.\n"
            f"2. Detail the weight changes. Analyze if the weight trend aligns with their goal (Goal: {profile.get('goal')}).\n"
            f"   - If their goal is to lose weight, weight should decrease. If it increased or stayed same, note the deviation.\n"
            f"   - If their goal is to gain weight/muscle, weight should increase. If it decreased or stayed same, note the deviation.\n"
            f"   - If weight is moving in a different direction than their aim, issue a distinct, polite, and encouraging WARNING, "
            f"and explain WHY it might be happening (e.g., fluid retention, underestimating portions, too high/low calorie targets).\n"
            f"3. Provide actionable recommendations (e.g., adjust caloric intake, increase protein, watch out for specific food categories, adjust water/physical activity level).\n"
            f"4. Keep the tone encouraging, professional, and clear. Keep the text concise and under 2500 characters. Avoid writing long introductions. Start directly with the report."
        )
    
    try:
        response = await call_gemini_with_retry(
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.3
            )
        )
        report_text = response.text
        if report_text:
            from src.utils.escape import clean_telegram_markdown
            report_text = clean_telegram_markdown(report_text)
        return report_text
    except Exception as e:
        print(f"Error calling AI for report: {e}")
        if language == "ru":
            return "⚠️ Не удалось сгенерировать отчет с помощью ИИ. Пожалуйста, попробуйте позже."
        return "⚠️ Failed to generate report using AI. Please try again later."
