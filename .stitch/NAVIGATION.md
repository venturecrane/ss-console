---
spec-version: 1
design-md-sha: absent
stitch-project-id: '17873719980790683333'
phase-0-compliance:
  categorical: '93%'
  strict: '87%'
  date: '2026-04-15'
enforcement: 'injection-first + required validator (nav-spec/validate.py)'
approval-state: 'approved (retrofit landed in PR feat/nav-spec-v1)'
---

# SMD Services — Navigation Specification

Single source of truth for navigation chrome across `smd.services`, `admin.smd.services`, and `portal.smd.services`. Every Stitch-generated screen and every new shipped component must conform. Deviations require a spec-version bump, not a one-off exception.

Companion skill: `~/.agents/skills/nav-spec/`. Spec is consumed by `stitch-design` (via NAV CONTRACT injection) and `stitch-ux-brief` (Phase 7 concept template, Phase 11 strip directive). Post-generation enforcement via `nav-spec/validate.py`.

**v1 status:** spec is authored; approval is held on the refactor checklist in Section 11. Validator is in injection-first + belt-and-suspenders mode per Phase 0 compliance evidence (see `nav-spec/examples/phase-0-compliance-report.md`).

---

## 1. Information architecture

### Sitemap (by subdomain)

**`smd.services`** (marketing and auth entry — served from apex)

- `/` — marketing home (public, dashboard archetype)
- `/contact` — contact form (public, form)
- `/book` — booking flow (public, form)
- `/book/manage/` — query-param redirect handler; renders an inline "invalid link" fallback when no token (public, error)
- `/book/manage/[token]` — **token-auth**, manage existing booking (token-auth, detail)
- `/get-started` — onboarding CTA page (public, dashboard)
- `/scorecard` — self-serve assessment (public, wizard)
- `/contact` — contact form (public, form)
- `/404` — shared not-found page (renders in all subdomain contexts; see "Cross-subdomain 404" in Section 7)
- `/auth/login` — admin OAuth entry (**auth-gate**, form)
- `/auth/portal-login` — client magic-link request (**auth-gate**, form)
- `/auth/verify` — magic-link verification (**auth-gate**, transient)

**`admin.smd.services`**

- `/admin` — dashboard (session-auth-admin, dashboard)
- `/admin/entities` — client list (session-auth-admin, list)
- `/admin/entities/[id]` — client detail (session-auth-admin, detail)
- `/admin/entities/[id]/quotes/[quoteId]` — quote detail, nested (session-auth-admin, detail)
- `/admin/engagements/[id]` — engagement detail (session-auth-admin, detail) — no `/admin/engagements` list; parent is the client detail
- `/admin/assessments/[id]` — assessment detail (session-auth-admin, detail) — no list; parent is the client detail
- `/admin/follow-ups` — follow-ups list (session-auth-admin, list)
- `/admin/analytics` — analytics dashboard (session-auth-admin, dashboard)
- `/admin/settings/google-connect` — integration settings (session-auth-admin, form)

**`portal.smd.services`**

- `/portal` — dashboard (session-auth-client, dashboard)
- `/portal/invoices` — list (session-auth-client, list)
- `/portal/invoices/[id]` — detail (session-auth-client, detail)
- `/portal/quotes` — list (session-auth-client, list)
- `/portal/quotes/[id]` — detail (session-auth-client, detail)
- `/portal/documents` — index (session-auth-client, list)
- `/portal/engagement` — current engagement summary (session-auth-client, detail)

**Dev-only (exempt from spec enforcement)**

- `/dev/portal-components`, `/dev/portal-states` — internal component preview harnesses, noindex, not Stitch generation targets

### Auth boundary table

| Surface class         | Auth model                                                   | Base URL                                                                        | Session cookie                                        | Redirect on logout                                |
| --------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------- |
| `public`              | None                                                         | `smd.services/*` (default marketing)                                            | None                                                  | n/a                                               |
| `auth-gate`           | Anonymous; surface exists to produce or consume a credential | `smd.services/auth/*`                                                           | None pre-submit; session set on post-success redirect | n/a — surface IS the redirect target after logout |
| `token-auth`          | Signed URL token in path segment                             | `/book/manage/[token]` (and any future `/proposal/[token]`, `/invoice/[token]`) | None                                                  | n/a — link expiry replaces logout                 |
| `session-auth-client` | Client cookie session (portal user)                          | `portal.smd.services/*`                                                         | `__Host-portal_session`                               | `smd.services/auth/portal-login`                  |
| `session-auth-admin`  | Admin cookie session (operator)                              | `admin.smd.services/*`                                                          | `__Host-admin_session`                                | `smd.services/auth/login`                         |

### Deep-link inventory

Every URL reachable from outside the app (email, SMS, saved bookmark):

- `/` and public marketing — organic traffic
- `/book/manage/[token]` — emailed after booking confirmation (signed token, session-less)
- `/portal` and `/portal/*` — arrived via `/auth/portal-login` after a magic-link email
- `/admin` — arrived via `/auth/login` (Google OAuth)

Portal detail pages (`/portal/invoices/[id]`, `/portal/quotes/[id]`) always require session auth in the current architecture. If token-auth landings for these ship later, they must route through a distinct URL (e.g., `portal.smd.services/proposal/[token]`) and render `token-auth` chrome per Appendix B — **not** portal chrome. See "Surface-class selection when subdomain is ambiguous" in Section 2.

---

## 2. Surface-class taxonomy

