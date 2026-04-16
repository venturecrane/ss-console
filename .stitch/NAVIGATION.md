---
spec-version: 2
nav-spec-skill-version: 2.0.0
design-md-sha: absent
stitch-project-id: '17873719980790683333'
phase-0-compliance:
  categorical: '93%'
  strict: '87%'
  date: '2026-04-15'
enforcement: 'injection-first + required validator (nav-spec/validate.py R1–R24)'
approval-state: 'v2 authoring complete; audit findings drive follow-up PRs'
injection-budgets:
  session-auth-client/dashboard/mobile/hub-and-spoke: { essential: 482, extended: 287, total: 769 }
  session-auth-client/list/mobile/master-detail: { essential: 421, extended: 156, total: 577 }
  session-auth-client/detail/mobile/master-detail: { essential: 437, extended: 218, total: 655 }
  session-auth-admin/dashboard/desktop/hub-and-spoke-tabs:
    { essential: 468, extended: 241, total: 709 }
  public/dashboard/desktop/pyramid: { essential: 395, extended: 112, total: 507 }
  auth-gate/form/mobile/single-page-form: { essential: 343, extended: 84, total: 427 }
  token-auth/detail/mobile/master-detail-simplified: { essential: 389, extended: 142, total: 531 }
revisions:
  - {
      from: 1.0,
      to: 2.0,
      date: '2026-04-15',
      kind: 'v1→v2 migration',
      added:
        [
          'Section 1 (Task model)',
          'Section 3 (Reachability matrix)',
          'Section 4 (Pattern selection)',
          'Section 5 (State machine)',
          'Section 12 (Content taxonomy)',
        ],
      renumbered:
        [
          'v1 §1+§2 → v2 §2',
          'v1 §3 → incorporated into v2 §4 pattern map',
          'v1 §4 → v2 §6',
          'v1 §5 → v2 §7',
          'v1 §6 → v2 §8',
          'v1 §7 → v2 §9',
          'v1 §8 → v2 §10',
          'v1 §9 → v2 §11',
          'v1 §10 → merged into v2 §12',
          'v1 §11 (refactor checklist) → archived',
        ],
    }
---

# SMD Services — Navigation Specification (v2)

Single source of truth for navigation across `smd.services`, `admin.smd.services`, and `portal.smd.services`. Three-layer spec: **Information Architecture**, **Patterns** (anchored to NN/g / Material Design 3 / Apple HIG), and **Chrome**. Every Stitch-generated screen and every new shipped component conforms. Deviations require spec revision, not one-off exceptions.

Companion skill: `~/.agents/skills/nav-spec/` (version 2.0.0). Spec is consumed by `stitch-design` (via NAV CONTRACT injection) and `stitch-ux-brief` (Phase 7 concept template, Phase 11 strip directive). Post-generation enforcement via `nav-spec/validate.py` (R1–R24).

**v2 rationale:** v1 defined chrome only. Result: pages shipped with consistent sticky headers but portal home had no way to reach list views (`/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement`) except by backtracking from a detail — a gaping IA hole that the skill, its reviewers, and its validator all missed. v2 adds Information Architecture and Patterns as explicit layers above chrome, anchored to established frameworks.

---

## 1. Task model

What users do on each surface class. IA and patterns follow from tasks.

### 1.1 Surface class: `public`

**Primary tasks:**

1. Understand what SMD Services does (organic visitor, case-study reader)
2. Book an assessment (qualified visitor)
3. Take the self-serve scorecard (prospect evaluating fit)
4. Contact consultant (ad hoc inquiry)

**Secondary:**

- Read specific landing pages (`/get-started`)
- Sign in (redirects to `auth-gate`)

### 1.2 Surface class: `auth-gate`

**Primary tasks:**

1. Request a magic link (client) or initiate OAuth (admin)
2. Verify a magic link (transient)

### 1.3 Surface class: `token-auth`

**Primary tasks:**

1. Manage an existing booking (reschedule, cancel) via `/book/manage/[token]`
2. (Future) Review/sign a proposal via token deep-link
3. (Future) Pay an invoice via token deep-link

Entry exclusively via email deep-link. No prior session assumed.

### 1.4 Surface class: `session-auth-client` (portal)

**Primary tasks** (ranked by frequency × criticality):

1. **Pay a pending invoice** — trigger: email received; completion: Stripe payment confirmed; frequency: 1–5 per engagement; criticality: blocking
2. **Review and sign a proposal** — trigger: email received; completion: SignWell signature; frequency: 1–3 per engagement; criticality: blocking
3. **See what's happening** — trigger: weekly check-in; completion: scan of Recent Activity; frequency: weekly; criticality: medium
4. **Find a document** — trigger: user wants a specific deliverable; completion: document open/downloaded; frequency: weekly (active), rare (post); criticality: medium
5. **Check engagement progress** — trigger: milestone review; completion: status scanned; frequency: weekly; criticality: medium
6. **Contact consultant** — trigger: ad hoc question; completion: message sent via preferred channel; frequency: variable; criticality: high (trust)

**Secondary tasks:**

- Review past proposals (reference)
- Review past invoices (accounting)
- Check scheduled touchpoint

### 1.4.1 Task-to-surface mapping (portal)

| Task                  | Primary surface         | Surfaces touched                                  | Entry point                |
| --------------------- | ----------------------- | ------------------------------------------------- | -------------------------- |
| Pay invoice           | `/portal/invoices/[id]` | home → list → detail → Stripe                     | Email, home ActionCard     |
| Review/sign proposal  | `/portal/quotes/[id]`   | home → list → detail → SignWell                   | Email, home timeline entry |
| See what's happening  | `/portal`               | home                                              | Login, bookmark            |
| Find document         | `/portal/documents`     | home → section card → list → document             | Section card on home       |
| Check progress        | `/portal/engagement`    | home → section card → engagement                  | Section card on home       |
| Contact consultant    | any                     | three-icon contact control in header + right rail | Header icons               |
| Review past proposals | `/portal/quotes`        | home → section card → list                        | Section card on home       |
| Review past invoices  | `/portal/invoices`      | home → section card → list                        | Section card on home       |

### 1.5 Surface class: `session-auth-admin`

**Primary tasks:**

1. Triage clients and their engagements — `/admin/entities`, `/admin/entities/[id]`
2. Manage assessments — `/admin/assessments/[id]` (accessed via entity detail)
3. Track engagement progress — `/admin/engagements/[id]` (accessed via entity detail)
4. Create and send proposals — `/admin/entities/[id]/quotes/[quoteId]`
5. Follow up on leads and clients — `/admin/follow-ups`
6. Monitor venture metrics — `/admin/analytics`
7. Manage integrations — `/admin/settings/google-connect`

Admin IA is **entity-scoped**: most work happens inside `/admin/entities/[id]` rather than at top-level list views.

---

## 2. Sitemap and auth boundary

### 2.1 Sitemap

**`smd.services`** (marketing + auth entry)

