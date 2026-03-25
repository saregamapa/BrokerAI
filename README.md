# BrokerAI

Agentic social campaigns for real estate: **LangGraph** orchestrates strategy → content → media → compliance → scheduling, then pauses for **one campaign approval** before the **publishing** step activates scheduled **Ayrshare** posts. Includes JWT auth, **campaigns + posts** in SQLite, and a **calendar** dashboard.

## Stack

- **Backend:** FastAPI, Uvicorn, **LangChain + LangGraph** (OpenAI), SQLModel, SQLite, httpx, bcrypt, python-jose  
- **Publishing:** Ayrshare API (background scheduler, **2 HTTP retries** per tick)  
- **Frontend:** Static HTML, Tailwind (CDN), vanilla JS, Marked.js  

## UI & experience

The product UI is a **light, premium SaaS shell** (Stripe / Notion / Linear–inspired): **Inter** typography, soft **gray canvas** (`#f9fafb`), **fixed dark sidebar** (`#0f172a`), **amber accent** CTAs, and **glass-style** panels (`backdrop-blur`, subtle borders).

- **Layout:** Authenticated pages use a **fixed left nav**, **sticky top bar** (account email + log out), and a **scrollable main** area with gentle page **fade-in**. Mobile uses a **drawer** for the sidebar (hamburger + overlay).
- **Wizard:** **Step-based** flow (business → schedule → platforms & AI → generate), **dropdowns**, **date** and **time** pickers, and a full-screen **generation overlay** with progress copy (“Generating your campaign…”), bar, and agent step dots.
- **Review:** Each post is a **raised card** (image on top, caption, hashtag chips, collapsible **video script**, **compliance** badge: green pass / yellow review / red fail). **Edit caption** and a **per-post approve** toggle (when the campaign is still pending) complement **Approve & schedule** for the whole campaign.
- **Calendar dashboard:** **Analytics summary** (from **`GET /analytics`**) plus **Month grid** with **rounded day cells**, shadows, hover states, **status dots** with **tooltips** (`title`). **Click a date** to open a **modal** with post previews (image, caption snippet, status badge). Stats cards mirror the same card language.
- **Feedback:** **Toasts** for success (green accent), error (red), and info (blue). No React/Vue and no heavy animation libraries—only CSS transitions and Tailwind utilities.

Shared chrome lives in `static/js/nav.js` and `static/css/styles.css`; pages are under `frontend/`.

## Security

- Put **`OPENAI_API_KEY`** and **`AYRSHARE_API_KEY`** only in `.env` or your host’s secret store. **Never commit keys** or paste them into issues/chat—rotate any key that was exposed.

### OpenAI not used / template posts only

1. Create **`.env`** in the **project root** (same folder as `requirements.txt`) with `OPENAI_API_KEY=sk-...` (real key, not `your_key_here`).  
2. **Restart** Uvicorn after editing `.env`.  
3. The wizard shows a banner if `GET /health` reports `openai_configured: false`.  
4. The app loads `.env` at startup via `backend/env_loader.py` (imported before agents). Start Uvicorn from the project root: `uvicorn backend.main:app`.

## Agent pipeline (LangGraph)

In-memory checkpoint pauses **`interrupt_before=["publishing"]`** after posts are persisted with `pending_approval`. **`POST /approve-campaign`** resumes the graph (or, if the process restarted and memory is empty, runs the **publishing node** directly from the DB so approval still works).

Nodes: **strategy** → **content** (OpenAI structured captions / hashtags / `image_prompt`; `video_script` left empty) → **media** (OpenAI Images API + optional structured **video scripts**) → **compliance** (OpenAI structured review + one recheck) → **scheduling** → **persist** (rejects empty caption or non-https `image_url`) → **approval_gate** → **publishing**. There is **no** template or placeholder media path — `OPENAI_API_KEY` and enabled AI toggles are required.

## Project layout

```
BrokerAI-main/        # project root — .env lives here
  backend/
    agents/           # LangGraph graph + nodes
    core/             # logging
    services/         # analytics (SQL aggregates)
    integrations/     # Ayrshare
    auth.py
    ...
  frontend/
  static/js/          # app.js, nav.js
  tests/              # pytest + TestClient
```

## Environment variables

| Variable | Purpose |
|----------|---------|
| `OPENAI_API_KEY` | **Required** for campaign generation (strategy, content, images, compliance). |
| `OPENAI_IMAGE_MODEL` | Optional (default `dall-e-3`). Some models return base64-only; the pipeline requires an **https** image URL. |
| `AYRSHARE_API_KEY` | Real publishing via Ayrshare. |
| `JWT_SECRET_KEY` | Sign JWTs in production (no hardcoded secrets in code). |
| `PORT` | Listen port for Docker / Render / `python -m backend.main` (default **8000**). |
| `DATABASE_URL` | Optional DB URL (default SQLite file in project root). |
| `BROKERAI_DISABLE_SCHEDULER` | Set to `1` to disable the 60s publish loop (used by pytest). |

## Local setup

