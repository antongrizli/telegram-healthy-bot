import os
import logging
from datetime import datetime, UTC, timedelta
from zoneinfo import ZoneInfo
from aiohttp import web
from sqlalchemy import select, func, desc, and_

from src.database.connection import AsyncSessionLocal
from src.database import crud
from src.database.models import FoodLog, WeightLog, Streak, Achievement, HealthCard, User
from src.webapp.auth import validate_init_data
from src.services.gamification import ACHIEVEMENTS
from src.utils import i18n_locales
from src.webapp.middlewares import block_scanners_middleware

logger = logging.getLogger(__name__)

# JSON API Endpoints

async def get_nutrition_data(request: web.Request) -> web.Response:
    """
    GET /api/charts/nutrition?range=7d|30d
    Returns daily calories and macros for the selected range, plus target allowances.
    """
    user_id = validate_init_data(request)
    range_param = request.query.get("range", "7d")
    
    days_count = 7
    if range_param == "30d":
        days_count = 30
    elif range_param == "90d":
        days_count = 90
        
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
            
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        local_now = datetime.now(user_tz)
        local_today = datetime(local_now.year, local_now.month, local_now.day)
        
        start_date_local = local_today - timedelta(days=days_count - 1)
        start_date_utc = start_date_local.astimezone(UTC).replace(tzinfo=None)
        end_date_utc = local_now.astimezone(UTC).replace(tzinfo=None)
        
        food_logs = await crud.get_food_logs(db, user_id, start_date_utc, end_date_utc)
        
        # Prepare day slots in local time
        dates = []
        daily_data = {}
        for i in range(days_count):
            day = start_date_local + timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            dates.append(day_str)
            daily_data[day_str] = {
                "calories": 0,
                "protein": 0.0,
                "fat": 0.0,
                "carb": 0.0
            }
            
        # Map logs to local date slots
        for log in food_logs:
            log_local = log.logged_at.replace(tzinfo=UTC).astimezone(user_tz)
            day_str = log_local.strftime("%Y-%m-%d")
            if day_str in daily_data:
                daily_data[day_str]["calories"] += log.calories
                daily_data[day_str]["protein"] += log.proteins
                daily_data[day_str]["fat"] += log.fats
                daily_data[day_str]["carb"] += log.carbs
                
        # Format response
        result = {
            "dates": dates,
            "calories": [daily_data[d]["calories"] for d in dates],
            "protein": [round(daily_data[d]["protein"], 1) for d in dates],
            "fat": [round(daily_data[d]["fat"], 1) for d in dates],
            "carb": [round(daily_data[d]["carb"], 1) for d in dates],
            "targets": {
                "calories": user.target_calories,
                "protein": user.target_protein,
                "fat": user.target_fat,
                "carb": user.target_carb
            }
        }
        return web.json_response(result)

async def get_weight_data(request: web.Request) -> web.Response:
    """
    GET /api/charts/weight?range=30d|90d
    Returns weight logs for the selected range.
    """
    user_id = validate_init_data(request)
    range_param = request.query.get("range", "30d")
    
    days_count = 30
    if range_param == "90d":
        days_count = 90
    elif range_param == "180d":
        days_count = 180
        
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
            
        try:
            user_tz = ZoneInfo(user.timezone or "UTC")
        except Exception:
            user_tz = ZoneInfo("UTC")
            
        local_now = datetime.now(user_tz)
        start_date = (local_now - timedelta(days=days_count)).astimezone(UTC).replace(tzinfo=None)
        
        weight_logs = await crud.get_weight_logs(db, user_id, start_date, local_now.astimezone(UTC).replace(tzinfo=None))
        
        # Sort and serialize
        weight_logs = sorted(weight_logs, key=lambda w: w.logged_at)
        
        dates = []
        weights = []
        for log in weight_logs:
            log_local = log.logged_at.replace(tzinfo=UTC).astimezone(user_tz)
            dates.append(log_local.strftime("%Y-%m-%d"))
            weights.append(log.weight)
            
        return web.json_response({
            "dates": dates,
            "weights": weights,
            "baseline": user.weight_kg
        })