- `/` — marketing home (public, dashboard)
- `/contact` — contact form (public, form)
- `/book` — booking flow (public, form)
- `/book/manage/` — query-param redirect handler; renders "invalid link" fallback when no token (public, error)
- `/book/manage/[token]` — **token-auth** detail, manage booking
- `/get-started` — onboarding CTA (public, dashboard)
- `/scorecard` — self-serve assessment (public, wizard)
- `/404` — shared not-found, subdomain-aware (error)
- `/auth/login` — admin OAuth entry (**auth-gate**, form)
- `/auth/portal-login` — client magic-link request (**auth-gate**, form)
- `/auth/verify` — magic-link verification (**auth-gate**, transient)

**`admin.smd.services`**

- `/admin` — dashboard
- `/admin/entities` — list (clients)
- `/admin/entities/[id]` — detail (client)
- `/admin/entities/[id]/quotes/[quoteId]` — detail (nested: quote under client)
- `/admin/engagements/[id]` — detail (no list; parent is entity detail)
- `/admin/assessments/[id]` — detail (no list; parent is entity detail)
- `/admin/follow-ups` — list
- `/admin/analytics` — dashboard (analytics)
- `/admin/settings/google-connect` — form

**`portal.smd.services`**

- `/portal` — dashboard
- `/portal/invoices` — list
- `/portal/invoices/[id]` — detail
- `/portal/quotes` — list
- `/portal/quotes/[id]` — detail
- `/portal/documents` — list
- `/portal/engagement` — detail (current engagement summary)

**Dev-only** (exempt from spec): `/dev/portal-components`, `/dev/portal-states`.

### 2.2 Surface-class taxonomy (authoritative)

Classes modeled by **auth model**, not subdomain. Subdomain is secondary; two classes can coexist on one subdomain.

| Class                 | Definition                                                            | Auth signal             |
| --------------------- | --------------------------------------------------------------------- | ----------------------- |
| `public`              | No authentication. Marketing, contact, scorecard.                     | None                    |
| `auth-gate`           | Anonymous; surface exists to produce/consume a credential. `noindex`. | None pre-submit         |
| `token-auth`          | Signed URL token grants access without an account.                    | Path-segment token      |
| `session-auth-client` | Client cookie session (low-privilege).                                | `__Host-portal_session` |
| `session-auth-admin`  | Operator cookie session (high-privilege).                             | `__Host-admin_session`  |

### 2.3 Auth boundary

| Class               | Base URL                                                                  | Session cookie            | Redirect on logout               |
| ------------------- | ------------------------------------------------------------------------- | ------------------------- | -------------------------------- |
| public              | `smd.services/*`                                                          | None                      | n/a                              |
| auth-gate           | `smd.services/auth/*`                                                     | None → session on success | n/a (surface IS logout target)   |
| token-auth          | `/book/manage/[token]` (+ future `/proposal/[token]`, `/invoice/[token]`) | None                      | n/a; link expiry                 |
| session-auth-client | `portal.smd.services/*`                                                   | `__Host-portal_session`   | `smd.services/auth/portal-login` |
| session-auth-admin  | `admin.smd.services/*`                                                    | `__Host-admin_session`    | `smd.services/auth/login`        |

### 2.4 Surface-class selection when subdomain is ambiguous

Future `portal.smd.services/proposal/[token]` routes will share subdomain with `session-auth-client` but are token-auth. Disambiguation:

1. **Valid session cookie** is the primary signal. Present → `session-auth-client` (or admin). Absent but token valid → `token-auth`.
2. **Token-auth on authenticated subdomain renders distinct chrome** (visible "Viewing via secure link" badge). Prevents a refreshed-tab user from thinking their session persists.
3. **Middleware carves out token-auth paths before session-auth checks**, else middleware redirects to login.

### 2.5 Deep-link inventory

| URL                                            | Entry vector                         | Surface class       |
| ---------------------------------------------- | ------------------------------------ | ------------------- |
| `/`, other public                              | Organic / direct                     | public              |
| `/book/manage/[token]`                         | Booking confirmation email           | token-auth          |
| `/portal/*`                                    | Magic link from `/auth/portal-login` | session-auth-client |
| `/admin/*`                                     | OAuth from `/auth/login`             | session-auth-admin  |
| `/portal/quotes/[id]`, `/portal/invoices/[id]` | Email (after portal login)           | session-auth-client |

---

## 3. Reachability matrix

The central IA artifact. Declares which destinations are reachable from which surfaces and by what mechanism. Validator rules R16, R18 check code against this matrix.

### 3.1 Invariants

- Every surface appears as From ≥1 and To ≥1, with exceptions flagged (Section 3.3, 3.4).
- Every `dashboard` archetype has Required=Yes rows to every sibling list/detail.
- Every `detail` archetype has a Required=Yes back to its parent (canonical URL).
- No dead ends unless flagged Terminal.
- Mechanisms use canonical names per [reachability-matrix-template.md](../../.agents/skills/nav-spec/references/reachability-matrix-template.md).

### 3.2 Matrix

