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

1. **Food Logging (Multimodal Input)**:
   * Send a text description (e.g., *"A plate of rice, 150g salmon, and broccoli"*) or a photo of a meal.
   * Gemini analyzes the image/description and returns structured nutritional facts (calories, protein, fat, carbohydrates).
   * Interactive **Accept / Correct / Cancel** flow. Users can edit/correct the estimates before logging.
2. **Profile & Goals Wizard (Mifflin-St Jeor)**:
   * First-time setup directly guides users through language selection, name, biological sex, age, height, weight, physical activity level, fitness goals, reminder settings, daily report time, and timezone.
   * Automatically calculates daily targets based on modern BMR formulas.
3. **Weight Tracking & Goal Mismatch Warning**:
   * Logs current weight and displays differences since the user's initial baseline.
   * Compiles daily/weekly/monthly weight progress.
   * **Goal Mismatch Warning**: If weight trend is negative while aiming to gain weight, or positive while aiming to lose weight, the bot alerts the user and Gemini generates tailored adjustments.
4. **Automated AI Reports**:
   * **Daily Report**: Combines today's food/weight logs and provides custom advice.
   * **Weekly/Monthly Report**: Long-term trends, averages, and detailed habit coaching.
5. **Multilingual Interface**:
   * Complete localized translation interface supporting **7 languages**: English, Russian, Ukrainian, Polish, German, Turkish, and Spanish.
6. **Admin Panel**:
   * Access statistics (registered users, activity counts, message counts, food entries logged).
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
│   │   ├── models.py              # User, food_log, weight_log tables
│   │   └── crud.py                # CRUD queries
│   ├── services/
│   │   ├── gemini.py              # Gemini client (google-genai SDK)
│   │   ├── scheduler.py           # APScheduler cron daemon
│   │   └── charts.py              # Matplotlib weekly progress charts
│   ├── handlers/
│   │   ├── common.py              # Command handlers (/start, /help, profile view)
│   │   ├── profile.py             # Setup FSM wizard
│   │   ├── food.py                # Food logging FSM and correction loop
│   │   ├── weight.py              # Weight logging
│   │   └── admin.py               # Administrative panel (stats, broadcasts)
│   ├── keyboards/
│   │   ├── reply.py               # Reply menus
│   │   └── inline.py              # Inline selection menus
│   ├── middlewares/
│   │   ├── i18n.py                # Localization injection
│   │   ├── logging.py             # Message logs tracker
│   │   ├── admin_check.py         # Security gatekeeper
│   │   └── registration_check.py  # Profile creation enforcer middleware
│   └── utils/
│       ├── formulas.py            # Mifflin-St Jeor math
│       └── i18n_locales.py        # Multilingual dictionaries (EN, RU, UK, PL, DE, TR, ES)
```
