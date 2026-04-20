# Admin Console — UX Brief (Swiss Functional)

_First-pass brief, authored 2026-04-19 alongside the identity sweep. Scope: the `admin.smd.services` subdomain under `role=admin`. Internal-only (Scott + future operations team). Inherits every identity commitment from `.design/DESIGN.md` and the portal brief (`.design/portal-ux-brief.md`); the per-surface rules here call out where admin diverges from portal in shape, density, or chrome._

## Context

The admin console is the operational interior of SMD Services. Leads, assessments, quotes, engagements, invoices, follow-ups, analytics, generators. High information density. Every surface is an operator's tool, not a customer surface.

Only the operator (Scott today, a small operations team later) sees admin. Identity coherence with the client portal still matters: admin and portal are one product, built by one firm, and drift between them signals carelessness. Same palette, same type stack, same chrome conventions. Density and component choice differ where operator workflows demand it.

## Scope — current surfaces

Eleven pages today. Each is a full-page composition under `AdminLayout.astro`; no shared admin primitives directory exists yet. This brief treats the existing pages as the spec and updates them in place.

| #   | Path                                | Archetype | Purpose                                                                                       |
| --- | ----------------------------------- | --------- | --------------------------------------------------------------------------------------------- |
| 1   | `/admin`                            | dashboard | Operator home. Counts, alerts, queue of work needing attention.                               |
| 2   | `/admin/entities`                   | list      | Every entity (business) in the system. Filter by stage.                                       |
| 3   | `/admin/entities/[id]`              | detail    | Single entity: stage, context timeline, contacts, assessments, engagements, quotes, invoices. |
| 4   | `/admin/entities/[id]/quotes/[qid]` | detail    | Quote builder: line items, pricing preview, SOW PDF gen, SignWell send, status transitions.   |
| 5   | `/admin/assessments/[id]`           | detail    | Live assessment workspace: entity context, schedule, live-notes textarea, completion form.    |
| 6   | `/admin/engagements/[id]`           | detail    | Engagement lifecycle: milestones, invoices, documents, context log, status transitions.       |
| 7   | `/admin/follow-ups/index`           | list      | Work queue: overdue / upcoming check-ins across engagements.                                  |
| 8   | `/admin/generators/index`           | list      | Catalog of content generators (outreach drafts, proposals, follow-ups).                       |
| 9   | `/admin/generators/[type]`          | detail    | Single generator: inputs + preview + run history.                                             |
| 10  | `/admin/analytics/index`            | dashboard | Venture analytics: pipeline conversion, delivery, financial overview.                         |
| 11  | `/admin/settings/google-connect`    | settings  | OAuth connect flow for Google Calendar / Drive integrations.                                  |

## Visit modes

- **Do the next thing** — operator logs in, scans the dashboard or follow-up queue, and acts on what's at the top.
- **Find a thing** — entities list, search by name, drill into detail.
- **Process a lead** — follow a specific entity through stage transitions (prospect → assessing → proposing → engaged → delivered).
- **Author a document** — build a quote, generate outreach, draft a proposal.
- **Log context** — append a note, transcript, or signal to an entity's timeline.

Admin does not need mobile-first design. Operators use laptops. Layouts may assume ≥1024px and degrade gracefully, not the other way around.

## Identity inheritance (from `.design/DESIGN.md`)

Everything in `.design/DESIGN.md` applies to admin. Summary of what admin gets for free from the token cutover:

- Switzer display + Switzer body + IBM Plex Mono data
- Warm near-white `#F8F8F6` background, `#0A0A0A` graphite ink
- Ochre `#1E4F5C` primary / attention / focus-ring accent
- 0 radii (sharp) across cards, buttons, and status tags
- Flat — no shadows, no gradient washes, no elevation
- Motion minimal (120ms color transitions only)

## Identity chrome conventions (admin interpretations)

Where portal and admin share a convention, the rule is stated once in the portal brief. The list below is the admin-specific interpretation or difference.

- **Masthead.** Admin uses the existing `AdminLayout.astro` sticky header: firm name + "Admin" tag on the left, nav tabs + session email + sign-out on the right. Client name does not appear there (multi-entity interior); entity context lives in the page breadcrumbs or hero.
- **Breadcrumbs.** Used on deep pages (`entities/[id]`, quote builder, assessment detail, engagement detail). Format: `Dashboard / Entities / {Entity Name} / {Leaf}`. Chevron separators, caption-sized sans. Last crumb is `text-primary` and not linked.
- **Section labels.** Mono-caps, hairline-underlined, consistent with portal. Used above grouped collapsibles (engagements, invoices, quotes, activity timeline) and between major page sections.
- **Reference lines.** Not required on every card in admin (lower-stakes than client-facing portal). Use when an object has a meaningful external ID — quotes (`REF QUOTE-2026-042`), invoices (`REF INV-1023`), engagements (`REF ENG-2026-017`), assessments (`REF ASSESS-2026-009`). Mono caps, top of card, hairline underline.
- **Status tags.** Single shared renderer: `statusBadgeClass(status)` in `src/lib/ui/status-badge.ts`. Returns a full class string — filled rectangular 2px tag with white text on tone-color background, mono caps with tracked letter-spacing. Admin maps ~20 status values across engagements, quotes, invoices, and leads to the five shared tones (info/success/danger/warning/neutral). Never wrap with extra padding or radius; the function covers it.
- **Tables.** Permitted in admin (not in portal). Used for line items, invoice lists, milestone rosters, entity rolls. Header row is `text-label` mono-caps, hairline-separated from body. Row dividers are `--color-border-subtle`. Column alignment: prose left-aligned, numbers right-aligned with `tabular-nums`.
- **Buttons.** Primary action petrol blue filled, secondary ghost with `--color-border` outline, destructive `--color-error` filled. **Stage-transition buttons are not rainbow-coded.** Difference communicated by label, not color: Promote / Book Assessment / Send Proposal / Mark Engaged / etc. all render as primary petrol blue. Lost / Dismiss / Cancel render neutral or destructive.
- **Stage / tier / context-type badges.** Local helpers in individual admin pages currently return soft Tailwind family tints (`bg-amber-100 text-amber-700`, etc.) for fine-grained admin metadata. Tinted, pill-shaped, caption-sized. **Not identity-correct** but deferred — the signal density (8 stages × 4 tiers × 13 context types) doesn't collapse cleanly into five tones, and these tags are contextual operator metadata rather than primary status. Flagged as follow-up; see §Open follow-ups.
- **Money.** Always IBM Plex Mono with `tabular-nums`, same as portal. Already rendered through `formatCurrency(...)` helpers per page.
- **Dates.** Two registers, same as portal: natural-language in prose (`April 14, 2026`), ISO in data rows (`2026-04-14`). Timestamps in activity logs use ISO with optional time suffix.