| From                                             | To                                      | Mechanism                   | Required?                         | Pattern                 |
| ------------------------------------------------ | --------------------------------------- | --------------------------- | --------------------------------- | ----------------------- |
| `/` (dashboard)                                  | `/scorecard`                            | Inline CTA                  | Yes                               | Pyramid                 |
| `/` (dashboard)                                  | `/book`                                 | Primary CTA                 | Yes                               | Pyramid                 |
| `/` (dashboard)                                  | `/contact`                              | Footer link                 | Yes                               | Pyramid                 |
| `/` (dashboard)                                  | `/get-started`                          | Inline CTA                  | Yes                               | Pyramid                 |
| `/scorecard` (wizard)                            | `/book`                                 | Terminal CTA                | Yes                               | Sequential              |
| `/scorecard` (wizard)                            | `/`                                     | Cancel                      | Yes                               | Sequential              |
| `/get-started` (dashboard)                       | `/book`                                 | Primary CTA                 | Yes                               | Pyramid                 |
| `/book` (form)                                   | (external calendar)                     | Submit redirect             | Yes                               | External                |
| `/book` (form)                                   | `/`                                     | Cancel                      | Yes                               | —                       |
| `/contact` (form)                                | `/`                                     | Submit → thank-you + Cancel | Yes                               | —                       |
| `/book/manage/[token]` (token-auth detail)       | `mailto:consultant`                     | Contact icon                | Yes                               | Contact-control         |
| `/book/manage/[token]` (token-auth detail)       | `sms:consultant`                        | Contact icon                | Yes                               | Contact-control         |
| `/book/manage/[token]` (token-auth detail)       | `tel:consultant`                        | Contact icon                | Yes                               | Contact-control         |
| `/book/manage/` (form, no token)                 | `/book`                                 | "Book a call instead" CTA   | Yes                               | Recovery-path           |
| `/auth/login` (form)                             | `/admin`                                | OAuth → 302                 | Yes                               | —                       |
| `/auth/portal-login` (form)                      | `/auth/verify`                          | Magic-link submit           | Yes                               | —                       |
| `/auth/verify` (transient)                       | `/portal`                               | Success redirect            | Yes                               | —                       |
| `/auth/verify` (transient)                       | `/auth/portal-login`                    | Failure fallback            | Yes                               | Recovery-path           |
| `/portal` (dashboard)                            | `/portal/quotes`                        | Section card                | Yes                               | Hub-and-spoke           |
| `/portal` (dashboard)                            | `/portal/invoices`                      | Section card                | Yes                               | Hub-and-spoke           |
| `/portal` (dashboard)                            | `/portal/documents`                     | Section card                | Yes                               | Hub-and-spoke           |
| `/portal` (dashboard)                            | `/portal/engagement`                    | Section card                | Yes                               | Hub-and-spoke           |
| `/portal` (dashboard)                            | `/portal/invoices/[id]`                 | ActionCard                  | Conditional (hasPendingInvoice)   | Dominant-action variant |
| `/portal` (dashboard)                            | `mailto:<consultant_email>`             | Contact icon                | Conditional (consultant assigned) | Contact-control         |
| `/portal` (dashboard)                            | `sms:<consultant_phone>`                | Contact icon                | Conditional                       | Contact-control         |
| `/portal` (dashboard)                            | `tel:<consultant_phone>`                | Contact icon                | Conditional                       | Contact-control         |
| `/portal` (dashboard)                            | `/api/auth/logout`                      | Logout button               | Yes                               | —                       |
| `/portal/quotes` (list)                          | `/portal/quotes/[id]`                   | Row click                   | Yes                               | Master-detail           |
| `/portal/quotes` (list)                          | `/portal`                               | Back button                 | Yes                               | Hub-and-spoke           |
| `/portal/quotes/[id]` (detail)                   | `/portal/quotes`                        | Back button                 | Yes                               | Master-detail           |
| `/portal/quotes/[id]` (detail)                   | `<signwell>`                            | Review & Sign CTA           | Conditional (status=sent)         | External                |
| `/portal/invoices` (list)                        | `/portal/invoices/[id]`                 | Row click                   | Yes                               | Master-detail           |
| `/portal/invoices` (list)                        | `/portal`                               | Back button                 | Yes                               | Hub-and-spoke           |
| `/portal/invoices/[id]` (detail)                 | `/portal/invoices`                      | Back button                 | Yes                               | Master-detail           |
| `/portal/invoices/[id]` (detail)                 | `<stripe>`                              | Pay Now CTA                 | Conditional (status≠paid)         | External                |
| `/portal/documents` (list)                       | `/api/portal/documents/[key]`           | Row click                   | Yes                               | Master-detail           |
| `/portal/documents` (list)                       | `/portal`                               | Back button                 | Yes                               | Hub-and-spoke           |
| `/portal/engagement` (detail)                    | `/portal`                               | Back button                 | Yes                               | Hub-and-spoke           |
| `/admin` (dashboard)                             | `/admin/entities`                       | Nav tab                     | Yes                               | Hub-and-spoke (tabs)    |
| `/admin` (dashboard)                             | `/admin/follow-ups`                     | Nav tab                     | Yes                               | Hub-and-spoke (tabs)    |
| `/admin` (dashboard)                             | `/admin/analytics`                      | Nav tab                     | Yes                               | Hub-and-spoke (tabs)    |
| `/admin/entities` (list)                         | `/admin/entities/[id]`                  | Row click                   | Yes                               | Master-detail           |
| `/admin/entities` (list)                         | `/admin`                                | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/entities/[id]` (detail)                  | `/admin/entities`                       | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/entities/[id]` (detail)                  | `/admin/entities/[id]/quotes/[quoteId]` | Related link                | Conditional (quotes exist)        | Nested-doll             |
| `/admin/entities/[id]` (detail)                  | `/admin/engagements/[id]`               | Related link                | Conditional (engagement exists)   | Nested-doll             |
| `/admin/entities/[id]` (detail)                  | `/admin/assessments/[id]`               | Related link                | Conditional (assessment exists)   | Nested-doll             |
| `/admin/entities/[id]/quotes/[quoteId]` (detail) | `/admin/entities/[id]`                  | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/engagements/[id]` (detail)               | `/admin/entities/[id]`                  | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/assessments/[id]` (detail)               | `/admin/entities/[id]`                  | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/follow-ups` (list)                       | `/admin`                                | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/analytics` (dashboard-like)              | `/admin`                                | Breadcrumb                  | Yes                               | Nested-doll             |
| `/admin/settings/google-connect` (form)          | `/admin`                                | Cancel                      | Yes                               | —                       |
| `/admin/*` (any)                                 | `/api/auth/logout`                      | Logout button               | Yes                               | —                       |
| `/404`                                           | `<subdomain-home>`                      | Back-to-safety CTA          | Yes                               | Recovery-path           |

### 3.3 Entry-only surfaces

| Surface                | Entry vector               | Notes                    |
| ---------------------- | -------------------------- | ------------------------ |
| `/book/manage/[token]` | Booking confirmation email | Token-auth; cold arrival |
| `/auth/verify`         | Magic-link email           | Auth-gate transient      |

### 3.4 Terminal surfaces

| Surface                       | Terminal action                              | Notes                                  |
| ----------------------------- | -------------------------------------------- | -------------------------------------- |
| `<signwell>` external         | SignWell signing flow                        | External; not part of venture IA       |
| `<stripe>` external           | Stripe hosted payment                        | External                               |
| `/auth/verify` (success path) | Auto-redirect to `/portal`                   | Transient; manual fallback link in DOM |
| `/auth/verify` (failure path) | Auto-redirect to `/auth/portal-login?error=` | Transient; manual fallback link        |

---

## 4. Pattern selection

Every `{surface class × archetype}` selects a named pattern from [pattern-catalog.md](../../.agents/skills/nav-spec/references/pattern-catalog.md). No pattern is invented; all choices specialize a catalog entry.

### 4.1 Surface class: `public`

| Archetype                       | Pattern                                     | Rationale                                                                  |
| ------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------- |
| dashboard (`/`, `/get-started`) | Pyramid (NN/g §1.4) with persistent top nav | Small content set; linear narrative from home; visitors move through story |
| wizard (`/scorecard`)           | Sequential (NN/g §1.3)                      | Multi-step scorecard with mandatory ordering                               |
| form (`/book`, `/contact`)      | Single-page form                            | Small field count; no step ordering                                        |
| error (`/404`)                  | Recovery-path                               | Single CTA back to subdomain home                                          |

### 4.2 Surface class: `auth-gate`

| Archetype                                  | Pattern                                      | Rationale                                               |
| ------------------------------------------ | -------------------------------------------- | ------------------------------------------------------- |
| form (`/auth/login`, `/auth/portal-login`) | Single-page form (centered, wordmark header) | One action: sign in                                     |
| transient (`/auth/verify`)                 | Recovery-path + auto-redirect                | Server-side processing; fallback link if redirect fails |

