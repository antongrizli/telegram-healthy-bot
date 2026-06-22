# Telegram Healthy Tracker Bot 🍏

<p align="center">
  <a href="https://telegram.me/your_healthy_body_bot">
    <img src="assets/icon.jpg" alt="Telegram Healthy Tracker Bot Icon" width="50" height="50" style="border-radius: 50%; vertical-align: middle; margin-right: 10px;">
    🤖 <b>Try the bot on Telegram: @your_healthy_body_bot</b>
  </a>
</p>

A modern, asynchronous Telegram bot built in Python utilizing **aiogram v3**, **PostgreSQL**, **APScheduler**, and Google's **Gemini 2.5 Flash** API via the official unified `google-genai` SDK.

The bot helps users track their nutrition logs (via photo or text), record periodic weights, calculate daily allowances, and receive automated, AI-driven daily, weekly, and monthly reports detailing progress and fitness goal alignment (warning the user if weight is moving in a different direction than their goal).

---

## Features

1. **Food Logging (Multimodal & Multi-image Input)**:
   * Send a text description (e.g., *"A plate of rice, 150g salmon, and broccoli"*), a single photo, or **multiple images (media groups/albums)** representing a meal.
   * Media group updates are automatically bundled by a custom middleware to trigger only a single AI processing request.
   * Supports combined inputs (e.g., photo with a caption).
   * Gemini analyzes the inputs and returns structured nutritional facts (calories, protein, fat, carbohydrates).
   * Interactive **Accept / Correct / Cancel** flow allowing users to refine the estimates before saving.
2. **Interactive WebApp Dashboard**:
   * Accessible directly from the Telegram chat menu via a dynamically localized button based on user profile language.
   * Securely authenticated using Telegram webapp initialization signature hash validation.
   * Renders interactive charts showing daily nutrition (calories, protein, fat, carb counts over 7d/30d/90d) and weight progress vs baseline (over 30d/90d/180d).
   * Displays streaks (food logging, weight logging, calorie/protein targets) and lists all 66 unlockable achievements (with unlock status).
   * Hosts the user's weekly Personalized Health Card.
3. **Gamification System**:
   * Tracks daily habits with streak counters for food logging, weight logging, calorie target compliance (within ±15%), and protein targets (at least 90%).
   * Weekly-replenished **Streak Freezes** rescue active streaks from breaking during occasional missed days.
   * A repository of **66 achievements** covering streaks, volumes, specific dietary behaviors (e.g., "Keto Hero", "Rabbit", "Sweet Tooth", "Hydration"), and times.
   * Achievements can be shared with a single tap using automatically generated, copy-pasteable Markdown messages.
4. **Personalized AI Briefings & Reports**:
   * **Morning Briefing** (daily at 8:00 AM user local time): Empathetic, supportive summary of yesterday's intake and streaks, offering a targeted tip for today, accompanied by quick inline actions (e.g. log breakfast shortcut).
   * **Daily Report**: Computes today's totals against daily targets and generates tailored coaching recommendations.
   * **Weekly/Monthly Report**: Extensive habit review and weight trends. Weekly reports embed visual **Matplotlib charts** showing progress and include inline links to the interactive dashboard.
5. **Profile & Goals Wizard (Mifflin-St Jeor)**:
   * Smooth, step-by-step onboarding (FSM wizard) setting up language, physical metrics, activity levels, fitness goals, timezone, and custom notification times.
   * Automatically calculates daily caloric and macro targets based on the modern Mifflin-St Jeor BMR equation.
6. **Weight Tracking & Goal Mismatch Warning**:
   * Logs current weight and measures progress relative to baseline.
   * **Goal Mismatch Warning**: Detects if weight trend is moving in a direction contrary to the user's selected goal (e.g., losing weight while bulking), alerting them and generating specific adjusting advice.
7. **Global AI Rate Limiter & Background Queue**:
   * Limits Gemini API calls globally to 15 requests per minute and 1500 requests per day.
   * Rate-limited requests are placed into a database-backed queue (`AiRequestQueue`) and processed asynchronously by a background loop worker using exponential backoff retry.
8. **Vulnerability Scanner Blocking & Web Security**:
   * Intercepts incoming requests on the WebApp server and blocks malicious crawler/scanner User-Agents (e.g. Palo Alto Cortex, Censys, Shodan, Nmap), returning a 403 Forbidden status.
   * Includes a `/health` endpoint for database connectivity and server health verification.
