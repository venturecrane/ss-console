# Issue #377 — closure audit (2026-04-17)

Cross-references each AC from #377's 2026-04-14 scope clarification against
the current codebase + docs state. Written to ground the final close after
the issue was reopened twice by `unmet-ac-on-close.yml`.

This audit walks the 14 ACs in order. Each item gets: **status**
(done / outstanding / out-of-repo), **evidence** (file path + line or PR
ref), and **notes** when the status is nuanced.

## Status at a glance

| AC                                             | Status               | Evidence                                                                                                                                           |
| ---------------------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Audit report committed                         | done                 | `docs/audits/client-facing-content-2026-04-15.md`                                                                                                  |
| Covers Pattern A + B                           | done                 | same file, Pattern A (33 findings) + Pattern B (9 findings) tables per pattern                                                                     |
| Every finding resolved or tracked              | done                 | #378, #398, #408, #419, #420, #429 ship the fixes; this PR closes the residuals via #430/#431                                                      |
| Move 1 — TODO(#NNN) merge gate                 | done                 | `.github/workflows/scope-deferred-todo.yml`                                                                                                        |
| Move 2 — unmet-AC reopen gate                  | done                 | `.github/workflows/unmet-ac-on-close.yml` (has reopened this issue twice)                                                                          |
| Move 3 — PR template with AC checklist         | done                 | `.github/PULL_REQUEST_TEMPLATE.md`                                                                                                                 |
| Move 4 — empty-state pattern doc               | done                 | `docs/style/empty-state-pattern.md`                                                                                                                |
| Move 5 — retroactive closed-issues sweep       | done                 | `docs/audits/closed-issues-unmet-ac-2026-04-15.md` + `triage-execution-log-2026-04-15.md`                                                          |
| CLAUDE.md names both patterns                  | done                 | `CLAUDE.md` lines 47-68 "No fabricated client-facing content" section                                                                              |
| Global guardrails doc with two-pattern framing | out-of-repo          | lives in `crane-console/docs/instructions/guardrails.md` — tracked as separate work in this PR's description                                       |
| CODEOWNERS for `src/pages/portal/**`           | done                 | `.github/CODEOWNERS` routes portal/components/lib portal paths to `@smdurgan-llc`                                                                  |
| Schema migration for authored content          | done                 | `migrations/0021_quotes_authored_content.sql` (schedule, deliverables, engagement_overview, milestone_label)                                       |
| Admin UI authors new fields                    | done                 | `src/pages/admin/entities/[id]/quotes/[quoteId].astro` has schedule/deliverables row editors + engagement_overview + milestone_label inputs        |
| Existing quotes backfilled or flagged          | done via empty-state | null values render nothing (empty-state pattern); send-gate at `src/lib/db/quotes.ts:480-488` prevents sending new quotes without authored content |

## Detailed notes

### "Every finding resolved or tracked"

The 2026-04-15 audit enumerated 42 findings (33 Pattern A + 9 Pattern B).
Remediations land across several PRs:

- **#378** (hotfix) — stripped the 3-week schedule literal from the proposal
  page. Original incident.
- **#384** — added the schema migration for `quotes.schedule`,
  `quotes.deliverables`, `quotes.engagement_overview`, `quotes.milestone_label`.
  Added the admin authoring UI and the draft→sent send-gate.
- **#408** — first pass remediation of Pattern A strings named in CLAUDE.md.
- **#419, #420** — Pattern B remediations for consultant-name fallbacks.
- **#429** — Stitch-matrix rewrite of 7 portal surfaces; removed the
  consultant-name identity fallbacks and folded the empty-state pattern into
  every surface.
- **This PR's companion (#431)** — residual Pattern B findings: invoice
  subtitle `scope_summary` borrow, invoice send-gate, SOW template Captain
  decision documented, 3 new forbidden-string regression patterns.

### Pattern A residuals in the SOW PDF

The audit flagged several Pattern A sentences in `src/lib/pdf/sow-template.tsx`
(start-date confirmation, stabilization period existence, deposit-invoice
workflow). These were reviewed and retained as **authored standard-practice
contractual template language**, parallel to CLAUDE.md Rule 3's explicit
exemption for signed contracts. The rationale is documented:

- Inline in the SOW template source (`src/lib/pdf/sow-template.tsx` near the
  TERMS section — added in #431).
- In the template spec (`docs/templates/sow-template.md` — clarified in #431).

Captain decision ratified by this PR's description.

### Global guardrails update (out-of-repo)

The AC calling for the "global guardrails doc" update points at
`crane-console/docs/instructions/guardrails.md`, which lives in a different
repository. That update is out of scope for this `ss-console` PR — it is
tracked as a separate work item to land in `crane-console` with a new
"Client-Facing Content Fabrication" section mirroring the ss-console CLAUDE.md
framing.

## Recommendation

All in-repo ACs for #377 are complete. The issue can be closed with a
reference to this audit. The cross-repo global-guardrails task is tracked
separately — not a blocker for closing #377 in `ss-console`.