### 4.3 Surface class: `token-auth`

| Archetype                       | Pattern                                    | Rationale                                                                                          |
| ------------------------------- | ------------------------------------------ | -------------------------------------------------------------------------------------------------- |
| detail (`/book/manage/[token]`) | Master-detail simplified + Contact-control | Cold email arrival; no back target; three-icon contact control is the primary secondary affordance |

### 4.4 Surface class: `session-auth-client` (portal)

| Archetype                                                                     | Pattern                                                                                   | Rationale                                                                                                                                               |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| dashboard (`/portal`)                                                         | **Hub-and-spoke** (NN/g §1.1) + **Dominant-action variant** + **Recent-activity variant** | Bounded task set (6 primary tasks); user returns to hub between tasks; mobile-first; ActionCard surfaces contextually urgent task when state demands it |
| list (`/portal/quotes`, `/portal/invoices`, `/portal/documents`)              | Master-detail                                                                             | Back to hub; row click to detail                                                                                                                        |
| detail (`/portal/quotes/[id]`, `/portal/invoices/[id]`, `/portal/engagement`) | Master-detail with right-rail ActionCard / ConsultantBlock on desktop                     | Primary action (Sign / Pay) prominent; consultant and status alongside                                                                                  |

**Required elements for hub-and-spoke (from catalog §1.1):**

- Hub surface with visible entry points to every spoke → Section cards on `/portal` to all 4 sibling lists (enforced by R16)
- Each spoke has back affordance to canonical hub URL → Back button with href=`/portal` (enforced by R5, R16)
- No sibling-to-sibling links in base nav (e.g., no link from `/portal/quotes` to `/portal/invoices`)

### 4.5 Surface class: `session-auth-admin`

| Archetype                                                              | Pattern                                                                  | Rationale                                                                                                                                                   |
| ---------------------------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| dashboard (`/admin`, `/admin/analytics`)                               | **Hub-and-spoke with ratified tab variant** (Material 3 navigation-tabs) | 3 top-level sections (Entities, Follow-ups, Analytics); high switching frequency; Material's destination-count-and-frequency rule justifies persistent tabs |
| list (`/admin/entities`, `/admin/follow-ups`)                          | Master-detail + Faceted (filter bar)                                     | Potentially large lists; filter useful                                                                                                                      |
| detail (`/admin/entities/[id]`, nested quotes/engagements/assessments) | Nested-doll + Persistent-context workspace                               | 3-level hierarchy; `/admin/entities/[id]` is the workspace context; nested surfaces inherit it                                                              |
| form (`/admin/settings/*`)                                             | Single-page form                                                         | Config editing                                                                                                                                              |

**Admin nav tabs exception (ratified, see Appendix D.2):** `session-auth-admin` is the ONE permitted exception to "no nav tabs in header." Rationale: 3 top-level sections + Dashboard, high-frequency switching in operator workflow, Material 3 permits 3–7 at persistent visibility. Validator R6 has `surface != "session-auth-admin"` guard.

---

## 5. Navigation state machine

How navigation changes per auth state, data state, and task state. Validator R21 checks state-aware rendering.

### 5.1 Surface class: `session-auth-client`

#### Auth states

| State           | Detection                     | Behavior                                 |
| --------------- | ----------------------------- | ---------------------------------------- |
| Authenticated   | Valid `__Host-portal_session` | Full nav per matrix                      |
| Expired         | Middleware 401                | 302 `/auth/portal-login?redirect=<path>` |
| Unauthenticated | No session                    | 302 `/auth/portal-login`                 |

#### `/portal` data states

| State                       | Condition                                     | Rendering                                                                                                   |
| --------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Empty                       | `!activeEngagement`                           | Welcome message; "Your portal will populate when your engagement begins"; ConsultantBlock; no section cards |
| Error                       | DB fetch threw                                | Error card + Retry; ConsultantBlock preserved                                                               |
| Populated + pending invoice | `pendingInvoice != null`                      | ActionCard dominant; Recent Activity; 4 section cards                                                       |
| Populated + next touchpoint | `nextTouchpointAt != null && !pendingInvoice` | Touchpoint card; Recent Activity; 4 section cards                                                           |
| Populated + idle            | else                                          | "Nothing needs your attention right now"; Recent Activity; 4 section cards                                  |

#### `/portal/quotes/[id]` task states

| quote.status | Primary CTA   | Body                                                       |
| ------------ | ------------- | ---------------------------------------------------------- |
| sent         | Review & Sign | Expiry countdown if set                                    |
| accepted     | —             | "Signed on <date>"; link to deposit invoice if issued      |
| declined     | —             | "You declined this proposal on <date>"; Contact consultant |
| expired      | —             | "This proposal expired on <date>"; Contact consultant      |

#### `/portal/invoices/[id]` task states

| invoice.status | Primary CTA              | Body                         |
| -------------- | ------------------------ | ---------------------------- |
| sent           | Pay Now                  | Amount + due date            |
| overdue        | Pay Now (with indicator) | Amount + due date past       |
| paid           | —                        | Paid badge; receipt download |
| void           | —                        | Voided notice                |

### 5.2 Surface class: `session-auth-admin`

#### Auth states

| State           | Detection                                 | Behavior                          |
| --------------- | ----------------------------------------- | --------------------------------- |
| Authenticated   | Valid `__Host-admin_session` + role=admin | Full nav                          |
| Expired         | 401                                       | 302 `/auth/login?redirect=<path>` |
| Unauthenticated | No session                                | 302 `/auth/login`                 |

#### Per-surface data states

Admin uses consistent empty + error + populated states across list/detail. Pattern: empty list → "No <entities> yet"; error → error card + Retry; populated → data rendering per surface.

---

## 6. Chrome component contracts

### 6.1 Default header band

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-5xl mx-auto h-full flex items-center justify-between px-4 md:px-6">
    <!-- left: per surface class -->
    <!-- right: per surface class -->
  </div>
</header>
```

**Height class placement:** `h-14 md:h-16` MUST be on `<header>` itself. Validator checks header's class list only.

**Width wrapper:** `max-w-5xl mx-auto` on inner `<div>`. Outer `<header>` is full-bleed so border spans viewport.

### 6.2 Token acceptance forms

Validator accepts any equivalent form for color tokens:

| Token        | Literal hex | CSS variable                  | Tailwind    |
| ------------ | ----------- | ----------------------------- | ----------- |
| Border       | `#e2e8f0`   | `var(--color-border)`         | `slate-200` |
| Text default | `#475569`   | `var(--color-text-secondary)` | `slate-600` |
| Text bold    | `#0f172a`   | `var(--color-text-primary)`   | `slate-900` |
| Primary      | `#1e40af`   | `var(--color-primary)`        | `blue-800`  |
| Focus ring   | `#3b82f6`   | `var(--color-action)`         | `blue-500`  |