Classes are modeled by **auth model**, not subdomain. Subdomain is a secondary attribute; two surface classes can coexist on one subdomain (e.g., `portal.smd.services` can host `session-auth-client` dashboards and `token-auth` proposal landings under distinct URL paths).

| Class                 | One-line definition                                                                                   |
| --------------------- | ----------------------------------------------------------------------------------------------------- |
| `public`              | No authentication. Addressed to a stranger or first-time visitor. Marketing, contact, scorecard.      |
| `auth-gate`           | Anonymous; surface exists solely to produce or consume an auth credential. Minimal chrome. `noindex`. |
| `token-auth`          | Signed URL token grants access without an account. User arrived via an emailed link; token expires.   |
| `session-auth-client` | Authenticated client with a portal cookie session. Low-privilege; views their own records.            |
| `session-auth-admin`  | Authenticated operator with an admin cookie session. High-privilege; views all records.               |

### Surface-class selection when subdomain is ambiguous

If the same subdomain hosts multiple surface classes (e.g., a future `portal.smd.services/proposal/[token]`), disambiguation:

1. **Presence of a valid session cookie** is the primary signal. If present → session-auth-client (or admin on admin subdomain). If absent but a URL token is valid → token-auth.
2. **Token-auth on an authenticated subdomain must render distinct chrome** so an authenticated user who refreshes the tab can tell the session expired. Use a visible "Viewing via secure link" badge in the header band plus the Appendix B token-auth chrome (not Appendix C portal chrome).
3. **Middleware enforcement:** `src/middleware.ts` must carve out token-auth paths before session-auth checks; otherwise the middleware redirects token-auth users to login.

---

## 3. Screen archetype taxonomy

10 archetypes. 9 interactive + 1 transient. See `~/.agents/skills/nav-spec/references/archetype-catalog.md` for full per-archetype contracts.

| Archetype   | Allowed surface classes            | Back                                   | Breadcrumbs                  |
| ----------- | ---------------------------------- | -------------------------------------- | ---------------------------- |
| `dashboard` | session-auth-\*, public (landing)  | no                                     | no                           |
| `list`      | session-auth-\*                    | yes → parent                           | session-auth-admin: 2 levels |
| `detail`    | all five                           | yes → parent                           | session-auth-admin: 3 levels |
| `form`      | session-auth-\*, public, auth-gate | cancel+save OR submit-only (auth-gate) | no                           |
| `wizard`    | session-auth-\*, public            | prev/next                              | no                           |
| `empty`     | session-auth-\*, token-auth        | inherit                                | inherit                      |
| `error`     | all five                           | no                                     | no                           |
| `modal`     | session-auth-\*, token-auth        | close                                  | no                           |
| `drawer`    | session-auth-\*, token-auth        | close                                  | no                           |
| `transient` | auth-gate                          | n/a                                    | n/a                          |

`transient` is new: a server-side processing surface with no user-facing chrome (renders only a fallback if the redirect fails). Used by `/auth/verify`.

---

## 4. Chrome component contracts (defaults)

Chrome pieces default to shapes in `~/.agents/skills/nav-spec/references/chrome-component-contracts.md`. Surface-class appendices override.

### Default header band

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-5xl mx-auto h-full flex items-center justify-between px-4 md:px-6">
    <!-- left: per surface class -->
    <!-- right: per surface class -->
  </div>
</header>
```

**Height class placement:** `h-14 md:h-16` MUST be on the `<header>` element, not on an inner wrapper. The validator checks the `<header>`'s class list only. Inner-div `py-*` produces a different computed height and fails validation.

**Width wrapper:** `max-w-5xl mx-auto` lives on the inner `<div>`, inside `<header>`. The outer `<header>` is full-bleed so the bottom border spans the viewport.

### Token acceptance forms

The spec uses literal hex (`#e2e8f0`) in examples. The validator and the Stitch injection accept these equivalent forms for every token:

| Token        | Literal hex | CSS variable                  | Tailwind named color |
| ------------ | ----------- | ----------------------------- | -------------------- |
| Border       | `#e2e8f0`   | `var(--color-border)`         | `slate-200`          |
| Text default | `#475569`   | `var(--color-text-secondary)` | `slate-600`          |
| Text bold    | `#0f172a`   | `var(--color-text-primary)`   | `slate-900`          |
| Primary      | `#1e40af`   | `var(--color-primary)`        | `blue-800`           |
| Focus ring   | `#3b82f6`   | `var(--color-action)`         | `blue-500`           |

Astro components should prefer the CSS-var form. Stitch generations emit literal hex. Validator accepts any.

### Default back affordance (detail archetypes)

```html
<a href="<canonical-parent-url>" aria-label="<parent-label>" class="inline-flex items-center gap-1 w-11 h-11 text-[#475569] hover:text-[#0f172a] active:text-[#0f172a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 rounded-lg">
  <span class="material-symbols-outlined">chevron_left</span>
  <span class="text-[13px] font-medium"><parent-label></span>
</a>
```

Positioned inside `<main>`, above content, never inside `<header>`.

### Breadcrumbs

Admin only, depth ≥ 2.

### Skip-to-main link (all surfaces, no exceptions)

```html
<a
  href="#main"
  class="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] bg-white px-4 py-2 rounded-lg border border-[#e2e8f0] text-[#1e40af] font-medium"
>
  Skip to main content
</a>
```

First element in `<body>`, before `<header>`. `<main id="main">` is the target.

---

## 5. Mobile ↔ desktop transforms

