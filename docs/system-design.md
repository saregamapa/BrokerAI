# BrokerAI — System Design

**Version:** 1.0
**Date:** 2026-04-02
**Scope:** Full platform — auth, campaigns, AI pipeline, publishing, analytics, teams

---

## 1. Requirements

### Functional Requirements

| # | Requirement |
|---|-------------|
| F1 | Users sign up / log in; sessions are JWT-protected |
| F2 | Users create campaigns with objective, audience, and platforms |
| F3 | AI pipeline generates strategy, captions, hashtags, image prompts, and schedules |
| F4 | Compliance agent screens generated content against real-estate ad rules |
| F5 | Users review and edit generated posts before publishing |
| F6 | Users connect social accounts via Ayrshare OAuth / JWT SSO |
| F7 | Posts are scheduled and auto-published at optimal times via Ayrshare |
| F8 | Analytics (likes, impressions, engagement) are surfaced per campaign and post |
| F9 | Team owners can invite members; roles control what each member can do |
| F10 | Campaign status flows: `draft → in_review → approved → publishing → published` |

### Non-Functional Requirements

| # | Requirement | Target |
|---|-------------|--------|
| N1 | AI generation latency | < 60 s per campaign (async, streamed to client) |
| N2 | Publish reliability | Retry up to 3× with exponential back-off |
| N3 | Auth security | bcrypt passwords, short-lived JWTs, no plain-text secrets |
| N4 | Idempotency | Duplicate publish calls must not double-post |
| N5 | Multi-tenancy | Team campaigns isolated; members see only their team's data |
| N6 | Deployability | Single Docker image; deployable on Render free/starter tier |

### Constraints

- Single Python process (FastAPI + Uvicorn, one worker)
- SQLite for MVP (swappable via `DATABASE_URL`)
- OpenAI API for all generation (GPT-4 class)
- Ayrshare as the only social publishing integration
- Vanilla JS frontend — no build toolchain

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Browser (Client)                            │
│   login · signup · dashboard · wizard · review · analytics          │
│                    Vanilla HTML / CSS / JS                          │
└─────────────────────────┬───────────────────────────────────────────┘
                          │  HTTPS  (JWT Bearer)
┌─────────────────────────▼───────────────────────────────────────────┐
│                   FastAPI Monolith (Uvicorn)                        │
│                                                                     │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────────┐   │
│  │ Auth     │  │ RBAC / Perms │  │ Route Handlers             │   │
│  │ JWT/bcrypt│  │ owner·admin  │  │ /auth /campaigns /posts    │   │
│  └──────────┘  │ ·member      │  │ /social /analytics /teams  │   │
│                └──────────────┘  └────────────┬───────────────┘   │
│                                               │                    │
│                          ┌────────────────────▼──────────────┐    │
│                          │          Services Layer            │    │
│                          │  campaign · publish · scheduler    │    │
│                          │  ayrshare · analytics · team       │    │
│                          └────────────┬──────────────────────┘    │
└───────────────────────────────────────┼────────────────────────────┘
                                        │
              ┌─────────────────────────┼───────────────────┐
              │                         │                   │
┌─────────────▼──────┐   ┌─────────────▼────────┐  ┌──────▼──────┐
│  SQLite (main DB)  │   │  LangGraph Pipeline   │  │  Ayrshare   │
│  brokerai.db       │   │  (in-process async)   │  │  API        │
│                    │   │                        │  │             │
│  users             │   │  strategy → content   │  │ scheduling  │
│  campaigns         │   │  → media → compliance │  │ publishing  │
│  posts             │   │  → scheduling →       │  │ analytics   │
│  teams             │   │  persist → approval   │  └─────┬───────┘
│  social_accounts   │   │  gate → publishing    │        │
│  team_invites      │   └────────────┬──────────┘  ┌────▼──────────────┐
└────────────────────┘                │             │ Social Platforms   │
                                      │             │ Instagram LinkedIn │