Astro components prefer CSS-var form. Stitch generations emit literal hex. Validator accepts any.

### 6.3 Default back affordance (detail archetypes)

```html
<a href="<canonical-parent-url>" aria-label="<parent-label>"
   class="inline-flex items-center gap-1 w-11 h-11 text-[#475569] hover:text-[#0f172a] active:text-[#0f172a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 rounded-lg">
  <span class="material-symbols-outlined">chevron_left</span>
  <span class="text-[13px] font-medium"><parent-label></span>
</a>
```

Positioned inside `<main>`, above content, never inside `<header>`.

### 6.4 Section cards (dashboard hub-and-spoke)

```html
<a href="<sibling-route>" aria-label="<sibling-label>"
   class="block rounded-lg border border-[color:var(--color-border)] bg-white p-4 min-h-[88px] hover:border-[color:var(--color-border-strong)] transition-colors focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2">
  <div class="flex items-start gap-3">
    <span class="material-symbols-outlined text-[color:var(--color-primary)]" aria-hidden="true"><icon></span>
    <div>
      <p class="font-['Plus_Jakarta_Sans'] font-bold text-[color:var(--color-text-primary)]"><label></p>
      <p class="text-sm text-[color:var(--color-text-secondary)] mt-1"><caption></p>
    </div>
  </div>
</a>
```

Grid: 1 column mobile, 2 columns desktop (via `grid md:grid-cols-2 gap-3`).

### 6.5 Breadcrumbs

Admin only. Depth ≥ 2. Separator: `<span class="material-symbols-outlined" aria-hidden="true">chevron_right</span>`. Segment truncation at 24ch on mobile via `truncate max-w-[24ch]`.

### 6.6 Skip-to-main link (all surfaces)

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

## 7. Mobile ↔ desktop transforms

Single chrome breakpoint: `md:` (768px). Content layouts may use `sm:` / `lg:` / `xl:`. Chrome never does, with one named exception (right-rail sticky).

### 7.1 Per chrome piece

| Chrome                     | Mobile (<768)                                | Desktop (≥768)            |
| -------------------------- | -------------------------------------------- | ------------------------- |
| Header height              | `h-14` (56px)                                | `md:h-16` (64px)          |
| Header padding x           | `px-4` (inner div)                           | `md:px-6`                 |
| Header max-width           | full bleed                                   | `md:max-w-5xl md:mx-auto` |
| Back affordance            | in-flow inside `<main>`                      | same                      |
| Section cards grid         | 1 column                                     | `md:grid-cols-2`          |
| Admin nav tabs             | `hidden`, collapsed into menu                | `md:flex`, inline         |
| Admin user menu trigger    | icon button                                  | `md:hidden`               |
| ActionCard (portal detail) | above `<main>` content                       | in right rail             |
| Right rail (portal)        | stacks below                                 | `md:` (see 7.2)           |
| Breadcrumbs                | truncation at 24ch                           | full labels               |
| Safe-area bottom           | `pb-[max(1rem,env(safe-area-inset-bottom))]` | n/a                       |

### 7.2 Right-rail stickiness (named exception)

Right rail is the ONE chrome piece permitted to use `md:` stickiness classes on `<aside>`:

```
md:w-[340px] md:sticky md:top-20 md:self-start
```

`md:top-20` (80px) clears 64px sticky header + 16px buffer. If header height changes, rail offset must update in lockstep.

---

## 8. State conventions

From `src/styles/global.css`.

| State            | Hex                         | CSS var                  | Tailwind      |
| ---------------- | --------------------------- | ------------------------ | ------------- |
| Primary / active | `#1e40af`                   | `--color-primary`        | `blue-800`    |
| Primary hover    | `#1e3a8a`                   | `--color-primary-hover`  | `blue-900`    |
| Default text     | `#475569`                   | `--color-text-secondary` | `slate-600`   |
| Bold text        | `#0f172a`                   | `--color-text-primary`   | `slate-900`   |
| Disabled         | `#94a3b8`                   | `--color-text-muted`     | `slate-400`   |
| Border           | `#e2e8f0`                   | `--color-border`         | `slate-200`   |
| Focus ring       | `#3b82f6` @ 2px, 2px offset | `--color-action`         | `blue-500`    |
| Error            | `#ef4444`                   | `--color-error`          | `red-500`     |
| Success / paid   | `#10b981`                   | `--color-complete`       | `emerald-500` |
| Attention        | `#f59e0b`                   | `--color-attention`      | `amber-500`   |
| Meta (timeline)  | `#6366f1`                   | `--color-meta`           | `indigo-500`  |

### 8.1 Touch-state parity (critical for mobile)

Every `hover:` MUST pair with `focus-visible:` AND `active:`. Without `active:`, touch devices exhibit "stuck hover."

Canonical interactive pattern:

```
text-[#475569] hover:text-[#0f172a] active:text-[#0f172a]
focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2
```

Apply to every chrome link, button, and CTA.

### 8.2 Tap target enforcement

Minimum 44×44px. Every interactive chrome element declares `min-h-11 min-w-11` or `w-11 h-11` (icon-only) or `min-h-11` + negative horizontal margins (text). Load-bearing; do not omit.

### 8.3 `aria-current`

Active nav item carries `aria-current="page"`. Validator checks on any rendered nav.

---

## 9. Transition contracts

### 9.1 Back-target resolution

All back buttons and cancel buttons resolve to a **hardcoded canonical URL string**. Never `#`, `javascript:`, or `history.back()`. Token-auth leaves have NO back button.

### 9.2 Canonical parents (authoritative)

| From                                    | Back target            | Label           |
| --------------------------------------- | ---------------------- | --------------- |
| `/portal/invoices/[id]`                 | `/portal/invoices`     | "All invoices"  |
| `/portal/quotes/[id]`                   | `/portal/quotes`       | "All proposals" |
| `/portal/engagement`                    | `/portal`              | "Home"          |
| `/admin/entities/[id]`                  | `/admin/entities`      | "All clients"   |
| `/admin/entities/[id]/quotes/[quoteId]` | `/admin/entities/[id]` | "<Client name>" |
| `/admin/engagements/[id]`               | `/admin/entities/[id]` | "<Client name>" |
| `/admin/assessments/[id]`               | `/admin/entities/[id]` | "<Client name>" |
| `/admin/follow-ups`                     | `/admin`               | "Dashboard"     |
| `/admin/analytics`                      | `/admin`               | "Dashboard"     |
| `/admin/settings/google-connect`        | `/admin`               | "Dashboard"     |
| `/contact`, `/book`                     | `/`                    | "Home"          |
| `/get-started`                          | `/`                    | "Home"          |
| `/scorecard`                            | `/`                    | "Home"          |

No fallback row. Every new dynamic route must add a row during revision.

### 9.3 Modal / drawer close

Esc closes. Click-outside (scrim) closes. X button closes. Focus returns to trigger.