**Single breakpoint for chrome:** `md:` (768px). Content layouts may use `sm:` / `lg:` / `xl:`; **chrome never does, with one named exception (right-rail sticky).**

### Transforms per chrome piece

| Chrome                                 | Mobile (<768)                                                         | Desktop (≥768)                                |
| -------------------------------------- | --------------------------------------------------------------------- | --------------------------------------------- |
| Header height                          | `h-14` (56px) on `<header>`                                           | `md:h-16` (64px) on `<header>`                |
| Header padding x                       | `px-4` on inner div                                                   | `md:px-6` on inner div                        |
| Header max-width                       | full bleed (no wrapper constraint below md)                           | `md:max-w-5xl md:mx-auto` on inner div        |
| Back affordance                        | in-flow, left of content, inside `<main>`                             | same                                          |
| Admin nav tabs (Appendix D)            | `hidden`, collapsed into user menu                                    | `md:flex`, inline                             |
| Admin user menu trigger (Appendix D)   | icon button, right of header                                          | `md:hidden` (email + Sign out visible inline) |
| ActionCard (portal detail, Appendix C) | **above** `<main>` content                                            | inside right rail                             |
| Right rail (portal detail, dashboard)  | stacks below (collapsed)                                              | `md:` (see below)                             |
| Breadcrumbs                            | segment truncation at 24ch                                            | full labels                                   |
| Safe-area bottom                       | `pb-[max(1rem,env(safe-area-inset-bottom))]` on `<main>` or container | n/a                                           |

### Right-rail stickiness (named exception)

The right rail is the **only** chrome piece that may use `lg:` classes. Live shipped portal code uses `md:` for the rail, and the spec ratifies this after reviewing the evidence:

- Permitted class pattern on `<aside>` elements inside `<main>`: `md:w-[340px] md:sticky md:top-20 md:self-start`
- Forbidden on all other chrome elements: `sm:`, `lg:`, `xl:` classes anywhere in `<header>`, `<footer>`, nav elements, or back affordances.

Rail top offset is `md:top-20` (80px), which clears the 64px sticky header + 16px buffer. If header height changes, rail offset must update in lockstep.

---

## 6. State conventions

Pulled from `src/styles/global.css`. Hex values authoritative.

| State                        | Hex                         | CSS var                  | Tailwind name (equivalent) |
| ---------------------------- | --------------------------- | ------------------------ | -------------------------- |
| Primary / active link        | `#1e40af`                   | `--color-primary`        | `blue-800`                 |
| Primary hover                | `#1e3a8a`                   | `--color-primary-hover`  | `blue-900`                 |
| Default text                 | `#475569`                   | `--color-text-secondary` | `slate-600`                |
| Bold text / hover on default | `#0f172a`                   | `--color-text-primary`   | `slate-900`                |
| Disabled text                | `#94a3b8`                   | `--color-text-muted`     | `slate-400`                |
| Border                       | `#e2e8f0`                   | `--color-border`         | `slate-200`                |
| Focus ring                   | `#3b82f6` @ 2px, 2px offset | `--color-action`         | `blue-500`                 |
| Error                        | `#ef4444`                   | `--color-error`          | `red-500`                  |
| Success / paid               | `#10b981`                   | `--color-complete`       | `emerald-500`              |
| Attention                    | `#f59e0b`                   | `--color-attention`      | `amber-500`                |
| Meta (timeline dates)        | `#6366f1`                   | `--color-meta`           | `indigo-500`               |

### Touch-state parity (critical for mobile)

Every `hover:` utility on an interactive element MUST be paired with a matching `focus-visible:` utility (keyboard users) AND an `active:` utility (pressed state on touch). Without `active:`, touch devices exhibit "stuck hover" — a tapped element stays in hover state until another element is tapped.

**Canonical interactive-element class pattern:**

```
text-[#475569] hover:text-[#0f172a] active:text-[#0f172a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2
```

Apply this pattern to every chrome link, button, and CTA.

### Tap target enforcement

Tap target minimum: 44×44px. Every interactive chrome element declares an explicit `min-h-11 min-w-11` (or `w-11 h-11` for icon-only, or `min-h-11` + negative horizontal margins when the ink is text). This is load-bearing; do not omit.

### aria-current

Active nav item carries `aria-current="page"` and the active hex. The validator checks for this on any rendered nav.

---

## 7. Transition contracts

### Back-target resolution

All back buttons and cancel buttons resolve to a **hardcoded canonical URL string**. Never `#`, `javascript:`, or `history.back()`. Token-auth leaves (which have no parent) have **no back button** at all.

### Canonical parents (authoritative; every shipped dynamic route enumerated)

| From                                    | Back target            | Label                                              |
| --------------------------------------- | ---------------------- | -------------------------------------------------- |
| `/portal/invoices/[id]`                 | `/portal/invoices`     | "All invoices"                                     |
| `/portal/quotes/[id]`                   | `/portal/quotes`       | "All quotes"                                       |
| `/portal/engagement`                    | `/portal`              | "Home"                                             |
| `/portal/documents/[id]` (future)       | `/portal/documents`    | "All documents"                                    |
| `/admin/entities/[id]`                  | `/admin/entities`      | "All clients"                                      |
| `/admin/entities/[id]/quotes/[quoteId]` | `/admin/entities/[id]` | "<Client name>"                                    |
| `/admin/engagements/[id]`               | `/admin/entities/[id]` | "<Client name>" (engagement is scoped to a client) |
| `/admin/assessments/[id]`               | `/admin/entities/[id]` | "<Client name>" (same pattern)                     |
| `/admin/settings/google-connect`        | `/admin`               | "Dashboard"                                        |
| `/admin/follow-ups`                     | `/admin`               | "Dashboard"                                        |
| `/admin/analytics`                      | `/admin`               | "Dashboard"                                        |
| `/contact` submit/cancel                | `/`                    | "Home"                                             |
| `/book` cancel                          | `/`                    | "Home"                                             |
| `/get-started`                          | `/`                    | "Home"                                             |
| `/scorecard`                            | `/`                    | "Home"                                             |