┌─────────────────────┐    ┌──────────▼──────────┐  │ Facebook Twitter  │
│ LangGraph Checkpts  │    │  OpenAI API          │  └───────────────────┘
│ brokerai_checkpts   │    │  GPT-4 class models  │
│ .db (SQLite)        │    └─────────────────────┘
└─────────────────────┘
```

### Key Design Decisions

**Monolith over microservices.** At MVP scale, a single FastAPI process eliminates network latency between components and simplifies deployment to a single Render service. The services/ layer enforces logical separation so extraction to microservices is straightforward later.

**SQLite for MVP.** Zero-ops, zero-cost. The `DATABASE_URL` env var lets the team swap to PostgreSQL on Render without changing application code (SQLModel abstracts the driver).

**LangGraph in-process.** The AI pipeline runs inside the same Python process as the API. This avoids a separate worker queue for MVP, but means long-running generation (30–60 s) occupies a thread. This is acceptable with a single-user beta; see §6 for the scaling path.

---

## 3. Component Deep Dive

### 3.1 Authentication & RBAC

```
POST /auth/signup  →  hash password (bcrypt)  →  create User  →  return JWT
POST /auth/login   →  verify password          →  return JWT
GET  /auth/me      →  decode JWT               →  return User
```

**JWT payload:** `{ user_id, exp }`

**RBAC model:**

```
account_type: individual | team | org
role:         owner | admin | member
```

The `check_permission(user, action, resource)` helper in `permissions.py` gates every sensitive route. Members can view and create campaigns but cannot approve or publish; admins can approve; owners have full control.

**Trade-off:** Role logic lives in a single Python dict/function. Simple now, but adding new roles means touching code. A policy table in the DB would be more flexible at the cost of added complexity — fine to defer.

---

### 3.2 Data Model

```
User ─── 1:N ──► Campaign ─── 1:N ──► Post
  │                  │
  └── FK to Team     └── FK to Team (nullable, for team campaigns)

Team ─── 1:N ──► TeamInvite
  │
  └── 1:N via User.team_id ──► User

User ─── 1:1 ──► SocialAccount (ayrshare_profile_key)
```

**Campaign status machine:**

```
draft ──► in_review ──► approved ──► publishing ──► published
                                                 └──► failed
```

Post follows a parallel machine: `draft → review → approved → publishing → published | failed`

**Notable fields:**

- `Post.idempotency_key` — prevents duplicate Ayrshare calls on retry
- `Post.publish_attempts` + `max_attempts` — retry budget (default 3)
- `Post.is_locked` + `lock_timestamp` — prevents concurrent scheduler races
- `Post.compliance_passed` — stored per-post so re-check can skip clean posts

---

### 3.3 LangGraph AI Pipeline

The pipeline is a **compiled StateGraph** with a **hard interrupt** before the publishing node. This splits execution into two phases with a human-in-the-loop approval gate:

```
Phase 1 (triggered by POST /campaigns/{id}/generate):
  strategy → content → media → compliance → scheduling → persist → approval_gate
                                                                        ↓
                                                              [INTERRUPT — waits for user]

Phase 2 (triggered by POST /campaigns/{id}/publish):
  resume → publishing → END
```

**State schema (`AgentState`):**

```python
{
  user_id: int,
  campaign_id: int,
  approved: bool,
  num_posts: int,
  campaign_data: dict,   # raw wizard inputs
  strategy_plan: dict,   # output of strategy agent
  posts: list[dict],     # accumulates through content/media/compliance
  step_log: list[str],   # append-only trace (Annotated with operator.add)
}
```

**Checkpointing:** LangGraph state is persisted to `brokerai_checkpoints.db` (SQLite) via `SqliteSaver`. If the server restarts between Phase 1 and Phase 2, `resume_campaign_publishing` can reload the thread state from disk and continue. Falls back to `MemorySaver` if the checkpoint package is unavailable.

**Agent responsibilities:**

| Agent | Input | Output |
|-------|-------|--------|
| Strategy | objective, audience | tone, cadence, num_posts, platform mix |
| Content | strategy_plan | captions, hashtags per post per platform |
| Media | posts[] | image_prompt per post |
| Compliance | posts[] | compliance_passed flag + issues list |
| Scheduling | num_posts, timezone | scheduled_at timestamps |
| Persist | posts[] | writes Post rows to DB |
| Approval Gate | — | interrupt; waits for `approved=True` |
| Publishing | posts[] | calls Ayrshare, stores social_post_id |

**Trade-off:** All agents call OpenAI sequentially within a single `invoke()`. Parallelising the content/media/compliance agents with `Send()` would reduce latency but adds complexity. Defer until p50 generation time exceeds user tolerance.

---

### 3.4 Publishing & Scheduler

**`publish_service.safe_publish_post(post, session)`** is the atomic publish unit:

1. Acquire optimistic lock (`is_locked=True`, `lock_timestamp=now`)
2. Transition post status to `publishing`
3. Call Ayrshare; on success store `social_post_id`, set `published`
4. On failure: increment `publish_attempts`; if below `max_attempts`, set `next_publish_attempt_at = now + back_off`; else set `failed`
5. Release lock

**`scheduler.publish_due_posts()`** is called on a background timer (APScheduler or FastAPI lifespan loop):

```
SELECT posts WHERE status='approved'
               AND scheduled_at <= now
               AND is_locked=False
               AND (next_publish_attempt_at IS NULL OR next_publish_attempt_at <= now)