### 9.4 Cross-auth-boundary

| From                | To                  | Mechanism                                         | Cookie?                        |
| ------------------- | ------------------- | ------------------------------------------------- | ------------------------------ |
| public              | auth-gate           | `<a>` same origin                                 | —                              |
| auth-gate           | session-auth-client | 302 + Set-Cookie                                  | sets `__Host-portal_session`   |
| auth-gate           | session-auth-admin  | 302 + Set-Cookie                                  | sets `__Host-admin_session`    |
| session-auth-client | auth-gate (logout)  | POST → 302 `/auth/portal-login`                   | clears `__Host-portal_session` |
| session-auth-admin  | auth-gate (logout)  | POST → 302 `/auth/login`                          | clears `__Host-admin_session`  |
| session-auth-\*     | public              | `<a>` with target origin                          | —                              |
| token-auth          | anywhere            | `<a>` with explicit origin; token does not follow | —                              |

All cross-subdomain transitions are full page loads (no client-side routing across origins).

### 9.5 Cross-subdomain 404

`src/pages/404.astro` renders in all three subdomain contexts. Back-to-safety link resolves by subdomain via middleware-set `Astro.locals.homeUrl`:

- `smd.services` → `/`
- `portal.smd.services` → `/portal`
- `admin.smd.services` → `/admin`

---

## 10. Anti-patterns

See `~/.agents/skills/nav-spec/references/anti-patterns.md` for rationale and validator cross-refs.

### 10.1 Chrome anti-patterns (R1–R15)

| Anti-pattern                                       | Forbidden on                                       | Exceptions                   |
| -------------------------------------------------- | -------------------------------------------------- | ---------------------------- |
| Global nav tabs in header                          | public, session-auth-client, token-auth, auth-gate | Admin (Appendix D.2)         |
| Sidebar / hamburger / drawer primary nav           | all                                                | none                         |
| Bottom-tab nav on mobile                           | all                                                | none                         |
| Sticky-bottom action bar on viewport               | all                                                | See 10.3                     |
| Footer                                             | auth-gate, session-auth-\*, token-auth             | Public (Appendix A)          |
| Marketing CTAs                                     | session-auth-\*, token-auth, auth-gate             | none                         |
| Testimonials / pull quotes                         | all                                                | Public marketing (explicit)  |
| Hero imagery                                       | session-auth-\*, token-auth, auth-gate             | Public (explicit)            |
| Real-face photo placeholders                       | all                                                | none                         |
| Breadcrumbs on non-admin                           | public, auth-gate, session-auth-client, token-auth | Admin only                   |
| `<nav aria-label="Breadcrumb">` around single link | all                                                | none                         |
| `fixed top-0` on header                            | all                                                | none                         |
| `backdrop-blur-*` / translucent header             | session-auth-\*, token-auth, auth-gate             | Public hero bands (explicit) |
| Icon before client name in header                  | session-auth-\*, token-auth                        | none                         |
| Back `href="#"` / `javascript:` / `history.back()` | all                                                | none                         |

### 10.2 IA anti-patterns (R16–R24, new in v2)

| Anti-pattern                | Validator | Notes                                                                      |
| --------------------------- | --------- | -------------------------------------------------------------------------- |
| Orphan destination          | R16       | Route exists in `src/pages/**` but no matrix row → no navigated affordance |
| Dead-end surface            | R18       | No nav exit; not flagged Terminal                                          |
| Pattern-impersonation       | R17       | Declared pattern's required elements not rendered                          |
| Token-auth amnesia          | R19       | Token surface assumes prior session                                        |
| Taxonomy drift              | R20       | "Quote" rendered when canonical is "Proposal", etc.                        |
| State omission              | R21       | Surface handles only populated state                                       |
| Heading hierarchy violation | R22       | Multiple `<h1>`, skipped levels                                            |
| Search affordance missing   | R23       | Declared search not rendered                                               |
| Cross-surface context loss  | R24       | Persistent-context pattern broken on a workspace surface                   |

### 10.3 Sticky-bottom escape hatches

For long forms where the primary CTA would fall below 390×844 fold:

1. **Primary CTA at natural end of `<main>`** (document flow). Preferred.
2. **Wizard split** — break form into steps. Required for ≥6 fields on mobile.
3. **Duplicate CTA top and bottom** (both in document flow). Acceptable for public forms.
4. **`sticky bottom-0` scoped to scrollable container inside `<main>`** — permitted only in `modal` and `drawer`.

### 10.4 Consultant block photo placeholder

Defers to `.stitch/portal-ux-brief.md § Photo placeholder rule`. Current rule: **neutral SVG silhouette**; never initials, never real photos.

---

## 11. A11y floor

- Landmarks: `<header role="banner">`, `<main role="main" id="main">`, `<footer role="contentinfo">` (public only).
- Skip-to-main link first in `<body>` (no exceptions).
- **Heading hierarchy: exactly one `<h1>`; `<h2>` for major sections; no level skipping** (R22).
- Keyboard order matches visual order. No positive `tabindex`.
- Focus rings on keyboard focus only (`focus-visible:`); no always-on rings.
- `aria-label` on every icon-only button, link, or control.
- `aria-current="page"` on active nav item.
- Tap targets ≥ 44×44px.
- Touch-state parity (Section 8.1).
- Safe-area insets on mobile bottom (Section 7.1).
- Contrast: body `#475569` on `#ffffff` = 8.6:1; bold `#0f172a` = 19:1 (WCAG 2.2 AAA).
- No autoplay media, no carousels on auth surfaces.

---

## 12. Content taxonomy

Labels, verbs, statuses, time expressions, and empty-state copy. Validator R20 checks rendered text against canonical terms.

### 12.1 Object names

| Entity          | Canonical label | Forbidden synonyms               |
| --------------- | --------------- | -------------------------------- |
| quote           | Proposal        | Quote, Estimate, Bid, SOW        |
| invoice         | Invoice         | Bill, Statement                  |
| engagement      | Engagement      | Project, Contract, Job           |
| milestone       | Milestone       | Phase, Stage, Task, Checkpoint   |
| entity (client) | Client          | Customer, Account                |
| consultant      | Consultant      | Agent, Staff, Advisor, Rep       |
| document        | Document        | File, Attachment, Artifact       |
| assessment      | Assessment      | Audit, Evaluation, Review (noun) |
| follow-up       | Follow-up       | Task, Ticket, Reminder           |

### 12.2 Action verbs

| Action                 | Canonical verb                | Forbidden                |
| ---------------------- | ----------------------------- | ------------------------ |
| View proposal detail   | Review                        | Read, Look at, Open      |
| Sign proposal          | Review & Sign (composite CTA) | Accept, Approve, Execute |
| Pay invoice            | Pay                           | Settle, Remit, Fund      |
| Download/view document | View (PDF) / Download (other) | Get, Fetch, Save         |
| Contact consultant     | Contact                       | Reach out, Message       |
| Book a call            | Book a Call                   | Schedule, Reserve        |