## Data density

Admin pages are denser than portal pages by intent. Operator workflows need to see many things at once: an entity's stage + context timeline + contacts + assessments + engagements + quotes + invoices + documents on one page. Pages tolerate:

- 5+ distinct collapsible panels (engagements, quotes, invoices, documents, context)
- Wide tables up to full content width
- Sidebars for stage transitions and quick-action buttons
- Inline forms for adding notes, updating stage, changing status

What admin does NOT tolerate (same as portal identity):

- Stock imagery, testimonials, marketing chrome
- Progress bars (milestone status is a tag, not a bar)
- Gradient backgrounds, elevation, shadow-lg
- Rounded pill badges
- Mixed typography registers within one information block

## Anti-patterns (admin-specific, additional to the identity list in `.design/DESIGN.md`)

- Color-coded stage-transition buttons. Label-first, petrol blue-uniform.
- Pastel tinted cards as the main container (tinted cards are fine for inline error / success banners only).
- Deep modal stacks. Admin prefers full-page navigation with breadcrumbs over nested modals.
- Mobile-first layouts. Desktop is the primary target.
- Hidden-until-click affordances for common actions. If an operator does it twice a day, it's above the fold.
- Decorative emoji or icon-only buttons. Text labels over icon-only for every primary action.

## Copy tone

Same voice as portal: human, direct, evidence-over-reassurance, no em dashes, no AI filler. Admin copy is slightly more terse (operator is the reader, not the client). Concrete examples:

- Empty state on entities list: "No entities in this stage."
- Error on failed save: "Save failed. {reason}."
- Confirmation on stage change: "Moved to Proposing."
- Destructive confirm: "Cancel this engagement? Milestones and invoices remain on file."

Never:

- "Congrats! You did a thing!"
- "Looks like…"
- "Please try again later" (state the actual reason)
- "All good!" (what's good? say what happened)

## Error states

Every failing admin action must surface a concrete reason and an immediate next step. Inline errors in page top-strips preferred over toasts for operator actions (toasts are ephemeral; operators need the error to stick until the issue is resolved).

## Success criteria

- Every admin page renders in Switzer + Switzer + IBM Plex Mono with no Inter / Plus Jakarta fallback.
- Every status tag renders rectangular 2px (no pills anywhere).
- Every monetary value renders mono tabular.
- Palette reads warm: no cool slate or indigo anywhere in chrome or page body.
- All buttons are petrol blue primary, neutral ghost, or error red. No rainbow.
- Admin operator moves through a full lead-to-engagement lifecycle (prospect → assessing → proposing → engaged) and the visual continuity is uninterrupted.

## Open follow-ups

- **Local tag helpers** (stage, tier, context-type) still return soft Tailwind tints. Collapse to the shared tone scale OR accept tinted admin-specific tags as a tolerated exception. Decision deferred.
- **Admin primitives directory.** `src/components/admin/` does not exist. Consider extracting shared pieces (breadcrumb header, collapsible detail panel, stage-transition button bar, activity-log entry, money column) after the next iteration of admin work. Premature now — scope not yet stable.
- **Dashboard `/admin` index.** Currently a basic landing; should eventually show a real operator-facing home with "next thing to do" prominent, overdue follow-ups, pending signatures, revenue run-rate. Out of scope for this identity sweep; captured for next iteration.
- **Analytics surface.** Same story — charts currently render with default palette. Chart colorways need a pass once a charting library is chosen; no charts ship in this sweep.

## Approver

Scott Durgan. Admin pages reviewed visually by running `npm run dev` and navigating `/admin/*` while logged in as admin.

---

## Appendix: Admin token map

Admin inherits every token from `.design/DESIGN.md`. No admin-specific tokens. Any color / type / spacing / radius value in admin must map to a token in `src/styles/global.css` `@theme` — hardcoded Tailwind family colors outside of the inherited tinted-badge helpers are not permitted.

## Appendix: Helper functions

| Helper                             | Location                        | Output                                              |
| ---------------------------------- | ------------------------------- | --------------------------------------------------- |
| `statusBadgeClass(status)`         | `src/lib/ui/status-badge.ts`    | Full rectangular tag class list (identity-correct). |
| `formatCurrency(n)` / `formatDate` | Per-page (currently duplicated) | ISO dates and USD amounts.                          |

The per-page duplication of `formatCurrency` and `formatDate` is a follow-up opportunity — extract to `src/lib/format.ts` when the next admin sweep happens.