```

**Idempotency:** Each post gets an `idempotency_key` (UUID) before the first publish attempt. The Ayrshare client sends this as `X-Idempotency-Key`; duplicate calls return the original response without creating a second post.

**Trade-off:** The scheduler runs in the same process as the API. Under load, a burst of due posts will starve API requests. At scale, move the scheduler to a separate worker (Celery + Redis, or a standalone `python -m scheduler` process).

---

### 3.5 Ayrshare Integration

```
User connects accounts:
  POST /social/connect
    → ayrshare.create_user_profile(email)
    → store profile_key in User.ayrshare_profile_key
    → SSO JWT linked to that profile

At publish time:
  ayrshare.schedule_post(profile_key, platforms, caption, media_url, scheduled_time)
    → returns { id, status, postIds: { instagram: "...", ... } }
    → stored in Post.social_post_id + Post.platform_response

Analytics refresh:
  ayrshare.get_post_analytics(social_post_id)
    → stored in Post.{likes, comments, shares, impressions, engagement_rate}
```

**Platform normalisation:** `coerce_ayrshare_platforms()` maps internal platform names (e.g. `"instagram"`) to Ayrshare's expected identifiers, and strips platforms the user hasn't connected.

**Trade-off:** All social publishing routes through Ayrshare. This is a single vendor dependency. The abstraction is in `integrations/ayrshare.py` + `services/ayrshare_service.py`, so swapping to a direct platform SDK later requires only those two files.

---

### 3.6 Frontend

Seven server-rendered HTML pages served by FastAPI `StaticFiles`:

| Page | Role |
|------|------|
| `index.html` | Marketing landing page |
| `login.html` / `signup.html` | Auth forms; JWT stored in `localStorage` |
| `dashboard.html` | Campaign list; create new |
| `wizard.html` | Multi-step campaign creation (objective → audience → platforms) |
| `review.html` | Post review + inline editing before publish |
| `connect.html` | Link social accounts via Ayrshare |
| `analytics.html` | Engagement metrics per campaign |

All pages communicate with the API via `fetch()` with `Authorization: Bearer <token>`. No SPA framework, no build step — deploy is a static file copy.

**Trade-off:** Vanilla JS is fast to ship but painful to maintain past ~5 pages. When the team grows, migrating to React/Next.js is the right move. The API is already fully decoupled from the frontend, so this is a frontend-only change.

---

## 4. API Contract Summary

### Auth
```
POST /auth/signup       { email, password }           → { token }
POST /auth/login        { email, password }           → { token }
GET  /auth/me           (JWT)                         → User
```

### Campaigns
```
POST /campaigns         { name, objective, audience, platforms }  → Campaign
GET  /campaigns         (JWT)                         → Campaign[]
GET  /campaigns/{id}    (JWT)                         → Campaign + Posts
POST /campaigns/{id}/generate  (JWT)                  → { status, posts[] }
POST /campaigns/{id}/publish   (JWT)                  → { status }
```

### Posts
```
GET  /campaigns/{id}/posts  (JWT)   → Post[]
PUT  /posts/{id}            (JWT)   → Post (edit caption, hashtags, schedule)
POST /posts/{id}/approve    (JWT)   → Post (role: admin/owner)
```

### Social
```
POST /social/connect    { platforms }  (JWT)  → { profile_key }
GET  /social/status     (JWT)                 → { connected, platforms[] }
```

### Analytics
```
GET  /analytics/{campaign_id}   (JWT)  → AnalyticsSummary
POST /analytics/refresh         (JWT)  → { updated: N }
```

### Teams
```
POST /teams             { name, account_type }  (JWT)  → Team
POST /teams/invite      { email, role }         (JWT)  → TeamInvite
POST /teams/join/{token}                         (JWT)  → User (updated team_id)
```

---

## 5. Infrastructure & Deployment

```
GitHub
  └── push to main
        └── Render auto-deploy
              └── Docker build
                    └── python -m uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

**Dockerfile highlights:**
- Base: `python:3.11-slim`
- Install deps: `pip install -r requirements.txt`
- Copy source; expose `$PORT`