### 12.3 Status labels

| Entity     | DB value   | Label          | Badge color |
| ---------- | ---------- | -------------- | ----------- |
| quote      | draft      | Draft          | slate       |
| quote      | sent       | Pending Review | blue        |
| quote      | accepted   | Accepted       | emerald     |
| quote      | declined   | Declined       | red         |
| quote      | expired    | Expired        | amber       |
| invoice    | draft      | Draft          | slate       |
| invoice    | sent       | Sent           | blue        |
| invoice    | paid       | Paid           | emerald     |
| invoice    | overdue    | Overdue        | red         |
| invoice    | void       | Voided         | slate       |
| engagement | scheduled  | Scheduled      | slate       |
| engagement | active     | In flight      | blue        |
| engagement | handoff    | Handoff        | amber       |
| engagement | safety_net | Safety net     | amber       |
| engagement | completed  | Completed      | emerald     |
| engagement | cancelled  | Cancelled      | red         |

### 12.4 Time expressions

| Context                | Format                               | Example                     |
| ---------------------- | ------------------------------------ | --------------------------- |
| List items, same year  | `Mon D`                              | Apr 15                      |
| List items, prior year | `Mon D, YYYY`                        | Apr 15, 2025                |
| Relative               | `N days ago` / `Yesterday` / `Today` | 3 days ago                  |
| Expiry countdown       | `Expires in N days` / `Expired`      | Expires in 5 days           |
| Touchpoint             | `DayName, Mon D at H:MM AM/PM`       | Thursday, Apr 18 at 2:30 PM |
| ISO (internal)         | `YYYY-MM-DD`                         | 2026-04-15                  |

### 12.5 Numeric formats

| Context                               | Format              | Example     |
| ------------------------------------- | ------------------- | ----------- |
| Project price (marketing / proposals) | `$N,NNN` (no cents) | $5,250      |
| Invoice amount                        | `$N,NNN.CC`         | $2,625.00   |
| Small counts                          | no units            | 3 proposals |

### 12.6 Empty-state copy

| Surface              | Canonical copy                                                                |
| -------------------- | ----------------------------------------------------------------------------- |
| `/portal/quotes`     | No proposals yet. When we send you one, it will appear here.                  |
| `/portal/invoices`   | No invoices yet. When we issue one, it will appear here.                      |
| `/portal/documents`  | Documents will appear here as your engagement progresses.                     |
| `/portal/engagement` | No active engagement. When your engagement begins, progress will appear here. |
| `/portal` (idle)     | Nothing needs your attention right now.                                       |
| `/admin/entities`    | No clients yet.                                                               |
| `/admin/follow-ups`  | No follow-ups due.                                                            |

### 12.7 Error-state copy

| Context                        | Canonical copy                                      |
| ------------------------------ | --------------------------------------------------- |
| Portal dashboard fetch failure | "Something went wrong loading your portal." + Retry |
| 404 on portal                  | "This page doesn't exist. Go to the portal home."   |
| 401 on portal                  | "Your session expired. Please sign in again."       |
| Admin DB error                 | "Something went wrong loading <section>." + Retry   |

### 12.8 Content rules (carried from v1 §10)

- Nav labels: sentence case ("Text Scott", "All invoices"). Not Title Case. Not ALL CAPS.
- Breadcrumb mobile truncation: 24ch per segment via `truncate max-w-[24ch]`.
- Icon-only buttons: `aria-label` required. Icon + visible text: do not duplicate as `aria-label` (redundant to screen readers).
- Status pills: sentence case. Colors per 12.3. Never three attention colors in one view.
- Date format in chrome: "Apr 18" short, never "04/18" or ISO.
- Contact channel labels: "Email Scott", "Text Scott", "Call Scott" (sentence case, action verb + name).

---

# Appendix A — `public` (marketing)

Base URL: `smd.services`. Visitor is a stranger or first-time prospect.

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

**Delta:** logo wordmark (only surface class with a logo) + one text link + one CTA button.

### Footer

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

### Hero imagery

Allowed on `/`, `/get-started`, `/scorecard` — must be explicitly specified in the page prompt. Stock headshots forbidden (R9).

## A.2 Chrome forbidden

Universal anti-patterns, plus: sidebar / hamburger, bottom-tab nav, sticky-bottom action bar, testimonials on non-`/` pages, `backdrop-blur-*` on header.

## A.3 Archetype-specific notes

- `dashboard` = `/`, `/get-started`. Public landing.
- `form` = `/contact`, `/book`. Cancel → `/`.
- `wizard` = `/scorecard`. Multi-step; progress indicator in header band.
- `error` = `/404`. Back-to-safety → subdomain home (see Section 9.5).

---

# Appendix B — `token-auth`

Base URL examples: `smd.services/book/manage/[token]`. Future: `portal.smd.services/proposal/[token]`, `/invoice/[token]`. No account; token encodes identity.

## B.1 Chrome allowed

### Header (three-icon contact control)

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

**Delta:** no logo; recipient name left; three contact channels right, each 44×44, each with `aria-label`. Client picks the channel. `<mailto:>`, `<sms:>`, `<tel:>` handle fallbacks per OS/browser.

### Back affordance

**Absent.** Token-auth leaves have no parent destination.

## B.2 Chrome forbidden

Universal anti-patterns. Additionally:

- Links to authenticated surfaces (`/portal`, `/admin`) — no session
- "Sign up" / "Create account" CTAs — not the job of token-auth

## B.3 Archetype-specific notes

- `detail` — primary; managing a booking, viewing a proposal/invoice
- `form` — reschedule via token; submit-only
- `empty`, `error` — three-icon contact control is the only recovery

---

# Appendix C — `session-auth-client` (portal)

Base URL: `portal.smd.services`. Authenticated client.

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

Shipped component: `src/components/portal/PortalHeader.astro`.

### Back affordance (detail archetypes)

Default contract from Section 6. Inside `<main>` above content.

### Right rail (detail + dashboard, `md:` and up)

```html
<aside class="w-full md:w-[340px] md:sticky md:top-20 md:self-start space-y-6">
  <!-- ActionCard, ConsultantBlock, related links -->
</aside>
```

`md:top-20` (80px) clears 64px sticky header + 16px buffer.

### Mobile stack order (detail archetype)

`<header>` → `<main>` → back button → **ActionCard** → main content body → ConsultantBlock → related links. Primary CTA above the fold on mobile.

### ConsultantBlock

Shipped: `src/components/portal/ConsultantBlock.astro`. Photo placeholder: SVG silhouette per `.stitch/portal-ux-brief.md`.

### C.1.1 Section card grid requirement (R16)

Portal home (`/portal`) MUST render section cards to all four sibling list routes: `/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement`. This is the specialization of hub-and-spoke required for this venture. Omission triggers R16 violation.

Grid: 1 column mobile (`grid-cols-1`), 2 columns desktop (`md:grid-cols-2`). Placement: in the main column, between hero/eyebrow and Recent Activity.