No fallback row. Every new dynamic route must add a row during spec revision.

### Modal / drawer close

Esc closes. Click-outside (scrim) closes. X button closes. Focus returns to the triggering element.

### Cross-auth-boundary transitions (full enumeration)

| From                | To                            | Mechanism                                                                 | Clears cookie?                 |
| ------------------- | ----------------------------- | ------------------------------------------------------------------------- | ------------------------------ |
| public              | auth-gate (`/auth/*`)         | `<a href="smd.services/auth/...">` (same origin)                          | no                             |
| auth-gate           | session-auth-client           | 302 to `portal.smd.services/portal` + Set-Cookie                          | sets `__Host-portal_session`   |
| auth-gate           | session-auth-admin            | 302 to `admin.smd.services/admin` + Set-Cookie                            | sets `__Host-admin_session`    |
| session-auth-client | auth-gate (logout)            | form POST to `/api/auth/logout` → 302 to `smd.services/auth/portal-login` | clears `__Host-portal_session` |
| session-auth-admin  | auth-gate (logout)            | form POST to `/api/auth/logout` → 302 to `smd.services/auth/login`        | clears `__Host-admin_session`  |
| session-auth-\*     | public (marketing link click) | `<a>` with target subdomain origin                                        | no                             |
| session-auth-admin  | session-auth-client           | not supported; operator must log out and re-login                         | n/a                            |
| token-auth          | anywhere (via link click)     | `<a>` with explicit origin; token does not follow                         | n/a                            |

All cross-subdomain transitions are **full page loads**; no client-side routing across origins.

### Post-login redirects (also table rows above)

- Client magic-link verify success → 302 `portal.smd.services/portal`
- Admin OAuth success → 302 `admin.smd.services/admin`
- Any auth-gate failure → 302 back to same auth-gate page with `?error=<code>`

### Cross-subdomain 404

`src/pages/404.astro` is a shared not-found page that renders in all three subdomain contexts. Current implementation has no layout, so it ships chrome-less — which is actually acceptable per the spec's `error` archetype rules (minimal header, back-to-safety link). Target link resolves by subdomain:

- On `smd.services` → `/`
- On `portal.smd.services` → `/portal`
- On `admin.smd.services` → `/admin`

Middleware detects host and sets `Astro.locals.homeUrl`; 404 page reads it for the back-to-safety link.

---

## 8. Anti-patterns (forbidden by default)

See `~/.agents/skills/nav-spec/references/anti-patterns.md` for rationale.

| Anti-pattern                                       | Forbidden by default on                            | Exceptions                            |
| -------------------------------------------------- | -------------------------------------------------- | ------------------------------------- |
| Global nav tabs in header                          | public, session-auth-client, token-auth, auth-gate | Admin (see Appendix D.2)              |
| Sidebar / hamburger / drawer as primary nav        | all                                                | none                                  |
| Bottom-tab nav on mobile                           | all                                                | none                                  |
| Sticky-bottom action bar on viewport               | all                                                | See escape hatches below              |
| Footer                                             | auth-gate, session-auth-\*, token-auth             | Public (Appendix A)                   |
| Marketing CTAs on auth surface                     | session-auth-\*, token-auth, auth-gate             | none                                  |
| Testimonials / pull quotes                         | all                                                | Public marketing (explicit in prompt) |
| Hero imagery on auth surface                       | session-auth-\*, token-auth, auth-gate             | Public marketing (explicit)           |
| Real-face photo placeholders                       | all                                                | none; see consultant-block rule below |
| Breadcrumbs on non-admin                           | public, auth-gate, session-auth-client, token-auth | Admin only                            |
| `<nav aria-label="Breadcrumb">` around single link | all                                                | none                                  |
| `fixed top-0` on header                            | all                                                | none; always `sticky top-0`           |
| `backdrop-blur-*` / translucent header bg          | session-auth-\*, token-auth, auth-gate             | Public hero bands (explicit)          |
| Icon before client name in header                  | session-auth-\*, token-auth                        | none                                  |
| Back `href="#"` / `javascript:` / `history.back()` | all                                                | none                                  |

### Sticky-bottom escape hatches (for long forms on mobile)

Sticky-bottom action bars that stick to the viewport (`fixed bottom-0`, `sticky bottom-0` with a `fixed` ancestor) are **forbidden**. For long forms or wizards where the primary CTA would fall below the fold on 390×844:

1. **Primary CTA at the natural end of `<main>`** (inside document flow). Preferred.
2. **Wizard split** — break the form into steps so each step's CTA fits above the fold. Required for forms ≥ 6 fields on mobile.
3. **Duplicate CTA at top and bottom of `<main>`** (both in document flow). Acceptable for public-facing forms (contact, book) where the form length is outside the designer's control.
4. **`sticky bottom-0` scoped to a scrollable container inside `<main>`** — permitted only inside `modal` and `drawer` archetypes. Not a header-level escape.

