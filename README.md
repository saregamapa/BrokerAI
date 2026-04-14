# BrokerAI

AI-powered social media campaign SaaS for real estate. LangGraph orchestrates **strategy → content → media → compliance → scheduling**, pauses for **one campaign approval**, then publishes scheduled posts via **Ayrshare**. FastAPI + SQLModel + SQLite, served as a single monolith with a vanilla HTML/Tailwind frontend.

---

## TL;DR

```bash
git clone <repo> brokerai && cd brokerai
cp .env.example .env                  # fill in OPENAI_API_KEY + JWT_SECRET_KEY at minimum
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.seed_demo --reset   # creates demo@brokerai.app / demo1234
uvicorn backend.main:app --reload --port 8000
open http://localhost:8000            # landing → /login
```

---

## Run locally

1. **Python 3.11** is the target runtime (matches Dockerfile + Render). 3.10 works for local dev.
2. `cp .env.example .env` and set at least:
   - `OPENAI_API_KEY` — required. `/generate-campaign` returns 503 without it.
   - `JWT_SECRET_KEY` — any 32+ char random string. Rotating invalidates all sessions.
3. `pip install -r requirements.txt`
4. `python -m scripts.seed_demo --reset` (optional — see [Demo account](#demo-account))
5. `uvicorn backend.main:app --reload --port 8000`
6. Visit `http://localhost:8000`. SQLite DB lands at `./brokerai.db`.

**Tests:** `pytest -q` — smoke suite in `tests/test_smoke_journey.py` covers the full signup → login → generate → approve → publish path with Ayrshare and OpenAI mocked. Keep it green.

---

## Deploy to Render

`render.yaml` at the repo root defines a Python web service that auto-deploys from `main`. In the Render dashboard:

1. **New → Blueprint**, point at this repo, apply `render.yaml`.
2. Set secret env vars on the service (same list as `.env`, minus placeholders).
3. First deploy runs `pip install -r requirements.txt` then `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`.
4. Health check: point Render (and UptimeRobot) at `/health`. It returns 503 if the DB ping fails, 200 otherwise, with `version` + `uptime_seconds`.
5. To ship structured JSON logs instead of text: set `LOG_FORMAT=json`.

Render injects `RENDER_GIT_COMMIT`; `/health` surfaces the first 12 chars as `version`.

**Docker:** `docker build -t brokerai . && docker run --env-file .env -p 8000:8000 brokerai`. The Dockerfile uses Python 3.11-slim.

---

## Environment variables

Grouped by concern. Only the ones marked **required** must be set for the app to boot.

### Core (required)
| Var | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | GPT-4 class model for the LangGraph pipeline. |
| `JWT_SECRET_KEY` | 32+ char random string. Signs auth tokens. |
| `DATABASE_URL` | SQLite by default (`sqlite:///./brokerai.db`). Swap for Postgres in prod. |

### Ayrshare (required in prod, optional locally)
| Var | Purpose |
| --- | --- |
| `AYRSHARE_API_KEY` | Primary API key from Ayrshare dashboard. |
| `AYRSHARE_SSO_DOMAIN` | App id from Business onboarding (`id-xxxxxx`, **not** your hostname). |
| `AYRSHARE_PRIVATE_KEY` **or** `AYRSHARE_PRIVATE_KEY_PATH` | PKCS#8 PEM for JWT SSO. |
| `AYRSHARE_MIN_LINKED_PLATFORMS` | `1`–`3`, default `1`. How many of FB/IG/LinkedIn count as “fully connected” for gates that use it. |
| `AYRSHARE_SINGLE_ACCOUNT_PUBLISH` | `true` to post without Profile-Key (local single-account dev only). |
| `PUBLIC_APP_URL` | Public HTTPS origin used as JWT `redirect` after Ayrshare link. |

### Optional integrations
| Var | Purpose |
| --- | --- |
| `UNSPLASH_ACCESS_KEY` | Fallback stock imagery when OpenAI image gen is skipped. |
| `REPLICATE_API_TOKEN` | Video generation (`/video/generate`). |
| `OPENAI_IMAGE_MODEL` | Override default `dall-e-3`. |
| `APP_VERSION` | Exposed in `/health`. Falls back to `RENDER_GIT_COMMIT` then `"dev"`. |
| `LOG_FORMAT` | `json` for structured logs; default text. |
| `BROKERAI_DISABLE_SCHEDULER` | `1` to skip the background scheduler (tests use this). |

See `.env.example` for the full, commented list.

---

## Architecture at a glance

```
frontend/ (vanilla HTML + Tailwind) ──►  FastAPI (backend/main.py)
                                          │
                                          ├── backend/auth.py         JWT + bcrypt
                                          ├── backend/db.py           SQLModel engine
                                          ├── backend/models.py       User, Campaign, Post, …
                                          ├── backend/services/       publish, ayrshare, analytics
                                          ├── backend/integrations/   ayrshare HTTP client
                                          ├── backend/agents/         LangGraph nodes
                                          └── backend/core/logger.py  JSON + PII-scrubbed events
```

- **One FastAPI app** serves both the JSON API and the static frontend (`app.mount("/static", …)` + per-page `FileResponse` routes).
- **LangGraph workflow** (`backend/agents/`): strategy → content → media → compliance → scheduling, gated by a human approval step before publishing.
- **Rate limiting** via `slowapi` with Starlette middleware. Disabled in tests.
- **Security headers** middleware adds CSP, HSTS, Referrer-Policy, Permissions-Policy.
- **Structured logging** — `backend/core/logger.py` exposes `log_event(name, **fields)` and `time_block(...)`. Emails are scrubbed to `a***@domain`.

---

## Demo account

```bash
python -m scripts.seed_demo --reset
```

Creates `demo@brokerai.app` / `demo1234` with:
- 2 campaigns (Austin Open House + First-Time Buyer Funnel)
- 14 review-ready posts across Facebook, Instagram, LinkedIn
- `social_connected=True` so dashboard + analytics render without OAuth

Safe to run on any environment — it only touches rows for the demo user. Drop `--reset` for idempotent re-runs (no-op when already seeded).

---

## Common gotchas (read this in 3 months)

1. **"Please connect your social accounts first" (403) on /generate-campaign** — user's `ayrshare_profile_key` is empty or Ayrshare `/user` returned 403. In tests, `mark_user_social_connected()` handles this; in dev, set `AYRSHARE_SINGLE_ACCOUNT_PUBLISH=true` or run the `/connect-social` flow.
2. **slowapi crash in tests** (`parameter response must be an instance of starlette.responses.Response`) — slowapi 0.1.9 can't decorate endpoints that return a bare Pydantic model. `tests/conftest.py` disables the limiter for this reason. Don't remove.
3. **Ayrshare "app id" ≠ hostname** — `AYRSHARE_SSO_DOMAIN` is the literal `id-xxxxxx` string from Ayrshare onboarding. Using `brokerai.app` here silently breaks JWT SSO.
4. **OpenAI returning `Strategy must return exactly N posts; got M`** — `CampaignPipelineError` surfaces when the LLM ignores `num_posts`. Usually a prompt regression; re-run or lower `num_posts`.
5. **Render cold start logs look empty** — set `LOG_FORMAT=json` and scroll. Uvicorn access logs are separate from our `brokerai.event` logger.
6. **DB migrations** — we don't use Alembic. SQLite table shape comes from `SQLModel.metadata.create_all`. For schema changes, either delete the DB (dev) or write a one-off script.
7. **Frontend pages missing the footer** — the shared footer is injected by `static/js/app.js` at `DOMContentLoaded`. Pages without `<script src="/static/js/app.js">` won't get it. Add `data-skip-footer` on `<body>` to opt out.
8. **Rate limits feel off in local dev** — slowapi keys on remote IP, which is `testclient` or `127.0.0.1` for everyone. Ten signups/hour is the default; bump or disable during load testing.
9. **Campaign approval is a hard gate** — posts won't publish until `/approve-campaign` succeeds. The scheduler silently skips non-approved campaigns.

---

## Useful endpoints

| Route | Notes |
| --- | --- |
| `GET /health`, `GET /healthz` | DB ping + version + uptime. Point UptimeRobot here. |
| `POST /signup`, `POST /login`, `GET /me` | JWT auth. |
| `POST /generate-campaign` | Kicks LangGraph phase 1. Returns draft posts. |
| `POST /approve-campaign` | Gate — only after approval do posts publish. |
| `POST /publish/{post_id}` | Manual publish, force-immediate. Idempotent. |
| `GET /analytics` | In-app analytics (no third-party SDK). |

---

## Project structure

```
backend/
  main.py              FastAPI app, routes, middleware, lifespan
  auth.py              bcrypt + JWT
  db.py                SQLModel engine + create_db_and_tables
  models.py            User, Team, Campaign, Post, SocialAccount, …
  schemas.py           Pydantic request/response models
  core/logger.py       JSON logger + log_event + time_block
  agents/              LangGraph nodes
  services/            publish, analytics, approval, scheduler
  integrations/        ayrshare HTTP client
frontend/              static HTML pages served by FastAPI
static/
  css/styles.css       design tokens + utility classes
  js/app.js            BrokerAI namespace (toast, confirm, loading, footer)
  img/favicon.svg
tests/                 pytest suite (smoke journey in test_smoke_journey.py)
scripts/
  seed_demo.py         create demo@brokerai.app + pre-filled campaigns
Dockerfile, render.yaml, requirements.txt, .env.example
```

---

## License

Proprietary. All rights reserved.