9. **Multilingual Interface**:
   * Complete localized translation interface supporting **7 languages**: English, Russian, Ukrainian, Polish, German, Turkish, and Spanish.
10. **Admin Panel**:
    * Access statistics (registered users, activity counts, message counts, food entries logged, and detailed queue reliability stats/error reports).
    * Broadcast announcements to all active users.
    * Block/unblock users by Telegram ID.

---

## Installation & Setup

### 1. Prerequisites
* [Docker](https://www.docker.com/) and Docker Compose installed.
* A Telegram Bot Token (obtain from [@BotFather](https://t.me/BotFather)).
* A Gemini API Key (obtain from [Google AI Studio](https://aistudio.google.com/)).

### 2. Configure Environment
Clone this repository, copy the `.env.example` file to `.env`, and fill in your credentials:

```bash
cp .env.example .env
```

Open `.env` and edit:
```env
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
GEMINI_API_KEY=your-gemini-api-key
ADMIN_USER_IDS=your-telegram-id,another-admin-id
```

### 3. Build & Run
Start the application and database containers using Docker Compose:

```bash
docker-compose up --build -d
```

To stop the containers:
```bash
docker-compose down
```

---

## Development & Testing

If you want to run the project locally without Docker:

1. Create a python virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
2. Make sure you have a local PostgreSQL database running (or replace the `DATABASE_URL` in `.env` with a SQLite string, e.g. `sqlite+aiosqlite:///healthy_bot.db`).
3. Run the bot:
   ```bash
   python -m src.main
   ```

---

## Developer Guidelines

Developers and AI agents working on this repository must review and adhere to the [Developer Skill Guide](.agents/skills/developer-skill.md) located at `.agents/skills/developer-skill.md`.

Crucial rules in the guide include:
- Enforcing database operations via local session blocks encapsulated within `src/database/crud.py`.
- Preserving formatting and sanitizing Telegram Markdown output.
- Maintaining test coverage and keeping the guide and [SECURITY.md](SECURITY.md) updated dynamically as new bot capabilities or middleware layers are introduced.

---

## Directory Structure

```
telegram-healthy-bot/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── README.md
├── src/
│   ├── main.py                    # Entrypoint
│   ├── config.py                  # Environment config
│   ├── database/
│   │   ├── connection.py          # Session management
│   │   ├── models.py              # Database models (User, FoodLog, WeightLog, Streak, Achievement, HealthCard, etc.)
│   │   ├── crud.py                # Database CRUD operations
│   │   └── init_db.py             # Database schema initializer and migration script
│   ├── webapp/                    # WebApp Dashboard
│   │   ├── server.py              # WebApp API server and endpoints
│   │   ├── auth.py                # Secure Telegram init data validation
│   │   ├── middlewares.py         # Crawler and vulnerability scanner blocking middleware
│   │   └── static/                # Dashboard static assets (HTML, CSS, JS, locales)
│   ├── services/
│   │   ├── gemini.py              # Gemini client (google-genai SDK)
│   │   ├── scheduler.py           # APScheduler cron daemon (briefings, reports, daily checks)
│   │   ├── charts.py              # Matplotlib weekly progress charts
│   │   ├── briefing.py            # Daily Morning Briefing generator
│   │   ├── gamification.py        # Streak tracking, achievements, weekly Health Card math
│   │   └── rate_limiter.py        # AI request rate limiter and background queue worker
│   ├── handlers/
│   │   ├── common.py              # Command handlers (/start, /help, profile view)
│   │   ├── profile.py             # Setup FSM wizard
│   │   ├── food.py                # Food logging FSM and correction loop
│   │   ├── weight.py              # Weight logging
│   │   ├── callbacks.py           # Controller for inline keyboard callbacks
│   │   └── admin.py               # Administrative panel (stats, broadcasts, queue logs)
│   ├── keyboards/
│   │   ├── reply.py               # Reply menus
│   │   └── inline.py              # Inline selection menus
│   ├── middlewares/
│   │   ├── i18n.py                # Localization injection and dynamic Menu Button updates
│   │   ├── logging.py             # Message logs tracker
│   │   ├── admin_check.py         # Security gatekeeper
│   │   ├── registration_check.py  # Profile creation enforcer middleware
│   │   └── media_group.py         # Album/media group bundling middleware
│   └── utils/
│       ├── formulas.py            # Mifflin-St Jeor math
│       ├── escape.py              # Markdown formatting and LaTeX symbol escaping
│       └── i18n_locales.py        # Multilingual dictionaries (EN, RU, UK, PL, DE, TR, ES)
```
