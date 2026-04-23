---
spec-version: 3
nav-spec-skill-version: 3.0.0
evidence-mode: validated
design-md-sha: <sha-placeholder>
phase-0-compliance: { categorical: '93%', strict: '87%', date: '2026-04-15' }
injection-budgets:
  session-auth-client/dashboard/mobile/persistent-tabs:
    { essential: 482, extended: 287, total: 769 }
  session-auth-client/list/mobile/master-detail: { essential: 421, extended: 156, total: 577 }
  session-auth-client/detail/mobile/master-detail: { essential: 437, extended: 218, total: 655 }
  session-auth-admin/dashboard/desktop/hub-and-spoke: { essential: 468, extended: 241, total: 709 }
  public/dashboard/desktop/pyramid: { essential: 395, extended: 112, total: 507 }
  auth-gate/form/mobile/single-page-form: { essential: 343, extended: 84, total: 427 }
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
    }
  - {
      from: 2.0,
      to: 3.0,
      date: '2026-04-16',
      kind: 'v2→v3 migration',
      added:
        [
          'evidence_source + return_locus columns (§1)',
          'Section 4 decision log format (chosen / runner_up / defense / reviewer approvals)',
          'R25 pattern fitness + R26 authoring-direction enforcement',
        ],
      pattern-changes:
        [
          'session-auth-client/dashboard: hub-and-spoke → persistent-tabs (D1 fired on 2 of top-3 tasks with return_locus=external)',
        ],
    }
---

# ss-console — Navigation Specification (v3)

The authoritative navigation spec for the SMD Services venture (`smd.services`, `portal.smd.services`, `admin.smd.services`). Anchored to Nielsen Norman Group navigation patterns, Material Design 3 navigation components, Apple HIG, and Dan Brown's 8 IA principles.

Sections 1–5 define **IA and patterns** (v2 addition). Sections 6–11 define **chrome** (carried forward from v1 with minor edits). Section 12 defines **content taxonomy** (v2 addition). Appendices A–E provide surface-class overrides.

---

## 1. Task model

### 1.1 Surface class: `public`

#### Primary tasks

1. **Understand what SMD Services does** — visitor arriving organic/referral wants to know the value proposition
2. **Book an assessment** — qualified visitor ready to explore an engagement
3. **Read case study / scorecard** — visitor evaluating fit

#### Secondary tasks

- Read blog / articles
- Sign in to existing portal (redirects to `auth-gate`)

### 1.2 Surface class: `auth-gate`

#### Primary tasks

1. **Sign in with magic link** (client) or email+password (admin)

Entry from: `/auth/login`, `/auth/portal-login`, expiry redirects.

### 1.3 Surface class: `token-auth`

#### Primary tasks

1. **Review and sign a proposal** (via `/portal/proposals/[token]`)
2. **Pay an invoice** (via `/invoice/[id]`)

Entry exclusively via email deep-link. No prior session assumed.

### 1.4 Surface class: `session-auth-client` (portal)

#### Tasks (v3 — required columns: evidence_source, return_locus, return_locus_evidence)

| Task                 | Frequency | Criticality | Evidence source                                            | return_locus         | return_locus_evidence                                                                                                                                                               |
| -------------------- | --------- | ----------- | ---------------------------------------------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Pay invoice          | high      | blocking    | SOW §4 (deposit schedule); ticket PTL-217                  | external             | vendor URL `https://checkout.stripe.com/*` (SOW §4)                                                                                                                                 |
| Review/sign proposal | high      | blocking    | SOW §3 (engagement kickoff)                                | external             | vendor URL `https://app.signwell.com/*` (SOW §3)                                                                                                                                    |
| See what's happening | high      | medium      | interview 2026-03-14 §2 "I just want to know where we are" | hub                  | redirect_to /portal cited: src/pages/portal/index.astro:332 (structural evidence type 1: shipped redirect-equivalent — the dashboard renders at `/portal` and is the login-landing) |
| Find document        | medium    | medium      | SOW §2.3 (deliverable retention); ticket PTL-138           | last-visited-surface | no auto-return; user stays on document list after download                                                                                                                          |
| Check progress       | medium    | medium      | interview 2026-03-14 §4                                    | hub                  | redirect_to /portal cited: src/pages/portal/engagement/index.astro:101 (back affordance)                                                                                            |
| Contact consultant   | variable  | high        | SOW §6 (support commitments); interview 2026-03-14 §5      | external             | mailto/tel/sms schemes (unambiguous vendor terminals)                                                                                                                               |

