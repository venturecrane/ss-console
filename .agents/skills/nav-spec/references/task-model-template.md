---
description: Template and guidance for authoring the Task Model section of NAVIGATION.md. Tasks drive IA; do not author IA before authoring tasks. v3 adds two required columns (evidence_source, return_locus) and a structural definition of "primary".
---

# Task Model

The Task Model is the first layer of the spec. It answers: _what is the user trying to accomplish on this surface, and where does that task terminate?_ IA, patterns, and chrome all follow from tasks.

Common failure mode: skipping straight to sitemaps and wireframes. The resulting IA works for the designer's mental model but not the user's actual workflow. Authoring tasks first forces the designer to name the workflow before naming the surface.

**v3 change.** Every task carries two new required columns — `evidence_source` and `return_locus` — so the pattern disqualifier rule R25 can evaluate task profiles mechanically. "Primary" is no longer an author-controlled label; it is derived structurally from `frequency` and `criticality`. See [§ Primary definition](#primary-definition) below.

---

## Template structure

In `.design/NAVIGATION.md`, the Task Model section goes at the top of the spec, immediately after the front matter and before the sitemap.

```markdown
## 1. Task model

### 1.1 Surface class: <surface-class-name>

#### Tasks

| Task                 | Frequency | Criticality | Trigger                              | Completion                            | Evidence source                                              | return_locus         | return_locus_evidence                                                                         |
| -------------------- | --------- | ----------- | ------------------------------------ | ------------------------------------- | ------------------------------------------------------------ | -------------------- | --------------------------------------------------------------------------------------------- |
| Pay invoice          | high      | blocking    | Consultant sends invoice email       | Stripe payment confirmed              | SOW §4.2 (deposit schedule); ticket PTL-217                  | external             | vendor URL `https://checkout.stripe.com/*` (SOW §4)                                           |
| Review/sign proposal | high      | blocking    | Consultant sends proposal email      | SignWell signature captured           | SOW §3.1 (engagement kickoff)                                | external             | vendor URL `https://app.signwell.com/*` (SOW §3)                                              |
| See what's happening | high      | medium      | User logs in to check status         | User views recent activity feed       | Interview 2026-03-14 §2 ("I just want to know where we are") | hub                  | redirect_to /portal (cited: src/pages/portal/index.astro:332 — the feed renders at `/portal`) |
| Find document        | medium    | medium      | User recalls receiving a deliverable | User has document open                | SOW §2.3 (deliverable retention)                             | last-visited-surface | no auto-return implemented (after download, user stays on document list)                      |
| Check progress       | medium    | medium      | User wants milestone status          | User sees milestone list              | Interview 2026-03-14 §4                                      | hub                  | redirect_to /portal cited: src/pages/portal/engagement/index.astro:101                        |
| Contact consultant   | variable  | high        | Ad-hoc question                      | Message delivered via email/SMS/phone | SOW §6 (support commitments); provisional:interview-only     | external             | mailto/tel/sms schemes (unambiguous vendor terminals)                                         |

#### Task-to-surface mapping

| Task        | Primary surface         | Surfaces touched                                            | Entry point                 |
| ----------- | ----------------------- | ----------------------------------------------------------- | --------------------------- |
| Pay invoice | `/portal/invoices/[id]` | /portal → /portal/invoices → /portal/invoices/[id] → Stripe | Email link; home ActionCard |
| ...         |
```

---

## Required columns (v3)

### `frequency`

One of: `high` / `medium` / `low` / `variable`. Drives pattern selection (R25 top-3-by-frequency).

- `high` — user performs this task ≥ weekly during active engagement.
- `medium` — weekly to monthly.
- `low` — rare or one-time.
- `variable` — genuinely unpredictable (e.g., "contact consultant"). Ranked as `medium` for R25 purposes unless a criticality override applies.

### `criticality`

One of: `blocking` / `high` / `medium` / `low`.

- `blocking` — engagement cannot proceed without the task completing.
- `high` — user considers this a primary reason for using the product.
- `medium` — expected functionality.
- `low` — convenience or administrative.

### `evidence_source` (v3, required)

The artifact that justifies the task's inclusion at the declared frequency/criticality. The rule: **"I looked at the portal and saw X" is not an evidence source.** Accepted values include:

- `SOW §<N.M>` — venture contract or scope document with section reference
- `ticket <ID>` — support ticket or client email thread
- `analytics <event-name>` — instrumented analytics event with observed rate
- `interview <path-or-ID> §<n>` — user interview transcript with section/line reference
- `provisional:SOW-scope` — pre-launch, SOW scope hypothesis; requires evidence-mode: provisional in front matter
- `provisional:design-hypothesis` — pre-launch, stated product goal; requires evidence-mode: provisional
- `provisional:interview-only` — interview data only, no SOW confirmation yet

Provisional evidence is acceptable in `evidence-mode: provisional` specs; in `evidence-mode: validated`, only non-provisional sources pass R25 structural validation.

### `return_locus` (v3, required)

Where the task terminates. Drives R25 disqualifier D1 for hub-and-spoke. One of:

- `hub` — the task ends at the dashboard/hub surface of the surface class (e.g., `/portal`).
- `last-visited-surface` — the task ends at whatever surface the user was on before starting it (no canonical landing).
- `external` — the task ends at a surface outside the venture's IA (vendor URL, `mailto:`, etc.).
- `new-destination` — the task ends at a venture surface that is neither the hub nor a prior one (e.g., a detail page the user did not start from).

### `return_locus_evidence` (v3, required for `return_locus = hub`)

Structural evidence — not prose — justifying the `hub` claim. Accepted evidence types:

1. **Shipped or designed redirect path.** Cite a specific file:line or SOW section that contains the hub URL as a literal string.
   - Example: `redirect_to /portal cited: src/pages/api/stripe/return.ts:12`
   - Example: `redirect_to /portal cited: SOW §5.3 "After payment, client returns to https://portal.smd.services/"`
2. **Interview quote.** Cite a transcript path and line with a user statement about returning to the hub.
   - Example: `interview transcripts/2026-03-14-alice.md:45 "I always go back to the main page after paying"`
3. **Analytics event.** Cite an instrumented event that fires on hub arrival post-task.
   - Example: `analytics.portal_return_after_payment`

For `return_locus ∈ {external, last-visited-surface, new-destination}`, prose citation is sufficient (the terminal surface is typically an unambiguous vendor URL or default behavior).

---

## Primary definition

**"Primary" is derived structurally, not author-declared.** The validator computes:

```
primary(task) := frequency(task) ∈ {high, medium} OR criticality(task) = blocking
```

The author cannot toggle a task's primary status without changing its frequency or criticality values. This closes a v2 failure mode where the author could demote tasks to avoid disqualifier thresholds.

**R25's disqualifier counting uses `top-3-by-frequency`, not `primary`:** for thresholds like "≥2 primary tasks with non-hub return_locus," the count operates on the three tasks with the highest frequency-rank (ties broken by criticality). This is stable against author reclassification.

---

## How to elicit tasks

Tasks are derived from non-UI sources: user research, support tickets, contracts, session recordings, analytics, or (failing those) the product owner's articulated reason the product exists.

**Forbidden.** Do not author the task model by reading `src/components/**`, `.astro` bodies, or shipped HTML. R26 (authoring-direction lint) fires if Sections 1–4 cite those paths; the IA Architect reviewer fires on paraphrased shipped-chrome claims (e.g., "the portal currently has a sidebar"). If the only available evidence is shipped chrome, declare `evidence-mode: provisional` and use `provisional:*` sources.

Good elicitation questions:

1. **"What does a good session look like for the user?"** — captures golden-path tasks
2. **"What goes wrong, and how do they get unstuck?"** — captures recovery tasks
3. **"What does the user do once a week? Once a month? Once ever?"** — captures frequency tiers
4. **"What would the user stop using the product for?"** — captures criticality
5. **"What does the user do that isn't in the product but should be?"** — captures IA gaps
6. **"Where does this task end?"** — elicits `return_locus` directly. If the user says "I go back to the home page," ask what specifically brought them there (redirect? bookmark? manual nav?) — that specificity feeds `return_locus_evidence`.

If the venture has no user research, the author drafts tasks from domain knowledge and CLAUDE.md, flags them as `provisional:*`, and sets `evidence-mode: provisional` in the front matter.

---

## Naming tasks

Tasks are named as **verb + object**. "Pay invoice," "Review proposal," "Check engagement progress," "Contact consultant." Never "View invoice page" — that's a navigation, not a task.

Tasks are named from the user's perspective. "Review and sign proposal" is correct; "Accept proposal submission" is the system's perspective.

---

## What the task model produces downstream

The Task Model constrains:

- **The reachability matrix (§3)** — every primary task's path through the IA must be a row.
- **Pattern selection (§4)** — frequency + return_locus drive R25's pattern disqualifier evaluation.
- **Chrome contract (§6)** — criticality drives affordance prominence.
- **State machine (§5)** — each task has pre/in-progress/completed states.
- **Content taxonomy (§12)** — verbs and object names come from task names.

Downstream sections must not contradict the task model. If they do, the task model is wrong (re-author) or the design diverged (reconcile). R25 enforces the pattern ↔ task-model consistency directly.
