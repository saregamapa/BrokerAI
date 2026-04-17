# S4-01 + S4-03 — Mobile-First Design Spec
**Sprint:** 4 | **Owner:** Sasha Kim (Design) | **Status:** Complete

---

## Breakpoints (Tailwind)
| Token | Width | Use |
|-------|-------|-----|
| *(default)* | 0–639px | Mobile portrait — primary mobile layout |
| `sm:` | 640–767px | Mobile landscape / small tablet |
| `md:` | 768–1023px | Tablet |
| `lg:` | 1024px+ | Desktop (current default) |

---

## S4-01 — Mobile Wizard (wizard-v2.html)

### Layout Changes

**Step Card Container**
- Mobile: `flex-col`, full-width cards, `px-4 py-6`
- Desktop `lg:` stays at current grid

**Progress Step Indicator**
- Mobile: compressed — show only current step number + title (hide other step labels)
  e.g. "Step 2 of 6 — Choose Platforms"
- Desktop: full horizontal stepper (current behavior)

**Action Bar (Back / Next / Generate)**
- Mobile: `fixed bottom-0 left-0 right-0` dock bar, `z-40`
  - `bg-white border-t border-slate-200 px-4 py-3 flex gap-3`
  - Back button: `flex-1` secondary outlined
  - Next/Generate button: `flex-2` primary amber filled
  - Add `pb-safe` equivalent (`padding-bottom: env(safe-area-inset-bottom)`) for notch devices
- Desktop: inline within step card (current behavior)

**Platform Checkboxes (Step 2)**
- Mobile: 2-column grid `grid-cols-2`
- Desktop: 4-column grid `grid-cols-4`

**Audience Chips (Step 3)**
- Mobile: horizontal scroll (`overflow-x-auto flex flex-nowrap gap-2 pb-2`)
- Desktop: `flex-wrap`

**Content Sliders (Step 5)**
- Mobile: stacked vertically with larger touch targets (`h-6` slider thumb)
- Desktop: grid layout (current)

**Post Preview Cards (Step 6)**
- Mobile: full-width single column, `w-full`
- Desktop: 3-column grid (current)

**AI Assistant Sidebar**
- Mobile: hidden by default, accessible via floating action button (FAB) bottom-right `📤`
- FAB: `fixed bottom-20 right-4 z-50 w-12 h-12 rounded-full bg-amber-500 shadow-lg`
- When FAB tapped: slide-in panel from bottom (sheet) covering 70% of screen height
- Desktop: visible sidebar (current)

---

## S4-03 — Mobile Dashboard (dashboard.html)

### Navigation: Sidebar → Bottom Nav

**Mobile (< lg):** Hide sidebar (`hidden lg:flex`), show bottom navigation bar
```
Bottom nav: fixed bottom-0, z-50, w-full, bg-white, border-t
5 tabs: 🏠 Home | 📅 Calendar | ✨ Campaigns | 📊 Analytics | ⚙️ Settings
Active tab: amber text + amber indicator dot above icon
Height: 64px + safe-area-inset-bottom
```

**Desktop (lg+):** Sidebar visible (current), bottom nav hidden

### Calendar → List View on Mobile

**Mobile:** Replace 7-column calendar grid with a chronological list view
```
List item: date badge (amber pill) | platform emoji | truncated content | time chip
Grouped by date (today, tomorrow, "Wed Apr 20", etc.)
No interact.js drag-drop on mobile (touch events conflict) — show "📅 Tap to reschedule" instead
```

**Desktop:** Full 7-column calendar grid (current)

Toggle button: "📅 Calendar view | ≡ List view" — desktop only

### Stats Cards
- Mobile: 2×2 grid (`grid-cols-2 gap-3`)
- Desktop: 4×1 row (`grid-cols-4`)

### Publishing Today
- Mobile: vertical stack, full-width cards (no horizontal scroll)
- Desktop: horizontal scroll row (current)

### Topnav
- Mobile: hide breadcrumb / page title; show only logo + notification bell + avatar
- Desktop: full topnav (current)

### Onboarding Checklist
- Mobile: collapsible by default (saves vertical space), expand on tap
- Desktop: expanded by default (current)

---

## Touch Target Standards
- Minimum tap target: 44×44px on all interactive elements
- Button padding on mobile: minimum `py-3 px-4`
- Input fields: `text-base` (16px) to prevent iOS zoom on focus

## Safe Area Handling
```css
/* Add to all fixed bottom elements */
padding-bottom: max(12px, env(safe-area-inset-bottom));
```

## Typography Scale (Mobile)
- Page headings: `text-xl` (mobile) → `text-2xl` (desktop)
- Card titles: `text-base` → `text-lg`
- Body: `text-sm` stays consistent

---

## Acceptance Criteria
- [ ] Wizard usable one-handed on a 375px wide screen (iPhone SE)
- [ ] Action bar never covers content (scroll area accounts for bottom nav height)
- [ ] Calendar shows as list on screens narrower than 768px
- [ ] Bottom nav highlights active page correctly
- [ ] No horizontal overflow at 320px width (oldest supported Android)
- [ ] Touch targets ≥ 44px on all interactive elements
- [ ] Fixed bottom elements respect safe-area-inset-bottom (notch devices)