#### Task-to-surface mapping

| Task                  | Primary surface         | Touched                         | Entry point            |
| --------------------- | ----------------------- | ------------------------------- | ---------------------- |
| Pay invoice           | `/portal/invoices/[id]` | home → list → detail → Stripe   | Email, home ActionCard |
| Review/sign proposal  | `/portal/quotes/[id]`   | home → list → detail → SignWell | Email, home timeline   |
| See what's happening  | `/portal`               | home                            | Login, bookmark        |
| Find document         | `/portal/documents`     | home → tab → list → document    | Persistent nav         |
| Check progress        | `/portal/engagement`    | home → tab → progress           | Persistent nav         |
| Contact consultant    | any                     | three-icon contact control      | Header + right rail    |
| Review past proposals | `/portal/quotes`        | home → tab → list               | Persistent nav         |
| Review past invoices  | `/portal/invoices`      | home → tab → list               | Persistent nav         |

**Note on v3 migration.** Rows 1 and 2 (pay-invoice, review-sign-proposal) declare `return_locus=external` because both tasks terminate at vendor URLs (Stripe, SignWell). With 2 of the top-3-by-frequency tasks exiting externally, R25 D1 disqualifies hub-and-spoke (NN/g definition requires hub return). See §4.4 for the v3 pattern selection.

### 1.5 Surface class: `session-auth-admin`

#### Primary tasks

1. **Triage incoming assessments** — new leads, qualification
2. **Create and send proposals** — build SOW, send to client
3. **Issue and manage invoices** — deposit, completion, retainer
4. **Track engagement progress** — milestones, touchpoints, handoff
5. **Manage clients** — view history, update notes

---

## 2. Sitemap and auth boundary

### 2.1 Sitemap (abbreviated)

```
public (smd.services)
├── /                           dashboard (marketing home)
├── /scorecard                  detail
├── /book                       form
├── /contact                    form
├── /404, /500                  error
└── /book/manage/[token]        token-auth detail

auth-gate
├── /auth/login                 form (admin)
└── /auth/portal-login          form (client)

token-auth (portal.smd.services)
├── /portal/proposals/[token]   detail
└── /invoice/[id]               detail

session-auth-client (portal.smd.services)
├── /portal                     dashboard
├── /portal/quotes              list
├── /portal/quotes/[id]         detail
├── /portal/invoices            list
├── /portal/invoices/[id]       detail
├── /portal/documents           list
└── /portal/engagement          detail

session-auth-admin (admin.smd.services)
├── /admin                      dashboard
├── /admin/assessments          list
├── /admin/assessments/[id]     detail
├── /admin/clients              list
├── /admin/clients/[id]         detail
├── /admin/quotes               list
├── /admin/quotes/new           form
├── /admin/quotes/[id]          detail
├── /admin/invoices             list
├── /admin/invoices/new         form
├── /admin/invoices/[id]        detail
├── /admin/engagements          list
├── /admin/engagements/[id]     detail
└── /admin/engagements/new      wizard
```

### 2.2 Auth boundary

| Surface class       | Middleware check       | Session cookie        | Redirect on expiry              |
| ------------------- | ---------------------- | --------------------- | ------------------------------- |
| public              | none                   | none                  | —                               |
| auth-gate           | none (render login)    | sets cookie on submit | —                               |
| token-auth          | token signature verify | none                  | `/auth/portal-login`            |
| session-auth-client | role=client cookie     | `smd_portal_session`  | `/auth/portal-login?redirect=X` |
| session-auth-admin  | role=admin cookie      | `smd_admin_session`   | `/auth/login?redirect=X`        |

### 2.3 Deep-link inventory