### Consultant block photo placeholder

Defers to `.stitch/portal-ux-brief.md § Photo placeholder rule`. Current rule: **neutral SVG silhouette**, never initials, never real photos. The spec in Section 4 and `chrome-component-contracts.md` is superseded by the portal-ux-brief on this specific element.

---

## 9. Accessibility floor

- **Landmarks on every page:** `<header role="banner">`, `<main role="main" id="main">`, `<footer role="contentinfo">` (public only).
- **Skip-to-main link** first in `<body>` (no exceptions; see Section 4).
- **Keyboard order matches visual order.** No positive `tabindex` values.
- **Focus rings** on keyboard focus only (`focus-visible:`); no always-on rings.
- **aria-label** on every icon-only button, link, or control.
- **`aria-current="page"`** on the active nav item if any nav is rendered.
- **Tap target** ≥ 44×44px on touch surfaces. Enforce via `min-h-11` or `w-11 h-11`.
- **Touch-state parity** (Section 6) — `hover:` paired with `focus-visible:` and `active:`.
- **Safe-area insets (mobile):** pages whose last interactive element is near the bottom apply `pb-[max(1rem,env(safe-area-inset-bottom))]` to `<main>` or the page container. Public footers apply the inset inside `<footer>`.
- **Contrast:** body text `#475569` on `#ffffff` = 8.6:1, bold `#0f172a` = 19.0:1 (WCAG 2.2 AAA).
- **No autoplay media, no carousels on auth surfaces.**

---

## 10. Content rules

- **Nav labels**: sentence case ("Text Scott", "All invoices"). Not Title Case. Not ALL CAPS.
- **Mobile breadcrumb truncation**: each segment caps at 24ch via `truncate max-w-[24ch]`. Full label on desktop.
- **Icon + label pairing**: icon-only buttons must have `aria-label`; icon + visible-text buttons must not duplicate the label as `aria-label` (redundant to screen readers).
- **Status pills**: sentence case ("Paid", "Due Friday"). Colors via state-token table. Never three attention colors in one view (use exactly one attention color per surface).
- **Date format in chrome**: "Apr 18" (short) in back labels / breadcrumbs; never "04/18" or ISO format.
- **Contact channel labels**: "Email Scott", "Text Scott", "Call Scott" (sentence case, action verb + name).

---

## 11. Refactor checklist (prerequisites for v1 approval)

Per user decision, v1 of this spec is held until the following retrofits land. All are spec-conformance refactors against shipped code; none change user-visible behavior materially.

### Blocking (v1 held until complete)

| #   | File / Scope                                                                 | Change                                                                                                                        | Est. LOC          |
| --- | ---------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| 1   | New `src/components/SkipToMain.astro`                                        | Create reusable component per Section 4 contract                                                                              | 10                |
| 2   | Every layout + every page outside a layout                                   | Insert `<SkipToMain />` as first child of `<body>`                                                                            | ~15 insertions    |
| 3   | Every `<main>` element                                                       | Add `id="main"`                                                                                                               | ~15               |
| 4   | `src/components/Nav.astro`                                                   | Move `h-16` from inner div to `<header>`; add mobile `h-14`                                                                   | 3                 |
| 5   | `src/components/portal/PortalHeader.astro`                                   | Replace `py-4` with `h-14 md:h-16` on `<header>`; add `sticky top-0 z-50`                                                     | 3                 |
| 6   | `src/layouts/AdminLayout.astro`                                              | Replace `py-3` with `h-14 md:h-16` on `<header>`; add `sticky top-0 z-50`; add mobile user-menu transform per D.1-mobile      | ~40               |
| 7   | `src/pages/portal/invoices/[id].astro`, `src/pages/portal/quotes/[id].astro` | Add back affordance inside `<main>` per canonical-parents table                                                               | ~6 per file = 12  |
| 8   | `src/layouts/AdminLayout.astro`                                              | Breadcrumb separator: replace text `/` with `<span aria-hidden="true" class="material-symbols-outlined">chevron_right</span>` | 3                 |
| 9   | `~/.agents/skills/nav-spec/validate.py`                                      | Guard R6b with `surface != "session-auth-admin"`; expand token regex to accept CSS-var, slate-N, and literal hex forms        | 10                |
| 10  | Portal + Token-auth headers                                                  | Implement three-icon contact control (email/SMS/phone) per Appendix B/C                                                       | ~15 per component |

**Total estimated scope:** ~120 LOC across ~10 files.

### Non-blocking (v2 follow-ups, tracked separately)

- Right-rail offset update when admin + portal headers become sticky — may require QA visual check of `md:top-20` being correct across all detail pages
- `<aside>` semantic audit for right-rail elements (ensure `<aside>` not `<div>`)
- `.stitch/designs/portal-v1/` artifact regeneration under the new spec (optional; existing artifacts are historical)
- `/privacy` and `/terms` marketing pages — currently linked from Appendix A footer but not shipped; either create minimal pages or remove links from footer

Once all 10 blocking items are merged, bump front matter to `spec-version: 1` (remove `-pending` suffix) and mark approval state as `approved`.

---

# Appendix A — `public` (marketing)

Base URL: `smd.services`. Visitor is a stranger or first-time prospect; goal is clear communication of offering.

## A.1 Chrome allowed

