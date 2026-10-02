# Reliability contracts

## Startup and schema

`init_db` creates new tables, then introspects and adds only missing startup columns.
PostgreSQL upgrades hold a transaction advisory lock. An error aborts startup instead
of continuing with an incomplete schema. User settings are never rewritten: weekday
0 means Monday, 6 means Sunday. Previously overwritten choices cannot be inferred;
users can select their desired day again in profile settings. This is an idempotent
startup upgrade mechanism; a full versioned Alembic migration history remains future work.

The new `ai_request_attempts` table is created automatically. Existing data is retained.

## AI quota and queue

| Setting | Default | Meaning |
| --- | --- | --- |
| `AI_REQUESTS_PER_MINUTE` | 15 | Global attempts in a rolling 60-second window |
| `AI_REQUESTS_PER_DAY` | 1500 | Global attempts in a rolling 24-hour window |
| `AI_USER_REQUESTS_PER_MINUTE` | 5 | Attempts for one user in 60 seconds |
| `AI_QUEUE_MAX_RETRIES` | 8 | Queue failure budget before terminal failure |

Every provider call goes through `call_gemini_with_retry`, which commits a quota
reservation before the network request. Each SDK-level retry reserves again.
PostgreSQL check-and-reserve uses an advisory transaction lock across independent
connections; a local event-loop lock also protects SQLite. Network calls never hold
the quota lock. These defaults are application policy, not a guarantee of a provider's
current pricing or quota; set them to match the actual API plan.

Successful AI operations keep their separate audit log. Pre-upgrade successes before
the first reservation count toward quota; subsequent successes do not double-count
reservations. Both ledgers store only IDs, request types and times, not prompts or
responses. Quota history older than 24 hours is cleaned when reserving. Profile
deletion anonymizes user IDs while retaining anonymous global quota usage.

Quota exhaustion defers queued work until the relevant window expires and does not
increment failures. Other exceptions consume the failure budget with capped backoff.
Terminal failures remain visible in existing queue administration. Saved analysis/OCR
results can be delivered without consuming or requiring additional AI quota.

One application/queue worker is supported. Adding more instances requires database
job leases; atomic provider quota alone does not prevent duplicate queue processing.

## Shutdown

Polling leaves ownership of the Telegram session to `main`. Stop the scheduler and
HTTP server, signal the worker and await it, then close FSM storage and Telegram.
The worker exits its idle wait immediately. If in-flight work exceeds 35 seconds,
cancel the worker; its persisted `processing` claim is requeued on the next startup.
Cancelling a coroutine does not necessarily interrupt a provider call already running
in a thread, so its reservation remains consumed and a restart may repeat that call.

## Progress card version 2

- Period: previous complete local calendar week, with an exclusive end boundary.
- Logging days use the profile timezone, including DST transitions.
- Nutrition averages divide by days with records, not seven calendar days. They
  describe recorded intake and do not assert that every meal was logged.
- Coverage exposes logged days out of seven and local dates with weight readings.
- Nutrition score is unknown without entries or a usable target. Weight score is
  unknown without readings on two distinct local dates.
- Overall diary score requires at least three logged days. Available categories use
  the existing 0.4/0.4/0.2 weights normalized to available categories. Three days is a
  product coverage threshold, not a clinically validated health threshold.
- Low-coverage cards have a localized explanation and skip the AI note. Other notes
  must acknowledge missing data and avoid medical/causal conclusions.
- Archived cards remain stored. WebApp hides old calculation scores and explains
  that updated values will appear in the next generated weekly card.

## Errors and verification

Medication validation uses controlled localized messages. Unexpected HTTP 5xx errors
have generic JSON and a request ID, with details logged only on the server. Opening
WebApp without Telegram initData shows launch guidance without private API requests.

`test_reliability.py` covers restart-safe settings, legacy columns, DST and missing
data, concurrent reservations, failed provider attempts, retry exhaustion, shutdown,
generic errors, model validation and anonymization. `test_postgres_reliability.py`
requires `TEST_POSTGRES_URL` and creates/removes only its own disposable schema.
The mobile browser smoke uses synthetic HTTP/Telegram fixtures, checks all seven
languages, unknown/archived card scores and unauthenticated navigation. CI requires
PostgreSQL and mobile checks before building/publishing the image.
