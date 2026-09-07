# Security Policy

## Supported Versions

Only the latest release version is actively supported for security updates.

| Version | Supported          |
| ------- | ------------------ |
| >= 1.0  | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please **do not open a public issue**. Instead, report it privately to the maintainers to prevent premature exposure.

### Reporting Process

1. Email the vulnerability details to the project owner/maintainers (e.g., `security@healthybot.local` or the designated repository owner).
2. Include in your report:
   - A description of the vulnerability and its potential impact.
   - Detailed step-by-step instructions to reproduce the issue (including any scripts or mock inputs).
   - Suggestions for mitigation or fixing the issue, if available.
3. The security team will acknowledge receipt of your report within 48 hours and work with you to analyze and remediate the vulnerability.

### Disclosure Policy

- We follow a coordinated disclosure model.
- We aim to address confirmed security vulnerabilities within 30 days of receipt.
- Once a fix is verified, a new release will be published, and a public security advisory will be issued.

## Security Best Practices for this Bot

### 1. Telegram Bot Token Management
- Never commit the Telegram Bot Token (`TELEGRAM_BOT_TOKEN`) or any API keys (`GEMINI_API_KEY`, Postgres credentials) directly to version control.
- Always load these sensitive secrets from environment variables (e.g. using a `.env` file locally or container environment config).

### 2. Dependency Management & Vulnerability Patching
- Regularly scan and update all dependencies listed in `requirements.txt`.
- Make sure that third-party packages, such as `aiohttp` and `aiogram`, are kept up-to-date with versions containing security fixes (e.g., ensuring `aiohttp>=3.14.1` is used to prevent parser issues).

### 3. FSM Context and State Sanitization
- Ensure that active FSM (Finite State Machine) states are cleared via `state.clear()` when users cancel, exit, or trigger administrative actions. This prevents state hijacking or input poisoning where user inputs intended for one flow are incorrectly processed by a legacy active state handler.

### 4. Input Sanitization and Markdown Escaping
- Sanitise all user inputs before displaying them in responses using `escape_markdown` from `src.utils.escape` to avoid injection vulnerabilities or bot crashes caused by malformed markdown tokens.


### 5. Dashboard Authentication and Database Access
- Every private dashboard endpoint requires Telegram-signed `initData` with a valid timestamp. Plain user IDs are never credentials.
- Blocked accounts are denied dashboard access and queued AI work.
- Public health checks return generic failure status; database exception details stay in server logs.

### 6. Durable Meal Confirmation
- Meal drafts live in `ai_request_queue` with status `awaiting_confirm`. Confirmation and cancellation check ownership and atomically consume the draft. Meal insertion and draft consumption commit together.
- Inline confirmation IDs survive bot restarts. The Pending meals button retrieves up to 20 outstanding drafts, oldest first; confirm or cancel those to see more.
- The queue worker assumes a single application instance and resumes interrupted `processing` requests on startup. Horizontal scaling requires a database lease/claim mechanism first.

### Medication data

Medication library, reminders, intake marks and OCR results are private to the authenticated
Telegram user. `/api/medications` routes validate signed initData and reject blocked profiles;
CRUD and bot callbacks additionally constrain every object by its owner. Photo recognition
uses the existing Gemini queue; image bytes are removed from the queue payload after successful
recognition. Recognition requires user review before saving a product. AI reports receive
user-entered medication context only when reminder tasks exist. All product and recognized
text is rendered with `textContent` or plain Telegram text. Profile deletion includes medications,
schedules, intake records and queued photos. The local preview/test authentication overrides are
in temporary test scripts only and must never be used for deployment.