### Header

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-5xl mx-auto h-full flex items-center justify-between px-4 md:px-6">
    <a
      href="/"
      class="text-lg font-bold tracking-tight text-[#0f172a] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 rounded"
      >SMD Services</a
    >
    <div class="flex items-center gap-5">
      <a
        href="/contact"
        class="text-sm font-medium text-[#475569] hover:text-[#0f172a] active:text-[#0f172a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 min-h-11 px-2 -mx-2 inline-flex items-center rounded"
        >Contact</a
      >
      <a
        href="/book"
        class="inline-flex items-center bg-[#1e40af] hover:bg-[#1e3a8a] active:bg-[#1e3a8a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 px-5 py-2 rounded font-medium text-white min-h-11"
        >Book a Call</a
      >
    </div>
  </div>
</header>
```

**Delta vs default header:** logo wordmark (only surface class where a logo appears) + one text link + one CTA button.

### Footer (public only)

```html
<footer role="contentinfo" class="bg-white border-t border-[#e2e8f0] mt-16">
  <div
    class="max-w-5xl mx-auto px-4 md:px-6 py-8 pb-[max(2rem,env(safe-area-inset-bottom))] flex flex-col md:flex-row justify-between gap-4 text-[13px] text-[#475569]"
  >
    <span>© 2026 SMDurgan LLC. All rights reserved.</span>
    <nav aria-label="Legal" class="flex gap-4">
      <a
        href="/contact"
        class="hover:text-[#0f172a] active:text-[#0f172a] min-h-11 inline-flex items-center"
        >Contact</a
      >
    </nav>
  </div>
</footer>
```

**`/privacy` and `/terms` are not shipped as of v1.** Footer omits them until pages exist. Re-add when ready (tracked in non-blocking follow-ups).

### Hero imagery

Allowed on `/`, `/get-started`, `/scorecard` — must be explicitly specified in the page prompt. Stock headshots forbidden (validator R9).

## A.2 Chrome forbidden on public

Universal anti-patterns from Section 8, plus:

- Sidebar / hamburger / drawer as primary nav
- Bottom-tab nav
- Sticky-bottom action bar (the header CTA is sufficient for marketing)
- Testimonials on non-`/` pages
- `backdrop-blur-*` / translucent header bg (marketing can use solid hero bands; header is always solid white)

## A.3 Archetype-specific notes

- `dashboard` = the marketing home `/`. The public landing.
- `form` = `/contact`, `/book`. Cancel returns to `/`.
- `wizard` = `/scorecard`, `/get-started`. Multi-step with progress indicator in header band.
- `error` = `/404` (on `smd.services` context). Back-to-safety returns to `/`.

---

# Appendix B — `token-auth`

Base URL examples: `smd.services/book/manage/[token]`. Future: `portal.smd.services/proposal/[token]`, `portal.smd.services/invoice/[token]`. No account; token encodes identity.

## B.1 Chrome allowed

### Header (three-icon contact control, per user rule: "clients know what they want")

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-5xl mx-auto h-full flex items-center justify-between gap-4 px-4 md:px-6">
    <p class="text-[13px] leading-[18px] font-medium text-[#475569] truncate"><recipient name or "Your booking"></p>
    <div class="flex items-center gap-1">
      <a href="mailto:<email>" aria-label="Email Scott" class="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[#475569] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
        <span class="material-symbols-outlined" aria-hidden="true">mail</span>
      </a>
      <a href="sms:<phone>" aria-label="Text Scott" class="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[#475569] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
        <span class="material-symbols-outlined" aria-hidden="true">sms</span>
      </a>
      <a href="tel:<phone>" aria-label="Call Scott" class="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[#475569] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
        <span class="material-symbols-outlined" aria-hidden="true">call</span>
      </a>
    </div>
  </div>
</header>
```

**Delta vs default:** no logo; recipient name left; **three contact channels on the right** (email / SMS / phone), each 44×44, each with `aria-label`. Client picks the channel that works for them; every channel works on every device. `<mailto:>`, `<sms:>`, and `<tel:>` handle fallbacks automatically per OS/browser.

### Back affordance

**Absent.** Token-auth pages are leaf surfaces; user arrived from a link.

## B.2 Chrome forbidden on token-auth

Universal anti-patterns from Section 8. Additionally:

- Links to authenticated surfaces (`/portal`, `/admin`) — user has no session
- "Sign up" / "Create account" CTAs — not the job of token-auth

## B.3 Archetype-specific notes

- `detail` — primary; managing a booking, viewing a proposal/invoice
- `form` — reschedule via token; submit-only (no cancel affordance — user closes tab)
- `empty`, `error` — the three-icon contact control is the only recovery path

---

# Appendix C — `session-auth-client` (portal)

Base URL: `portal.smd.services`. Authenticated client viewing their own records. The working surface this spec's rigor was tuned against.

## C.1 Chrome allowed

### Header (three-icon contact control)

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-5xl mx-auto h-full flex items-center justify-between gap-4 px-4 md:px-6">
    <p class="text-[13px] leading-[18px] font-medium tracking-[0.01em] text-[#475569] truncate"><client name></p>
    <div class="flex items-center gap-1">
      <a href="mailto:<email>" aria-label="Email Scott" class="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[#475569] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
        <span class="material-symbols-outlined" aria-hidden="true">mail</span>
      </a>
      <a href="sms:<phone>" aria-label="Text Scott" class="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[#475569] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
        <span class="material-symbols-outlined" aria-hidden="true">sms</span>
      </a>
      <a href="tel:<phone>" aria-label="Call Scott" class="inline-flex items-center justify-center w-11 h-11 rounded-lg text-[#475569] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
        <span class="material-symbols-outlined" aria-hidden="true">call</span>
      </a>
      <slot /> <!-- host page may pass Sign out form -->
    </div>
  </div>