```bash
cd BrokerAI-main   # or your clone folder name
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add keys (project root)
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Open **http://127.0.0.1:8000**.

Use **one Uvicorn worker** if you rely on in-process LangGraph `MemorySaver` checkpoints; after a restart, approval still completes via the DB fallback.

## User flow

1. **Sign up / log in**  
2. **Wizard** → `POST /generate-campaign` runs agents through scheduling and saves one week of posts (`CAMPAIGN_POST_COUNT` in `backend/agents/nodes.py`, currently **7**) + **campaign** (`pending_approval`).  
3. **Review** → inspect captions, images, video scripts, compliance.  
4. **Approve campaign** → `POST /approve-campaign` resumes publishing; posts become **`approved`** and the **scheduler** sends them to Ayrshare when `scheduled_at` is due.  
5. **Calendar** dashboard → month grid by `scheduled_at` / `published_at`.

## Analytics

Lightweight **in-database** metrics (no third-party analytics SDK):

- **`GET /analytics`** (auth) — `total_campaigns`, `total_posts`, `posts_published`, `posts_failed`, `success_rate` (integer percent), `last_published_at` (ISO string or `null`).
- **`GET /stats`** (auth) — extended breakdown (scheduled / pending counts, `success_rate_pct` float, `plan`) for dashboards.

The **Calendar** page shows an **Analytics summary** row fed by `/analytics`.

## Running tests

Install dev dependency **`pytest`**, then from the project root:

```bash
pip install -r requirements.txt
pytest tests/
```

Or:

```bash
bash scripts/run_tests.sh
```

Tests use a **temporary SQLite file**, a test **`JWT_SECRET_KEY`**, and **`BROKERAI_DISABLE_SCHEDULER=1`** so the background scheduler does not run during the suite. LangGraph phase 1 is **stubbed** in campaign tests so OpenAI is not required.

## API overview

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/signup`, `/login` | No | JWT |
| GET | `/me` | Yes | Current user |
| POST | `/generate-campaign` | Yes | LangGraph phase 1 → `{ campaign_id, posts }` |
| GET | `/campaign/{id}` | Yes | Campaign + posts |
| POST | `/approve-campaign` | Yes | Body `{ "campaign_id" }` — resume publishing (async) |
| GET | `/posts` | Yes | All your posts (calendar uses this) |
| GET | `/analytics` | Yes | Account analytics (see [Analytics](#analytics)) |
| GET | `/stats` | Yes | Extended stats + plan |
| POST | `/check-compliance` | Yes | Optional `post_id` |
| POST | `/approve-post/{id}`, `/update-post/{id}` | Yes | Legacy / per-post |
| GET | `/health` | No | `{"status":"ok","ok":true,"openai_configured":...}` |

### Rate limits

- **`POST /generate-campaign`**: max **10** generations per user per **UTC calendar day** (in-memory counter; resets on process restart). Plan-based campaign caps still apply.

## Logging

Structured-style logs go to **stdout** with format  
`timestamp | LEVEL | logger | message`  
configured in `backend/core/logger.py`. Notable loggers: **`brokerai`** (API, scheduler, publishing) and **`brokerai.agents`** (LangGraph / nodes). Campaign generation, publishing outcomes, and scheduler errors are logged at **INFO** / **WARNING** / **ERROR**.

## Docker / Render

### Render.com (recommended path)

1. Push this repo to **GitHub** (or GitLab / Bitbucket supported by Render).
2. In [Render](https://dashboard.render.com): **New** → **Blueprint** → connect the repo → select **`render.yaml`** → **Apply**.
3. When prompted, set environment variables marked **`sync: false`**:
   - **`OPENAI_API_KEY`** — for LangChain / agents (optional but recommended).
   - **`AYRSHARE_API_KEY`** — for publishing to social accounts.
   - **`ALLOWED_ORIGINS`** — set to your app URL, e.g. `https://brokerai.onrender.com`, or `*` for quick tests (tighten for production).
4. **`JWT_SECRET_KEY`** is auto-generated by the blueprint. To rotate it later, change it under **Environment** for the web service.
5. **Health check**: Render uses **`GET /health`** (configured in `render.yaml`).
6. **Database**: By default the app uses **SQLite** on the instance disk (data can be lost on redeploy or if the instance is replaced). For production, create a **Render PostgreSQL** instance, copy its **Internal Database URL** into **`DATABASE_URL`**, and redeploy. `psycopg2-binary` is included in `requirements.txt` for Postgres URLs.
7. **CORS**: The SPA is served by the same FastAPI app, so the UI works on your Render URL; set **`ALLOWED_ORIGINS`** if you call the API from another origin.

### Docker (any host)

1. Set secrets in the host UI or container env: **`OPENAI_API_KEY`**, **`AYRSHARE_API_KEY`**, **`JWT_SECRET_KEY`** (and optional **`DATABASE_URL`** for a managed DB).
2. **Build** from the repo root using the **`Dockerfile`** (`python:3.11-slim`, installs `requirements.txt`).
3. **Start** with host **`PORT`** (Render injects this automatically):

   ```bash
   uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```

   The image **`CMD`** matches the above. Default exposed port is **8000** when `PORT` is unset.
4. Point your **health check** to **`GET /health`** and expect **`status: "ok"`**.

Alternatively run locally without Docker:

```bash
PORT=8000 python -m backend.main
```

## License

Use and modify freely for your MVP.
