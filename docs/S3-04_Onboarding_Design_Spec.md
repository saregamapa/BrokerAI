# S3-04 — Onboarding Checklist & Product Tour Design Spec

**Sprint:** 3 | **Owner:** Sasha (UX Designer) | **Status:** Complete

---

## Overview

BrokerAI's onboarding system guides new users from signup to their first published post through two complementary layers:

1. **Persistent Checklist Card** (dashboard) — 3-step progress tracker
2. **Guided Product Tour** (Shepherd.js) — 7-step interactive walkthrough

Both are dismissible, localStorage-gated (never shown again once completed/dismissed), and non-blocking.

---

## 1. Onboarding Checklist Card

### Trigger Conditions
- Shown on dashboard load when `localStorage.brokerai_onboarding_dismissed !== "1"`
- Auto-hides permanently when all 3 steps complete (3-second success banner → fade out)
- Dismissed via ✕ button → sets `localStorage.brokerai_onboarding_dismissed = "1"`

### Visual Design
- **Position:** Top of main content area, above "Publishing Today" section
- **Card:** `rounded-xl border border-amber-200 bg-white shadow-sm p-4`
- **Header row:** Rocket emoji + "Get started with BrokerAI" + chevron collapse button + ✕ dismiss
- **Progress bar:** Thin amber gradient bar, "N of 3 steps complete" label
- **Collapsed state:** Header only (chevron rotates −90°, saves vertical space after step 1+)

### Steps

| # | Icon | Title | Action | Completion Check |
|---|------|-------|--------|-----------------|
| 1 | 🔗 | Connect your social account | `/connect.html` | `GET /social-status → connected: true` |
| 2 | 📝 | Create your first campaign | `/wizard-v2.html` | `GET /campaigns → length > 0` |
| 3 | 👥 | Invite your team | `/settings.html#team` | `localStorage.brokerai_team_invited === "1"` OR `GET /team → members > 1` |

### Step State Styling
- **Pending:** Gray circle (○), normal text, amber arrow link button
- **Complete:** Green filled checkmark (✓), line-through text (text-slate-400), link hidden
- **All complete:** Green "You're all set! 🎉" banner → auto-dismiss after 3s

### Accessibility
- Collapse button: `aria-expanded`, `aria-controls="onboarding-checklist-body"`
- Dismiss button: `aria-label="Dismiss onboarding checklist"`
- Step links: descriptive `aria-label` with destination context

---

## 2. Guided Product Tour (Shepherd.js v11.2.0)

### Trigger Conditions
- Runs on dashboard load after all data has rendered
- Skipped if `localStorage.brokerai_tour_done === "1"`
- Sets `brokerai_tour_done = "1"` on complete OR cancel

### Tour Configuration
```js
new Shepherd.Tour({
  useModalOverlay: true,      // darkens page behind highlighted element
  defaultStepOptions: {
    scrollTo: { behavior: 'smooth', block: 'center' },
    cancelIcon: { enabled: true },  // X button on every step
  }
})
```

### Color Theme (BrokerAI Palette Override)
| Element | Value |
|---------|-------|
| Header background | `#0f172a` (slate-900) |
| Header text | `#ffffff` |
| Primary button (Next/Done) | `#f59e0b` amber bg, `#0f172a` text |
| Secondary button (Back/Skip) | Transparent, `#64748b` slate text |
| Border radius | `12px` |
| Z-index | `9999` (above loading overlay) |

### Tour Steps

| # | Target Selector | Placement | Copy |
|---|----------------|-----------|------|
| 1 | *(center modal)* | — | 👋 Welcome to BrokerAI! Let's take a 60-second tour. |
| 2 | `#app-sidebar` | right | 📌 The sidebar is your home base — campaigns, analytics, settings. |
| 3 | `a[href="/wizard-v2.html"]` | bottom | ⚡ Click here to launch the AI campaign wizard — takes ~2 minutes. |
| 4 | `#publishing-today-section` | top | 📅 Your daily publishing queue — all posts going out today. |
| 5 | `#analytics-bar` | top | 📊 Key metrics at a glance — posts published, success rate, engagement. |
| 6 | `#onboarding-checklist-container` | top | ✅ Follow these 3 steps to finish setting up your account. |
| 7 | *(center modal)* | — | 🎉 You're ready! Create your first campaign and start publishing. |

### Step Navigation Buttons
- Steps 1–6: **Back** (secondary) + **Next →** (primary)
- Step 1: **Skip tour** (secondary, cancels) + **Next →** (primary)  
- Step 7: **← Back** + **Let's go! →** (primary, completes tour)

---

## 3. User Journey Flowchart

```
New User Signup
      ↓
Dashboard Load
      ↓
┌─────────────────────────────────┐
│  brokerai_tour_done === "1"?    │
│  NO → Run 7-step Shepherd tour  │
│  YES → Skip tour                │
└─────────────────────────────────┘
      ↓
┌──────────────────────────────────────┐
│  brokerai_onboarding_dismissed="1"?  │
│  NO → Show checklist card            │
│     → Check 3 step completions       │
│     → Update progress bar            │
│  YES → Hidden                        │
└──────────────────────────────────────┘
      ↓
User completes step 1 (Connect social)
      ↓
Progress bar: 1/3 ████░░░░
      ↓
User creates first campaign (Step 2)
      ↓
Progress bar: 2/3 ████████░░
      ↓
User invites team (Step 3)
      ↓
Progress bar: 3/3 ████████████
      ↓
"You're all set! 🎉" banner (3s)
      ↓
Checklist auto-dismisses permanently
```

---

## 4. Technical Implementation Notes

### LocalStorage Keys
| Key | Value | Purpose |
|-----|-------|---------|
| `brokerai_tour_done` | `"1"` | Prevents tour repeat |
| `brokerai_onboarding_dismissed` | `"1"` | Hides checklist permanently |
| `brokerai_onboarding_v1` | JSON `{collapsed: bool}` | Persists collapsed state |
| `brokerai_team_invited` | `"1"` | Set by settings page on invite |

### API Calls (all parallel via Promise.allSettled)
- `GET /social-status` → step 1 check
- `GET /campaigns` → step 2 check  
- `GET /team` (graceful 404) → step 3 check

### Dependencies
- Shepherd.js 11.2.0 (CDN, dashboard.html only)
- No additional npm packages required

---

## 5. Acceptance Criteria

- [ ] New user sees tour on first dashboard load
- [ ] Tour does not re-run after completion or dismissal
- [ ] Each tour step correctly highlights its target element
- [ ] Checklist shows 0/3 for a fresh account
- [ ] Checklist updates to 1/3 after social connection (on next page load)
- [ ] Checklist updates to 2/3 after first campaign created
- [ ] All-complete state shows success banner and auto-hides
- [ ] Dismiss ✕ permanently hides checklist
- [ ] Collapsed state persists across page reloads
- [ ] All checks handle API errors gracefully (treat as incomplete)
