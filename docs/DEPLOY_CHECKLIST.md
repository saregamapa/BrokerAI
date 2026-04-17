# BrokerAI — Production Deploy Checklist

> Run this checklist for EVERY production deployment. No exceptions.
> Last updated: 2026-04-17

---

## Pre-Deploy (T-60 min)

### Environment Audit
- [ ] Run `python scripts/pre_deploy_check.py` — all ❌ FAILED items resolved
- [ ] `JWT_SECRET_KEY` is ≥ 32 random chars (not default/dev value)
- [ ] `DATABASE_URL` points to production PostgreSQL (not SQLite)
- [ ] `OPENAI_API_KEY` has sufficient credits for expected traffic
- [ ] `SENTRY_DSN` is set — verify in Sentry dashboard that events are flowing
- [ ] `REDIS_URL` is set (Render Redis add-on or external)
- [ ] `AYRSHARE_WEBHOOK_SECRET` is set and matches Ayrshare dashboard value
- [ ] `STRIPE_WEBHOOK_SECRET` is set and matches Stripe CLI/dashboard value

### Code Review
- [ ] PR merged to `main` — no open, blocking PRs
- [ ] All CI checks green (GitHub Actions `build-and-test` workflow)
- [ ] `pytest tests/ -q` passes locally (191+ tests)
- [ ] No `TODO: fix before prod` comments remaining in changed files
- [ ] No `.env` files committed accidentally (`git log --oneline -5`)

### Database Migration
- [ ] Run migration dry-run: `python -c "from backend.db import create_db_and_tables; create_db_and_tables(); print('Migration OK')"`
- [ ] Verify no data-loss migrations (all new columns have `default=None` or explicit defaults)
- [ ] New models confirmed: `Notification`, `ContentItem`, `AuditEvent` tables will auto-create
- [ ] Soft-delete columns (`deleted_at`) added non-destructively

---

## Deploy (T-0)

- [ ] Create DB backup (Render → Database → Backups → Manual backup)
- [ ] Trigger deploy: `git push origin main` or Render Dashboard → Manual Deploy
- [ ] Monitor Render build logs — confirm no install/compile errors
- [ ] Watch Sentry for new errors during deploy (first 5 min)

---

## Post-Deploy Smoke Tests (T+5 min)

Run these against production URL:

```bash
PROD=https://your-app.onrender.com

# 1. Health check
curl -s $PROD/health | python -m json.tool
# Expected: {"status": "ok", "db": "ok", ...}

# 2. Readiness
curl -s $PROD/health/ready
# Expected: {"ready": true}

# 3. Signup (new test user)
curl -s -X POST $PROD/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"smoketest+$(date +%s)@brokerai.test","password":"SmokeTest123!"}' | python -m json.tool
# Expected: {"access_token": "...", "token_type": "bearer"}

# 4. Login with existing account
# (use a known staging user)

# 5. Campaign list (authenticated)
curl -s -H "Authorization: Bearer $TOKEN" $PROD/campaigns | python -m json.tool
# Expected: {"campaigns": [...]}
```

Or run the automated script:
```bash
./scripts/smoke_test.sh https://your-app.onrender.com
```

- [ ] `/health` returns `{"status": "ok"}`
- [ ] `/health/ready` returns `{"ready": true}`
- [ ] Auth signup + login flow works end-to-end
- [ ] Dashboard loads without JS errors (open browser DevTools)
- [ ] Notification bell appears and polls correctly
- [ ] At least one campaign visible for seed user

---

## Rollback Plan

### If deploy fails (build errors):
1. Render auto-reverts to previous deploy — no action needed
2. Check build logs: Render Dashboard → Service → Deploys → [failed deploy]
3. Fix the issue, push a new commit

### If deploy succeeds but app is broken (T+5 smoke tests fail):
1. Render Dashboard → Service → Deploys → [previous successful deploy] → **Rollback**
2. Takes ~2 min to revert
3. Post incident notification to team: "Rolling back to [commit hash]"
4. Create GitHub Issue with "P0 - Production Regression" label

### If DB migration is bad:
1. Rollback the code (step above)
2. If new tables were created: they're empty, safe to drop
3. If columns were added: `ALTER TABLE ... DROP COLUMN ...` (run via Render SQL console)
4. If data was modified: restore from pre-deploy backup (Render → Database → Backups)

### Emergency contacts:
- Render support: https://render.com/support
- OpenAI status: https://status.openai.com
- Ayrshare status: https://status.ayrshare.com
- Sentry oncall: Check Sentry → Alerts → Oncall schedule

---

## Post-Deploy Monitoring (T+24h)

- [ ] Sentry error rate < 1% of requests
- [ ] Render response time P95 < 2000ms (check Metrics tab)
- [ ] No failed publish jobs in the last hour (check logs for `publish_failed`)
- [ ] Redis hit rate > 30% for campaign list endpoint (optional)
- [ ] No spike in support emails/tickets

---

## Env Var Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | ✅ | OpenAI API key for all AI generation |
| `JWT_SECRET_KEY` | ✅ | ≥32 char random secret for JWT signing |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `AYRSHARE_API_KEY` | ✅ | Ayrshare API key for social publishing |
| `RESEND_API_KEY` | ⚠️ | Resend key (emails disabled without it) |
| `FROM_EMAIL` | ⚠️ | Sender email (e.g. noreply@brokerai.com) |
| `SENTRY_DSN` | ⚠️ | Sentry DSN (error tracking disabled without it) |
| `REDIS_URL` | ⚠️ | Redis URL (caching disabled without it) |
| `GOOGLE_CLIENT_ID` | ⚠️ | Google OAuth2 (SSO disabled without it) |
| `GOOGLE_CLIENT_SECRET` | ⚠️ | Google OAuth2 client secret |
| `GOOGLE_REDIRECT_URI` | ⚠️ | Must match Google Console exactly |
| `STRIPE_SECRET_KEY` | ⚠️ | Stripe billing (billing disabled without it) |
| `STRIPE_WEBHOOK_SECRET` | ⚠️ | Stripe webhook HMAC verification |
| `AYRSHARE_WEBHOOK_SECRET` | ⚠️ | Ayrshare webhook verification |
| `APP_VERSION` | ℹ️ | Version string shown in /health |
| `ENVIRONMENT` | ℹ️ | production/staging/development |
| `SENTRY_TRACES_RATE` | ℹ️ | Sentry performance sample rate (default: 0.1) |
