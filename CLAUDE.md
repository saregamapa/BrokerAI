# BrokerAI — AI-Powered Social Media Campaign SaaS

## 🧠 Project Overview

BrokerAI is a SaaS platform that allows users (real estate brokers, agents, and marketers) to generate, schedule, and publish AI-powered social media campaigns.

The system uses an agentic workflow powered by LangGraph to:
1. Generate strategy
2. Create content
3. Generate media prompts
4. Run compliance checks
5. Schedule posts
6. Publish via Ayrshare

The product is a **single FastAPI monolith** that serves both:
- REST API
- Static frontend (HTML/CSS/JS)

---

## 🏗️ Tech Stack

### Backend
- FastAPI
- Uvicorn
- SQLModel (ORM)
- SQLite (default, switchable via DATABASE_URL)
- Python-JOSE (JWT auth)
- Bcrypt (password hashing)

### AI Layer
- LangGraph (multi-step agent workflows)
- LangChain
- LangChain OpenAI
- OpenAI API (GPT-4 class models)

### Integrations
- Ayrshare API (social publishing + OAuth via JWT)

### Frontend
- Vanilla HTML, CSS, JS
- Served via FastAPI StaticFiles

### DevOps
- Docker
- Render (deployment)
- python-dotenv

### Testing
- Pytest

---

## 📁 Project Structure
brokerai/
│
├── app/
│ ├── main.py
│ ├── config.py
│ ├── database.py
│ ├── models/
│ ├── schemas/
│ ├── routes/
│ ├── services/
│ ├── agents/
│ ├── utils/
│
├── frontend/
│ ├── index.html
│ ├── login.html
│ ├── signup.html
│ ├── dashboard.html
│ ├── campaign.html
│ ├── review.html
│ ├── connect.html
│ ├── analytics.html
│ ├── calendar.html
│ ├── css/
│ ├── js/
│
├── tests/
│
├── Dockerfile
├── render.yaml
├── requirements.txt
├── .env.example
└── claude.md


---

## 🔐 Authentication System

### Requirements
- JWT-based authentication
- Password hashing using bcrypt
- Token expiration support

### API Endpoints
- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me`

### Rules
- Never store plain passwords
- JWT must include user_id
- Protect all campaign routes

---

## 🧩 Database Models (SQLModel)

### User
- id
- email
- hashed_password
- created_at

### Campaign
- id
- user_id
- name
- objective
- target_audience
- status (draft, generated, scheduled, published)
- created_at


---

## 🔐 Authentication System

### Requirements
- JWT-based authentication
- Password hashing using bcrypt
- Token expiration support

### API Endpoints
- `POST /auth/signup`
- `POST /auth/login`
- `GET /auth/me`

### Rules
- Never store plain passwords
- JWT must include user_id
- Protect all campaign routes

---

## 🧩 Database Models (SQLModel)

### User
- id
- email
- hashed_password
- created_at

### Campaign
- id
- user_id
- name
- objective
- target_audience
- status (draft, generated, scheduled, published)
- created_at

### Post
- id
- campaign_id
- platform (instagram, linkedin, etc.)
- content
- media_prompt
- scheduled_time
- status

### SocialAccount
- id
- user_id
- platform
- ayrshare_profile_key

---

## 🤖 AI Workflow (LangGraph)

### Graph Flow
START
↓
Strategy Agent
↓
Content Agent
↓
Media Agent
↓
Compliance Agent
↓
Scheduling Agent
↓
Publish Agent
↓
END


---

## 🧠 Agent Definitions

### 1. Strategy Agent
Input:
- campaign objective
- target audience

Output:
- campaign strategy
- tone
- posting frequency

---

### 2. Content Agent
Generates:
- captions
- hashtags
- platform-specific variations

---

### 3. Media Agent
Generates:
- image prompts (for DALL·E or similar tools)

---

### 4. Compliance Agent
Checks:
- real estate ad compliance
- removes misleading claims

---

### 5. Scheduling Agent
Generates:
- optimal posting times

---

### 6. Publish Agent
- Sends posts to Ayrshare API
- Stores post IDs

---

## 🔌 Ayrshare Integration

### Features
- Social account linking
- Scheduled publishing
- Multi-platform support

### Required Endpoints
- `POST /social/connect`
- `POST /publish`

### Notes
- Store Ayrshare profile_key per user
- Use JWT SSO for authentication

---

## 🌐 Frontend Pages

### 1. Login / Signup
- Form-based auth
- Store JWT in localStorage

### 2. Dashboard
- List campaigns
- Create new campaign

### 3. Campaign Wizard
Steps:
1. Enter objective
2. Select audience
3. Choose platforms

### 4. Review Page
- Show generated posts
- Allow edits before publishing

### 5. Connect Page
- Link social accounts via Ayrshare

### 6. Analytics Page
- Show engagement metrics (mock initially)

### 7. Calendar Page
- Show scheduled posts

---

## 🔁 API Design

### Campaigns
- `POST /campaigns`
- `GET /campaigns`
- `GET /campaigns/{id}`

### AI Generation
- `POST /campaigns/{id}/generate`

### Posts
- `GET /campaigns/{id}/posts`
- `PUT /posts/{id}`

### Publish
- `POST /campaigns/{id}/publish`

---

## ⚙️ Configuration

Use `.env` — copy `.env.example` to `.env` and set real values locally. **Never commit API keys or tokens.**

Examples (placeholders only):

- `OPENAI_API_KEY=your_key_here`
- `DATABASE_URL=sqlite:///./brokerai.db`
- `AYRSHARE_API_KEY=` (from Ayrshare dashboard)
- `JWT_SECRET_KEY=` (long random string for JWT signing)


---

## 🐳 Docker Setup

- Use Python 3.11
- Install dependencies
- Run Uvicorn

---

## ☁️ Render Deployment

- Use `render.yaml`
- Web service
- Auto deploy from GitHub

---

## 🧪 Testing (Pytest)

### Must Cover
- Auth routes
- Campaign creation
- AI pipeline execution
- Publish flow

---

## ⚡ Performance Rules

- Async endpoints where possible
- Cache AI responses (optional)
- Avoid blocking calls

---

## 🚨 Guardrails

- Handle OpenAI failures gracefully
- Retry logic for Ayrshare API
- Validate all inputs

---

## 🎯 Definition of Done

The system is complete when:
1. User can sign up and log in
2. Create a campaign
3. Generate AI posts
4. Review/edit posts
5. Connect social account
6. Schedule and publish posts
7. View campaigns in dashboard

---

## 🧠 Instructions for Claude (Execution Mode)

You are building a production-ready SaaS.

Rules:
- Write clean, modular Python code
- Use services layer for business logic
- Keep routes thin
- Follow folder structure strictly
- Do NOT skip steps
- Always ensure code runs

Execution Plan:
1. Setup project structure
2. Implement auth system
3. Setup database models
4. Build campaign APIs
5. Implement LangGraph workflow
6. Integrate OpenAI
7. Integrate Ayrshare
8. Build frontend pages
9. Add Docker + Render config
10. Write tests

Deliver working code at each step.
Do not leave placeholders.