</header>
```

Shipped component: `src/components/portal/PortalHeader.astro`. Refactor item #5 in Section 11 aligns shipped component to this contract.

### Back affordance (detail archetypes)

Default contract from Section 4. Positioned inside `<main>` above content.

### Right rail (detail + dashboard, `md:` and up)

```html
<aside class="w-full md:w-[340px] md:sticky md:top-20 md:self-start space-y-6">
  <!-- ActionCard, ConsultantBlock, related links -->
</aside>
```

Width 340px ratifies live shipped portal code. `md:top-20` (80px) clears the 64px sticky header + 16px buffer.

### Mobile stack order (detail archetype)

On mobile (<`md:`), the ActionCard moves **above** `<main>` content — the primary CTA must be above the fold. Order: `<header>` → `<main>` → back button → **ActionCard** → main content body → ConsultantBlock → related links.

On desktop (`md:` and up), ActionCard lives in the right rail.

### ConsultantBlock

Shipped component: `src/components/portal/ConsultantBlock.astro`. Photo placeholder treatment is the **SVG silhouette** defined in `.stitch/portal-ux-brief.md § Photo placeholder rule`. The nav-spec defers to the UX brief on this element specifically (do not substitute initials).

## C.2 Chrome forbidden on session-auth-client

Universal anti-patterns. Especially:

- **Breadcrumbs** — never; portal is shallow (≤2 levels)
- **Global nav tabs** — portal is narrow; no tab bar
- **Sticky-bottom action bar** — primary action is in ActionCard above the fold
- **Logo in header** — client name identifies context

## C.3 Archetype-specific notes

- `dashboard` = `/portal`. No back. Two-column on `md:`. ActionCard above timeline on mobile.
- `list` = `/portal/invoices`, `/portal/quotes`, `/portal/documents`. Back → `/portal`. Filter bar below header.
- `detail` = `/portal/*/[id]`. Back → canonical parent. ActionCard above main on mobile, in right rail on `md:`.
- `form` — rare on portal; none currently live. Cancel → origin.
- `empty` — inside list views; no CTA (user doesn't create these records).
- `error` — three-icon contact control is the recovery.

---

# Appendix D — `session-auth-admin`

Base URL: `admin.smd.services`. Operator (Scott). High-privilege. Broader IA than portal; nav tabs allowed as a ratified exception.

## D.1 Chrome allowed

### Desktop header (`md:` and up)

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16 hidden md:block">
  <div class="max-w-5xl mx-auto h-full flex items-center justify-between px-4 md:px-6">
    <div class="flex items-center gap-3">
      <a href="/admin" class="text-lg font-bold text-[#0f172a] hover:text-[#1e40af] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 rounded">SMD Services</a>
      <span class="text-xs bg-[#f1f5f9] text-[#475569] px-2 py-0.5 rounded">Admin</span>
    </div>
    <div class="flex items-center gap-4">
      <!-- nav tabs per D.2, rendered hidden md:flex -->
      <span class="text-sm text-[#94a3b8]" aria-hidden="true">|</span>
      <span class="text-sm text-[#475569]"><operator email></span>
      <form method="POST" action="/api/auth/logout">
        <button type="submit" class="text-sm text-[#475569] hover:text-[#0f172a] active:text-[#0f172a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 min-h-11 px-2 -mx-2 rounded">Sign out</button>
      </form>
    </div>
  </div>
</header>
```

### Mobile header (<`md:`)

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:hidden">
  <div class="h-full flex items-center justify-between px-4">
    <div class="flex items-center gap-3">
      <a href="/admin" class="text-lg font-bold text-[#0f172a] active:text-[#1e40af] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 rounded">SMD Services</a>
      <span class="text-xs bg-[#f1f5f9] text-[#475569] px-2 py-0.5 rounded">Admin</span>
    </div>
    <details class="relative">
      <summary aria-label="Open menu" class="list-none w-11 h-11 flex items-center justify-center rounded-lg hover:bg-[#f1f5f9] active:bg-[#f1f5f9] focus-visible:ring-2 focus-visible:ring-[#3b82f6] cursor-pointer">
        <span class="material-symbols-outlined" aria-hidden="true">menu</span>
      </summary>
      <div class="absolute right-0 top-12 w-64 bg-white border border-[#e2e8f0] rounded-lg shadow-lg p-2 z-50">
        <nav aria-label="Primary" class="flex flex-col">
          <a href="/admin" class="block px-3 py-2.5 text-sm rounded hover:bg-[#f1f5f9] active:bg-[#f1f5f9]">Dashboard</a>
          <a href="/admin/entities" class="block px-3 py-2.5 text-sm rounded hover:bg-[#f1f5f9] active:bg-[#f1f5f9]">Entities</a>
          <a href="/admin/follow-ups" class="block px-3 py-2.5 text-sm rounded hover:bg-[#f1f5f9] active:bg-[#f1f5f9]">Follow-ups</a>
          <a href="/admin/analytics" class="block px-3 py-2.5 text-sm rounded hover:bg-[#f1f5f9] active:bg-[#f1f5f9]">Analytics</a>
        </nav>
        <hr class="my-2 border-[#e2e8f0]">
        <p class="px-3 py-1.5 text-xs text-[#475569]"><operator email></p>
        <form method="POST" action="/api/auth/logout">
          <button type="submit" class="w-full text-left px-3 py-2.5 text-sm rounded hover:bg-[#f1f5f9] active:bg-[#f1f5f9]">Sign out</button>
        </form>
      </div>
    </details>
  </div>
</header>
```

Below `md:`, tabs + email + Sign out collapse into a `<details>` menu triggered by an icon button. This is a **ratified menu-trigger pattern** (not a general hamburger) — it exists specifically to collapse the admin-only tab bar below `md:`. The pattern is scoped to admin; no other surface class gets a menu trigger.

### D.2 Admin nav tabs (ratified exception)

```html
<nav aria-label="Primary" class="hidden md:flex items-center gap-4">
  <a href="/admin" aria-current={active ? "page" : undefined}
     class={`text-sm transition-colors min-h-11 inline-flex items-center px-2 -mx-2 rounded focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 ${active ? "text-[#1e40af] font-medium" : "text-[#475569] hover:text-[#0f172a] active:text-[#0f172a]"}`}>
    Dashboard
  </a>
  <!-- Entities, Follow-ups, Analytics — same pattern -->
</nav>
```

Rationale for exception: admin has 4 top-level sections with distinct IA (Entities, Follow-ups, Analytics, plus Dashboard). Tab bar is clearest IA for a single-operator surface. Cap at 5 tabs; if a 6th appears, revisit via `/nav-spec --revise`.

### D.3 Breadcrumbs

Admin is the only surface class rendering breadcrumbs. Live `AdminLayout.astro` ships an optional breadcrumb prop; keep it. Format per `chrome-component-contracts.md` (use `<chevron_right aria-hidden="true">`, not `/` text).

Depth caps:

- `list`: 2 levels
- `detail`: 3 levels

### D.4 Back vs breadcrumbs

If the page has breadcrumbs, it does **not** also have a back button. If no breadcrumbs (e.g., admin dashboard), no back either — the nav tabs/menu is the navigation.

## D.5 Chrome forbidden on session-auth-admin

Universal anti-patterns, **except** nav tabs (D.2 exception) and the mobile `<details>` menu (D.1 exception). Additionally:

- Sidebar (permanent) — no
- Right rail — no (data-dense; 3-column is cramped)
- Footer — no (authenticated)
- Marketing CTAs, testimonials, hero imagery — obviously forbidden

## D.6 Archetype-specific notes

- `dashboard` = `/admin`. Grid of metric cards.
- `list` = `/admin/entities`, `/admin/follow-ups`. Filter bar. 2-level breadcrumbs acceptable.
- `detail` = `/admin/*/[id]`. 3-level breadcrumbs. No back button (breadcrumbs carry the affordance).
- `form` = `/admin/settings/*`, `/admin/*/[id]/edit`. Cancel + Save. 2-level breadcrumbs.

---

# Appendix E — `auth-gate`

Base URLs: `smd.services/auth/login`, `/auth/verify`, `/auth/portal-login`. Anonymous surface; exists to produce or consume an auth credential. All pages in this class carry `<meta name="robots" content="noindex, nofollow">`.

## E.1 Chrome allowed

### Header (wordmark only)

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-sm md:max-w-md mx-auto h-full flex items-center justify-center px-4">
    <span class="text-lg font-bold tracking-tight text-[#0f172a]">SMD Services</span>
  </div>
</header>
```