## C.2 Chrome forbidden

Universal anti-patterns. Additionally:

- Breadcrumbs — never
- Global nav tabs — never (portal is narrow)
- Sticky-bottom action bar — primary action in ActionCard above the fold
- Logo in header — client name identifies context

## C.3 Archetype-specific notes

- `dashboard` = `/portal`. No back. Section cards + Recent Activity + right rail (desktop).
- `list` = `/portal/invoices`, `/portal/quotes`, `/portal/documents`. Back → `/portal`. Filter bar below header.
- `detail` = `/portal/*/[id]`, `/portal/engagement`. Back → canonical parent. ActionCard mobile-above / desktop-rail.
- `form` — rare; none currently live. Cancel → origin.
- `empty` — inside list views; no CTA (user doesn't create these records).
- `error` — three-icon contact control is recovery.

---

# Appendix D — `session-auth-admin`

Base URL: `admin.smd.services`. Operator (Scott). High-privilege. Broader IA; nav tabs allowed.

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
      <!-- nav tabs per D.2 -->
      <span class="text-sm text-[#94a3b8]" aria-hidden="true">|</span>
      <span class="text-sm text-[#475569]"><operator email></span>
      <form method="POST" action="/api/auth/logout">
        <button type="submit" class="text-sm text-[#475569] hover:text-[#0f172a] active:text-[#0f172a] focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 min-h-11 px-2 -mx-2 rounded">Sign out</button>
      </form>
    </div>
  </div>
</header>
```

### Mobile header (<`md:`) — `<details>` dropdown

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
          <a href="/admin/entities" class="block px-3 py-2.5 text-sm rounded hover:bg-[#f1f5f9] active:bg-[#f1f5f9]">Clients</a>
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

Ratified menu-trigger pattern; scoped to admin; no other surface class gets a menu trigger.

### D.2 Admin nav tabs (ratified exception)

```html
<nav aria-label="Primary" class="hidden md:flex items-center gap-4">
  <a href="/admin" aria-current={active ? "page" : undefined}
     class={`text-sm transition-colors min-h-11 inline-flex items-center px-2 -mx-2 rounded focus-visible:ring-2 focus-visible:ring-[#3b82f6] focus-visible:ring-offset-2 ${active ? "text-[#1e40af] font-medium" : "text-[#475569] hover:text-[#0f172a] active:text-[#0f172a]"}`}>
    Dashboard
  </a>
  <!-- Clients, Follow-ups, Analytics — same pattern -->
</nav>
```

Cap at 5 tabs. Current count: 4 (Dashboard, Clients, Follow-ups, Analytics). If a 6th appears, revisit via `/nav-spec --revise`.

### D.3 Breadcrumbs

Admin only. Format: `<nav aria-label="Breadcrumb"><ol>...</ol></nav>` with 2+ items. Separator: `<span aria-hidden="true" class="material-symbols-outlined">chevron_right</span>`.

Depth caps: list = 2, detail = 3.

### D.4 Back vs breadcrumbs

If breadcrumbs present → no back button. Admin dashboard: no breadcrumbs, no back (nav tabs ARE the navigation).

## D.5 Chrome forbidden

Universal anti-patterns, **except** nav tabs (D.2) and mobile `<details>` menu (D.1). Additionally:

- Sidebar (permanent) — no
- Right rail — no (data-dense; 3-column cramped)
- Footer — no (authenticated)
- Marketing CTAs, testimonials, hero imagery — forbidden

## D.6 Archetype-specific notes

- `dashboard` = `/admin`, `/admin/analytics`. Grid of metric cards. Nav tabs for primary navigation.
- `list` = `/admin/entities`, `/admin/follow-ups`. Filter bar; 2-level breadcrumbs.
- `detail` = `/admin/entities/[id]`, nested quotes/engagements/assessments. 3-level breadcrumbs. Persistent-context pattern (client is the workspace scope).
- `form` = `/admin/settings/*`. Cancel + Save. 2-level breadcrumbs.

---

# Appendix E — `auth-gate`

Base URLs: `smd.services/auth/login`, `/auth/verify`, `/auth/portal-login`. Anonymous; produces/consumes credential. `<meta name="robots" content="noindex, nofollow">`.

## E.1 Chrome allowed

### Header (wordmark only)

```html
<header role="banner" class="sticky top-0 z-50 bg-white border-b border-[#e2e8f0] h-14 md:h-16">
  <div class="max-w-sm md:max-w-md mx-auto h-full flex items-center justify-center px-4">
    <span class="text-lg font-bold tracking-tight text-[#0f172a]">SMD Services</span>
  </div>
</header>
```

**Delta:** wordmark centered (not linked — no safe destination from auth-gate); no Contact/Book a Call/nav/contact icons. Narrower max-width.

### No footer, no back affordance

Auth-gate has no parent destination and no legal chrome. Post-success redirects are server-side (Section 9.4).

## E.2 Chrome forbidden

Universal anti-patterns. Additionally:

- Contact / Book a Call — wrong audience
- Footer with legal — if OAuth legal is required, OAuth provider renders it
- Links to authenticated surfaces — session doesn't exist yet
- Links to marketing — distracting mid-auth

## E.3 Archetype-specific notes

- `form` — `/auth/login` (admin OAuth), `/auth/portal-login` (client magic-link). Submit-only. Errors via `?error=` query param.
- `transient` — `/auth/verify`. Server-side processing + redirect. Bare HTML + `<noscript>` fallback.

## E.4 Post-auth transitions

See Section 9.4. Summary:

- Admin OAuth success → 302 `admin.smd.services/admin`
- Client magic-link verify success → 302 `portal.smd.services/portal`
- Failure → 302 back to same auth-gate with `?error=<code>`

---

## Revision history

| Version | Date       | Change                                                                                                                                                                                                                                                                                                                                                           |
| ------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1.0     | 2026-04-15 | Initial v1 spec authored (chrome-only). Retrofit shipped in PR #391.                                                                                                                                                                                                                                                                                             |
| 2.0     | 2026-04-15 | v1→v2 migration: added Sections 1 (Task model), 3 (Reachability matrix), 4 (Pattern selection), 5 (State machine), 12 (Content taxonomy). Renumbered v1 Sections 4–10 → v2 Sections 6–11. Archived v1 Section 11 (refactor checklist — all 10 items shipped in PR #391). Anchored all pattern choices to NN/g / Material 3 / Apple HIG per `pattern-catalog.md`. |

## Non-blocking follow-ups

Tracked for future revisions:

- `/privacy` and `/terms` marketing pages — currently absent; Appendix A footer references were removed in v1 and stay removed
- Scorecard wizard sticky-bottom nav refactor (Phase 0 semantic compliance improvement)
- Right-rail `md:top-20` offset QA across admin detail pages (if admin gets a right rail)
- `.stitch/designs/portal-v1/` artifact regeneration under v2 (optional; artifacts are historical)