| URL pattern                 | Surface             | Entry vector        |
| --------------------------- | ------------------- | ------------------- |
| `/portal/proposals/[token]` | token-auth          | Email               |
| `/invoice/[id]`             | token-auth          | Email               |
| `/book/manage/[token]`      | token-auth          | Confirmation email  |
| `/portal/quotes/[id]`       | session-auth-client | Email (after login) |
| `/portal/invoices/[id]`     | session-auth-client | Email (after login) |

---

## 3. Reachability matrix

### 3.1 Invariants

- Every surface appears as From ≥1 and To ≥1.
- Entry-only and Terminal exceptions flagged explicitly.
- Every dashboard has Required=Yes rows to every sibling list.
- Every detail has Required=Yes back to its parent list.
- No dead ends unless Terminal.

### 3.2 Matrix

| From                                 | To                            | Mechanism             | Required?                         | Pattern                              |
| ------------------------------------ | ----------------------------- | --------------------- | --------------------------------- | ------------------------------------ |
| `/` (dashboard)                      | `/scorecard`                  | Inline CTA            | Yes                               | Pyramid                              |
| `/` (dashboard)                      | `/book`                       | Primary CTA           | Yes                               | Pyramid                              |
| `/` (dashboard)                      | `/contact`                    | Footer link           | Yes                               | Pyramid                              |
| `/scorecard` (detail)                | `/book`                       | Primary CTA           | Yes                               | Pyramid                              |
| `/book` (form)                       | (external calendar)           | Submit redirect       | Yes                               | External                             |
| `/portal` (dashboard)                | `/portal/quotes`              | Section card          | Yes                               | Hub-and-spoke                        |
| `/portal` (dashboard)                | `/portal/invoices`            | Section card          | Yes                               | Hub-and-spoke                        |
| `/portal` (dashboard)                | `/portal/documents`           | Section card          | Yes                               | Hub-and-spoke                        |
| `/portal` (dashboard)                | `/portal/engagement`          | Section card          | Yes                               | Hub-and-spoke                        |
| `/portal` (dashboard)                | `/portal/invoices/[id]`       | ActionCard            | Conditional (has pending invoice) | Dominant-action variant              |
| `/portal` (dashboard)                | `mailto:<consultant_email>`   | Contact icon          | Conditional (consultant assigned) | Contact-control                      |
| `/portal` (dashboard)                | `sms:<consultant_phone>`      | Contact icon          | Conditional                       | Contact-control                      |
| `/portal` (dashboard)                | `tel:<consultant_phone>`      | Contact icon          | Conditional                       | Contact-control                      |
| `/portal` (dashboard)                | `/api/auth/logout`            | Logout button         | Yes                               | —                                    |
| `/portal/quotes` (list)              | `/portal/quotes/[id]`         | Row click             | Yes                               | Master-detail                        |
| `/portal/quotes` (list)              | `/portal`                     | Back button           | Yes                               | Hub-and-spoke                        |
| `/portal/quotes/[id]` (detail)       | `/portal/quotes`              | Back button           | Yes                               | Master-detail                        |
| `/portal/quotes/[id]` (detail)       | `<signwell>`                  | Review & Sign CTA     | Conditional (status=sent)         | External                             |
| `/portal/invoices` (list)            | `/portal/invoices/[id]`       | Row click             | Yes                               | Master-detail                        |
| `/portal/invoices` (list)            | `/portal`                     | Back button           | Yes                               | Hub-and-spoke                        |
| `/portal/invoices/[id]` (detail)     | `/portal/invoices`            | Back button           | Yes                               | Master-detail                        |
| `/portal/invoices/[id]` (detail)     | `<stripe>`                    | Pay Now CTA           | Conditional (status≠paid)         | External                             |
| `/portal/documents` (list)           | `/api/portal/documents/[key]` | Row click             | Yes                               | Master-detail                        |
| `/portal/documents` (list)           | `/portal`                     | Back button           | Yes                               | Hub-and-spoke                        |
| `/portal/engagement` (detail)        | `/portal`                     | Back button           | Yes                               | Hub-and-spoke                        |
| `/admin` (dashboard)                 | `/admin/assessments`          | Nav tab               | Yes                               | Hub-and-spoke (ratified tab variant) |
| `/admin` (dashboard)                 | `/admin/clients`              | Nav tab               | Yes                               | Hub-and-spoke (tabs)                 |
| `/admin` (dashboard)                 | `/admin/quotes`               | Nav tab               | Yes                               | Hub-and-spoke (tabs)                 |
| `/admin` (dashboard)                 | `/admin/invoices`             | Nav tab               | Yes                               | Hub-and-spoke (tabs)                 |
| `/admin` (dashboard)                 | `/admin/engagements`          | Nav tab               | Yes                               | Hub-and-spoke (tabs)                 |
| `/portal/proposals/[token]` (detail) | `<signwell>`                  | Review & Sign CTA     | Yes                               | External                             |
| `/invoice/[id]` (detail)             | `<stripe>`                    | Pay Now CTA           | Yes                               | External                             |
| `/auth/login` (form)                 | `/admin`                      | Submit → redirect     | Yes                               | —                                    |
| `/auth/portal-login` (form)          | `/portal`                     | Magic link → redirect | Yes                               | —                                    |