**`render.yaml`:**
- Service type: `web`
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn backend.main:app ...`
- Env vars: `OPENAI_API_KEY`, `AYRSHARE_API_KEY`, `JWT_SECRET_KEY`, `DATABASE_URL`

**Persistence on Render:** SQLite files live on the Render instance disk, which is ephemeral on the free tier. For production, set `DATABASE_URL` to a Render PostgreSQL instance and `CHECKPOINT_DB_URL` accordingly.

---

## 6. Scale & Reliability

### Current Bottlenecks

| Bottleneck | Impact | Mitigation Path |
|------------|--------|-----------------|
| LangGraph runs in-process (30–60 s) | Ties up a Uvicorn thread; blocks other requests | Move to async task queue (Celery + Redis or ARQ) |
| Scheduler runs in-process | Competes with API threads during bulk publish | Separate worker process |
| SQLite single-file | No concurrent writes; 1 writer at a time | Migrate to PostgreSQL (just change DATABASE_URL) |
| No connection pooling | N/A for SQLite; needed when moving to Postgres | SQLModel/SQLAlchemy pool config |
| Single Render instance | No horizontal scaling | Add load balancer; move DB and checkpoints to managed services |

### Reliability Features Already in Place

- **Retry with back-off** on publish failures (`publish_attempts`, `next_publish_attempt_at`)
- **Idempotency keys** prevent double-posts on retry
- **Optimistic locking** on posts prevents concurrent scheduler races
- **Persistent LangGraph checkpoints** survive server restarts
- **Compliance gate** before any post can be published
- **Human approval gate** via `interrupt_before=publishing`

### Monitoring Gaps (To Add)

- No structured error telemetry (Sentry / Datadog)
- No health-check endpoint `/healthz` for load balancers
- No rate-limiting on auth endpoints (brute-force risk)
- No alerting on Ayrshare publish failures

---

## 7. Security

| Concern | Current Approach | Gap |
|---------|-----------------|-----|
| Password storage | bcrypt hash | ✅ Solid |
| Token auth | JWT (python-jose) | ⚠️ No refresh token; short expiry needed |
| Secret management | `.env` / Render env vars | ✅ Never committed |
| RBAC | `check_permission()` helper | ⚠️ Not enforced on every route yet |
| SQL injection | SQLModel ORM (parameterised) | ✅ Safe |
| CORS | `CORSMiddleware` configured | ⚠️ Review allowed origins for prod |
| Rate limiting | None | ❌ Add on `/auth/login` and `/generate` |
| Ayrshare profile keys | Stored in User row (plaintext) | ⚠️ Consider encrypting at rest |

---

## 8. Trade-Off Summary

| Decision | Chosen | Alternative | Why |
|----------|--------|-------------|-----|
| Architecture | Monolith | Microservices | Simpler ops, faster MVP; clean services/ boundary enables future split |
| Database | SQLite | PostgreSQL | Zero-ops for MVP; `DATABASE_URL` swap is the migration path |
| Frontend | Vanilla JS | React / Next.js | No build toolchain; fine for ≤10 pages |
| AI orchestration | LangGraph in-process | Celery + separate worker | Single-process simplicity; swap when latency becomes an issue |
| Social publishing | Ayrshare only | Direct platform SDKs | Saves 3–4 separate OAuth integrations; locked to one vendor |
| Checkpointing | SQLite (SqliteSaver) | MemorySaver | Survives restarts; low overhead |
| Approval flow | Human-in-the-loop interrupt | Auto-approve | Compliance and brand control; adds one round-trip |

---

## 9. What to Revisit as the System Grows

1. **Replace SQLite with PostgreSQL** before going beyond a handful of concurrent users. Set `DATABASE_URL` and `CHECKPOINT_DB_URL` — no code changes needed.
2. **Move LangGraph to an async task queue** (ARQ or Celery) when generation latency starts blocking API requests. Return a `task_id` from `/generate` and poll or use WebSockets for progress.
3. **Add a refresh token flow** to avoid forcing users to re-login when the access token expires.
4. **Instrument with Sentry** for error tracking and add `/healthz` for Render health checks.
5. **Enforce RBAC on every route** — audit `main.py` routes that currently call services without `require_permission()`.
6. **Parallelise Content + Media + Compliance agents** in LangGraph using `Send()` to cut per-campaign generation time.
7. **Add a proper job queue for the scheduler** so it doesn't compete with API workers on burst publish days.