async def get_streaks_data(request: web.Request) -> web.Response:
    """
    GET /api/gamification/streaks
    """
    user_id = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
            
        streaks = await crud.get_user_streaks(db, user_id)
        result = []
        for s in streaks:
            result.append({
                "streak_type": s.streak_type,
                "current_count": s.current_count,
                "longest_count": s.longest_count,
                "last_logged_date": s.last_logged_date.strftime("%Y-%m-%d") if s.last_logged_date else None
            })
            
        return web.json_response({
            "streaks": result,
            "freezes_left": user.streak_freezes_left
        })

async def get_achievements_data(request: web.Request) -> web.Response:
    """
    GET /api/gamification/achievements
    """
    user_id = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
            
        unlocked = await crud.get_user_achievements(db, user_id)
        unlocked_keys = {a.achievement_key: a.unlocked_at for a in unlocked}
        
        result = []
        for key, ach in ACHIEVEMENTS.items():
            is_unlocked = key in unlocked_keys
            result.append({
                "key": key,
                "name": i18n_locales.get_text(ach["name_key"], user.language),
                "description": i18n_locales.get_text(ach["desc_key"], user.language),
                "icon": ach["icon"],
                "unlocked": is_unlocked,
                "unlocked_at": unlocked_keys[key].strftime("%Y-%m-%d %H:%M") if is_unlocked else None
            })
            
        return web.json_response(result)

async def get_health_card_data(request: web.Request) -> web.Response:
    """
    GET /api/gamification/health-card
    """
    user_id = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        card = await crud.get_latest_health_card(db, user_id)
        if not card:
            return web.json_response({"error": "No health card generated yet"}, status=404)
            
        return web.json_response({
            "week_start": card.week_start.strftime("%Y-%m-%d"),
            "card_data": card.card_data,
            "generated_at": card.generated_at.strftime("%Y-%m-%d %H:%M")
        })


async def get_user_settings(request: web.Request) -> web.Response:
    """
    GET /api/user/settings
    """
    user_id = validate_init_data(request)
    async with AsyncSessionLocal() as db:
        user = await crud.get_user(db, user_id)
        if not user:
            return web.json_response({"error": "User not found"}, status=404)
        return web.json_response({
            "language": user.language or "en",
            "name": user.name or "Guest",
            "timezone": user.timezone or "UTC"
        })


async def health_check(request: web.Request) -> web.Response:
    """
    GET /health
    """
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(select(1))
        return web.json_response({
            "status": "healthy",
            "database": "connected",
            "timestamp": datetime.now(UTC).isoformat()
        })
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return web.json_response({
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat()
        }, status=500)


# Web App Routing Setup

def create_app(bot) -> web.Application:
    app = web.Application(middlewares=[block_scanners_middleware])
    
    # Expose API endpoints
    app.router.add_get("/health", health_check)
    app.router.add_get("/api/user/settings", get_user_settings)
    app.router.add_get("/api/charts/nutrition", get_nutrition_data)
    app.router.add_get("/api/charts/weight", get_weight_data)
    app.router.add_get("/api/gamification/streaks", get_streaks_data)
    app.router.add_get("/api/gamification/achievements", get_achievements_data)
    app.router.add_get("/api/gamification/health-card", get_health_card_data)
    
    # Serve static assets
    static_path = os.path.join(os.path.dirname(__file__), "static")
    
    # Redirect /webapp to /webapp/ to ensure relative paths resolve correctly
    # Using a client-side redirect to preserve hash fragments (e.g., #tgWebAppData)
    async def redirect_webapp(request):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <script>
                var newPath = window.location.pathname + "/";
                if (window.location.search) {
                    newPath += window.location.search;
                }
                if (window.location.hash) {
                    newPath += window.location.hash;
                }
                window.location.replace(newPath);
            </script>
        </head>
        <body>
            Redirecting...
        </body>
        </html>
        """
        return web.Response(text=html, content_type="text/html")
    app.router.add_get("/webapp", redirect_webapp)
    
    # Serve index.html directly for webapp root path requests
    async def serve_index(request):
        return web.FileResponse(os.path.join(static_path, "index.html"))
    app.router.add_get("/webapp/", serve_index)
    
    app.router.add_static("/webapp/", static_path, name="webapp", show_index=False)
    
    return app