### 3.3 Entry-only surfaces

| Surface                     | Entry vector       | Notes                    |
| --------------------------- | ------------------ | ------------------------ |
| `/portal/proposals/[token]` | Email deep-link    | Token-auth; cold arrival |
| `/invoice/[id]`             | Email deep-link    | Token-auth; cold arrival |
| `/book/manage/[token]`      | Confirmation email | Token-auth               |

### 3.4 Terminal surfaces

| Surface                     | Terminal action                      | Notes                         |
| --------------------------- | ------------------------------------ | ----------------------------- |
| Stripe hosted payment       | External redirect                    | Not part of venture IA        |
| SignWell signing            | External redirect                    | Not part of venture IA        |
| Post-signature confirmation | Auto-redirect to `/portal` within 3s | Manual fallback link provided |

---

## 4. Pattern selection

### 4.1 Surface class: `public`

| Archetype | Pattern                                     | Rationale                                     |
| --------- | ------------------------------------------- | --------------------------------------------- |
| dashboard | Pyramid (NN/g §1.4) with persistent top nav | Small content set; linear narrative from home |
| detail    | Single-page detail with inline CTA          | Single long-form; conversion CTA inline       |
| form      | Single-page form                            | Small field count; no ordering                |
| error     | Recovery-path                               | Link to `/`                                   |

### 4.2 Surface class: `auth-gate`

| Archetype | Pattern                     | Rationale           |
| --------- | --------------------------- | ------------------- |
| form      | Single-page form (centered) | One action: sign in |

### 4.3 Surface class: `token-auth`

| Archetype | Pattern                    | Rationale                                                           |
| --------- | -------------------------- | ------------------------------------------------------------------- |
| detail    | Master-detail (simplified) | Cold arrival via email; back target is external; single primary CTA |

### 4.4 `session-auth-client × dashboard` — pattern decision (v3 format)

**Chosen pattern:** persistent-tabs
**Runner-up pattern:** hub-and-spoke
**Defense:** Per §1.4, the top-3-by-frequency tasks are pay-invoice, review-sign-proposal, and see-what's-happening. The first two carry `return_locus=external` (Stripe, SignWell); only one returns to the hub. Hub-and-spoke requires hub return as its core premise (NN/g §1.1) — with 2 of 3 high-frequency tasks exiting externally, D1 disqualifies hub-and-spoke on this surface. Persistent-tabs (Material Design 3 §2.2 bottom navigation / §2.5 tabs) satisfies the task model: 4 destinations ≤ 5 (D3 threshold), cross-section switching is high (users move pay-invoice → find-document → check-progress within a session per interview 2026-03-14 §3), and no sequential ordering applies.

**Algorithm inputs (from §1.4 + §3):**