**Delta vs default:** wordmark centered (not linked — no destination is safe from an auth-gate); no Contact, no Book a Call, no nav, no contact icons. Narrower max-width to focus the form.

### No footer, no back affordance

Auth-gate has no parent destination and no legal chrome. Post-success redirects happen server-side (Section 7's cross-auth-boundary table).

## E.2 Chrome forbidden

Universal anti-patterns. Additionally:

- Contact link / Book a Call CTA — wrong audience; users are trying to sign in
- Footer with legal links — if legal is required by an OAuth consent screen, it's rendered by the OAuth provider, not by this surface
- Links to authenticated surfaces — session doesn't exist yet
- Links to marketing pages — distracting mid-auth

## E.3 Archetype-specific notes

- `form` — `/auth/login` (admin OAuth), `/auth/portal-login` (client magic-link). Submit-only (no cancel). Errors render inline via `?error=` query param.
- `transient` — `/auth/verify`. Server-side processing + redirect. Renders a fallback error only if the redirect fails. No header required (server sends a bare HTML document with a `<noscript>` fallback link).

## E.4 Post-auth transitions

Enumerated in Section 7 cross-auth-boundary table. Summary:

- Admin OAuth success → 302 `admin.smd.services/admin`
- Client magic-link verify success → 302 `portal.smd.services/portal`
- Any failure → 302 back to same auth-gate with `?error=<code>`

---

## Revision history

| spec-version | Date       | Change                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Approver |
| ------------ | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 1            | 2026-04-15 | Initial spec authored via `/nav-spec`. Grounded in Phase 0 compliance run (categorical 93%, strict 87%) and drift audit of shipped code. Approves: admin nav tabs as documented exception (D.2), admin mobile `<details>` menu (D.1-mobile), ratified live portal right-rail `md:` + 340px + `md:top-20`, SVG silhouette for ConsultantBlock (defers to portal-ux-brief), three-icon contact control in portal and token-auth headers (per user: "clients know what they want"). Introduces `auth-gate` surface class (Appendix E) for `/auth/*` pages and `transient` archetype for `/auth/verify`. All 10 Section 11 retrofit items shipped in PR feat/nav-spec-v1; verify pipeline green (1077 tests pass). | Captain  |