- destination_count: 4 (quotes, invoices, documents, engagement)
- primary_task_count: 6
- task_ordering: independent
- top-3-by-frequency: [pay-invoice external, review-sign-proposal external, see-what's-happening hub]
- return_locus counts in top-3: { hub: 1, external: 2 }

**Reviewer approvals (Phase 7):** Not applicable — chosen pattern equals the top-scored (surviving) pattern. No override required.

### 4.4.1 `session-auth-client × list` and `session-auth-client × detail`

| Archetype | Pattern           | Rationale                                                  |
| --------- | ----------------- | ---------------------------------------------------------- |
| list      | **Master-detail** | List → detail → back; state preserved on return            |
| detail    | **Master-detail** | Back to parent list; primary action (Sign / Pay) prominent |

**Required elements for persistent-tabs (from pattern-catalog.md §2 + pattern-disqualifiers.md):**

- Persistent nav affordance (bottom nav on mobile / top tabs on desktop) visible on every portal surface
- 4 labeled destinations: Proposals, Invoices, Documents, Progress
- `aria-current="page"` on the active tab
- Tap target ≥ 44px (mobile), minimum Material 3 dimensions
- Active indicator per Section 8 state colors

### 4.5 Surface class: `session-auth-admin`

| Archetype | Pattern                                     | Rationale                                                                                                                                                        |
| --------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| dashboard | **Hub-and-spoke with ratified tab variant** | 5 top-level sections; Material Design 3 navigation-tabs pattern permits 3–5 destinations at persistent visibility; high switching frequency in operator workflow |
| list      | Master-detail + Faceted (when count >30)    | Filter needed for large sets                                                                                                                                     |
| detail    | Nested-doll + Master-detail                 | Multi-level hierarchy uses breadcrumbs                                                                                                                           |
| wizard    | Sequential (NN/g §1.3)                      | Multi-step intake with mandatory ordering                                                                                                                        |

**Ratification note (Appendix E.1):** Admin nav tabs are the one permitted exception to the global "no nav tabs in header" anti-pattern. Rationale: admin's 5-section scope + high-frequency switching (10–20×/session) + Material's documented destination-count-and-frequency rule. Validator R6 has explicit `surface != "session-auth-admin"` guard.

---

## 5. Navigation state machine

### 5.1 Surface class: `session-auth-client`

#### Auth states

| State           | Detection                                 | Behavior                                         |
| --------------- | ----------------------------------------- | ------------------------------------------------ |
| Authenticated   | `Astro.locals.session?.role === 'client'` | Full nav per reachability matrix                 |
| Expired         | Middleware 401                            | Redirect to `/auth/portal-login?redirect=<path>` |
| Unauthenticated | No session                                | Redirect to `/auth/portal-login`                 |

#### `/portal` data states

| State                      | Condition                                     | Rendering                                                                                                   |
| -------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Empty                      | `!activeEngagement`                           | Welcome message, "Your portal will populate when your engagement begins," ConsultantBlock, no section cards |
| Error                      | DB fetch threw                                | Error card with Retry, ConsultantBlock preserved                                                            |
| Populated, pending invoice | `pendingInvoice != null`                      | ActionCard dominant, Recent Activity, 4 section cards                                                       |
| Populated, next touchpoint | `nextTouchpointAt != null && !pendingInvoice` | Touchpoint card, Recent Activity, 4 section cards                                                           |
| Populated, idle            | else                                          | "Nothing needs your attention right now," Recent Activity, 4 section cards                                  |

#### `/portal/quotes/[id]` task states

| quote.status | Primary CTA              | Body                                                       |
| ------------ | ------------------------ | ---------------------------------------------------------- |
| sent         | Review & Sign (SignWell) | Expiry countdown if set                                    |
| accepted     | —                        | "Signed on <date>"; link to deposit invoice if issued      |
| declined     | —                        | "You declined this proposal on <date>"; Contact consultant |
| expired      | —                        | "This proposal expired on <date>"; Contact consultant      |

#### `/portal/invoices/[id]` task states

| invoice.status | Primary CTA                 | Body                         |
| -------------- | --------------------------- | ---------------------------- |
| sent           | Pay Now                     | Amount + due date            |
| overdue        | Pay Now (overdue indicator) | Amount + due date (past)     |
| paid           | —                           | Paid badge, receipt download |
| void           | —                           | Voided notice                |

---

## 6. Chrome component contracts

### 6.1 Header band

- Element: `<header role="banner">`, `sticky top-0 z-50`, `h-14 md:h-16`, `bg-white`, `border-b border-[color:var(--color-border)]`.
- Left: client/workspace name, Inter 500, 13/18, `#475569`.
- Right: optional contact icons (44×44 each with `aria-label`), utility actions.
- Forbidden: logo, nav tabs (except admin), hamburger, backdrop-blur, opacity modifiers.

### 6.2 Back affordance

- Element: `<a href="<canonical-URL>" aria-label="<target-name>">` with `chevron_left` icon.
- Height: 44px.
- Position: first child of `<main>`, not in header.
- Forbidden: `href="#"`, `javascript:*`, `history.back()`, breadcrumb wrapper for single link.

### 6.3 Breadcrumbs

- Allowed ONLY on `session-auth-admin` list and detail archetypes.
- Format: `<nav aria-label="Breadcrumb"><ol>...</ol></nav>` with 2+ items.
- Separator: `chevron_right` Material icon with `aria-hidden`.
- Mobile: segment truncation at 24ch.

### 6.4 Section cards (dashboard)

- Element: `<a>` with full-card click area, `min-h-[88px]`, rounded-lg surface, border.
- Content: icon top-left, label (Plus Jakarta Sans bold), caption (muted).
- Hover: border lifts to `--color-border-strong`.
- Focus: 2px ring at 2px offset.
- Grid: 1 column mobile, 2 columns desktop.

### 6.5 Skip-to-main

- Element: `<a href="#main" class="sr-only focus:not-sr-only ...">Skip to main content</a>`.
- Position: first child of `<body>`.
- Target: `<main id="main" role="main">`.

### 6.6 Footer

- Rendered ONLY on `public` surfaces.
- 3-column desktop, stacked mobile.

---

## 7. Mobile ↔ desktop transforms

Single breakpoint: **768px** (`md:`). Higher breakpoints only for content-width adjustments, not navigation pattern changes.

| Surface class       | Mobile                                  | Desktop (≥768px)                                            |
| ------------------- | --------------------------------------- | ----------------------------------------------------------- |
| public              | Single column, hero stacked             | Two column on home; three column on footer                  |
| token-auth          | Single column, primary action prominent | Single column max 720px                                     |
| session-auth-client | Single column; sections stacked         | Two-column grid (main + right rail); section cards 2-across |
| session-auth-admin  | `<details>` menu dropdown for nav       | Inline nav tabs in header                                   |

---

## 8. State conventions

- Active: `#1E40AF` / `var(--color-primary)`
- Default: `#475569` / `var(--color-text-secondary)`
- Hover: `#0F172A` / `var(--color-text-primary)`
- Focus ring: 2px `#3B82F6` / `var(--color-action)` at 2px offset, keyboard-focus only
- Disabled: `#94A3B8` / `var(--color-text-muted)`
- Tap target minimum: 44×44px

---

## 9. Transition contracts

- Back target is canonical absolute URL. Never `#`, `javascript:*`, or `history.back()`.
- Modal: Esc + click-outside close; focus returns to trigger.
- Cross-auth-boundary navigation: full page reload (not SPA nav).
- Post-action redirects (sign, pay) target dashboard with confirmation message.

---

## 10. Anti-patterns (summary)

**Chrome (R1–R15):** fixed-not-sticky, backdrop-blur, icon before client name, breadcrumb-wrapper-single-back, href="#"/history.back(), nav tabs (non-admin), bottom-tab nav, footer on auth, real-face photos, marketing CTAs on auth.

**IA (R16–R24):** orphan destinations, dead-ends, pattern-impersonation, token-auth amnesia, taxonomy drift, state omission, heading hierarchy violation, search missing, cross-surface context loss.

Full list with rationale: [references/anti-patterns.md](../references/anti-patterns.md).

---

## 11. A11y floor

- Landmarks: `<header role="banner">`, `<main role="main" id="main">`, `<nav>` when navigation elements are grouped.
- Skip-to-main link: first body child, sr-only-until-focused.
- Heading hierarchy: exactly one `<h1>`; `<h2>` for major sections; no level skipping.
- `aria-current="page"` on active nav item if nav is rendered.
- Focus rings: keyboard-only.
- Icon-only buttons/links: always carry `aria-label`.
- Tap targets: 44×44px minimum.
- Keyboard order matches visual order.

---

## 12. Content taxonomy

### 12.1 Object names

| Entity          | Canonical label | Forbidden synonyms             |
| --------------- | --------------- | ------------------------------ |
| quote           | Proposal        | Quote, Estimate, Bid, SOW      |
| invoice         | Invoice         | Bill, Statement                |
| engagement      | Engagement      | Project, Contract, Job         |
| milestone       | Milestone       | Phase, Stage, Task, Checkpoint |
| client (entity) | Client          | Customer, Account              |
| consultant      | Consultant      | Agent, Staff, Advisor, Rep     |
| document        | Document        | File, Attachment, Artifact     |

### 12.2 Action verbs

| Action                 | Canonical verb                |
| ---------------------- | ----------------------------- |
| View proposal detail   | Review                        |
| Sign proposal          | Review & Sign                 |
| Pay invoice            | Pay                           |
| Download/view document | View (PDF) / Download (other) |
| Contact consultant     | Contact                       |

### 12.3 Status labels

| Entity  | DB value | Label          | Badge |
| ------- | -------- | -------------- | ----- |
| quote   | sent     | Pending Review | blue  |
| quote   | accepted | Accepted       | green |
| quote   | declined | Declined       | red   |
| quote   | expired  | Expired        | amber |
| invoice | sent     | Sent           | blue  |
| invoice | paid     | Paid           | green |
| invoice | overdue  | Overdue        | red   |
| invoice | void     | Voided         | slate |

### 12.4 Time expressions

| Context          | Format                               |
| ---------------- | ------------------------------------ |
| List items       | `Mon D, YYYY` (same year: `Mon D`)   |
| Relative         | `N days ago` / `Yesterday` / `Today` |
| Expiry countdown | `Expires in N days`                  |
| Touchpoint       | `DayName, Mon D at H:MM AM/PM`       |

### 12.5 Empty-state copy

| Surface              | Copy                                                                          |
| -------------------- | ----------------------------------------------------------------------------- |
| `/portal/quotes`     | No proposals yet. When we send you one, it will appear here.                  |
| `/portal/invoices`   | No invoices yet. When we issue one, it will appear here.                      |
| `/portal/documents`  | Documents will appear here as your engagement progresses.                     |
| `/portal/engagement` | No active engagement. When your engagement begins, progress will appear here. |
| `/portal` (idle)     | Nothing needs your attention right now.                                       |

---

## Appendix A — public

Chrome-allowed delta: logo in header, footer present, marketing hero imagery, CTAs addressed to prospects.

Chrome-forbidden delta: back button (home is the top), session-auth chrome.

## Appendix B — auth-gate

Chrome-allowed delta: wordmark-only centered header, single form card.

Chrome-forbidden delta: nav tabs, section cards, Recent Activity, right rail.

## Appendix C — token-auth

Chrome-allowed delta: minimal header with client name (derived from token), primary CTA, optional contact icons.

Chrome-forbidden delta: Sign out button, "welcome back" copy, section cards, session-state UI.

## Appendix D — session-auth-client (portal)

Chrome-allowed delta: three-icon contact control on right of header (mail/sms/tel) conditionally when consultant assigned.

Chrome-forbidden delta: nav tabs.

### D.1 Section card grid requirement (R16)

Portal home MUST render section cards to all four sibling list routes: `/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement`. Specialization of hub-and-spoke required for this venture. Omission is R16 violation.

## Appendix E — session-auth-admin

Chrome-allowed delta: nav tabs in header (5 items), breadcrumbs on list/detail, mobile `<details>` menu dropdown for nav.

### E.1 Admin tabs exception (ratified)

`session-auth-admin` is the ONE permitted exception to the global "no nav tabs in header" rule. Rationale: 5-section scope, high-frequency switching, Material Design 3 navigation-tabs rule (3–5 destinations with frequent switching). Validator R6 has explicit `surface != "session-auth-admin"` guard.

---

## Non-blocking follow-ups

Flagged during v2 migration:

- `/privacy` and `/terms` routes not yet created
- 404 page subdomain-aware back-link logic not yet implemented
- Scorecard wizard sticky-bottom nav refactor (Phase 0 semantic compliance)
