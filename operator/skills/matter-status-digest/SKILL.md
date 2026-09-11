---
name: matter-status-digest
description: Assembles a periodic internal digest of all matters. It covers the firm's matters from Smokeball — open matters by stage, upcoming dates, quiet matters, low-trust and held flags. Reports state; never decides legal next steps.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Matter, Digest, Internal, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: read + assembly (internal digest)
    action_class: read + internal_write
    connectors:
      - smokeball # PracticeManagement — matters, tasks, stages, billing/AR, native trust balance (read)
    # Calendar comes from the mail/calendar binding (Google/M365), not Smokeball.
    # Shared-core candidate. This is law's concrete realization of the status
    # "spine" role. It is NOT the marketing `status-report-assembler` (that skill
    # is `vertical: marketing-agency` — Asana/GA4/paid-media, client-facing
    # weekly reports). The shared digest core is EARNED at vertical-2, not
    # designed up front (ADR 0038 §7). Until then this is the law delta.
---

# Matter Status Digest

Assembles a periodic **internal** digest of the firm's matters from Smokeball: what's open and where it stands, what's coming due, what's gone quiet, what's low on trust, and what's held for conflict clearance. It is the principal's "state of the practice this week" view. It **reports state; it never decides or recommends a legal next step** — surfacing a quiet matter is connective work, deciding what that matter needs is the lawyer's.

This is the proactive counterpart to the wedge's reactive `matter-status-responder` (which answers one client's "where are we"). Same Smokeball reads, opposite direction: one client asking outward vs. the principal scanning inward.

## When to Use

A principal running a book of matters loses the forest for the trees: which matters are advancing, which are waiting on the firm, which on the client, which have gone silent, which are running low on retainer. Reconstructing that by clicking through Smokeball every week is the bottleneck. This skill assembles it — sourced, current, and honest about what Smokeball can and cannot tell — so the principal reads one digest instead of auditing the system.

Runs on the firm's cadence (e.g., a Monday-morning scan).

## Prerequisites

Reads Smokeball (`list_matters`, `get_matter`, the stage model via `list_matter_types` → `get_stage_sets` → `get_stage_to_matter_mappings`, `list_tasks`, and for AR `get_matter_billing_config` + `get_fees` + `get_expenses`), the **mail/calendar binding** (Google/M365) for upcoming appointments, and — for the low-trust flag — Smokeball's **native** trust read `get_matter_balances` (`availableBalance`). Trust is a separate read from AR: `get_matter_balances` is trust; `get_matter_billing_config`/`get_fees`/`get_expenses` is AR, and an outstanding AR balance is not a low trust balance (see `operator/verticals/law-firm/smokeball-surface.md`). Requires `python3` for the fetch block. Internal output only — this digest is for the principal, not a client.

## How to Run

```
hermes run matter-status-digest                  # full scan on cadence
hermes run matter-status-digest --status open
hermes run matter-status-digest --window 7d      # "what changed / came due" window
```

## Procedure

Two phases (ADR 0021 Stream A). The mechanical per-matter Smokeball fetch runs in one `execute_code` block so per-matter reads never flood context; the sectioning and flagging stay in the agent's reasoning loop.

### Phase 1 — Fetch (single `execute_code` block)

Enumerate matters (`list_matters`, filtered by `--status`), then per matter pull `get_matter` (status, `personResponsibleStaffId`, `versionId`), the stage for the matter (via the stage model — see below), `list_tasks` (open tasks + `due_date`), upcoming calendar entries from the **mail/calendar binding** (Google/M365), and AR via `get_matter_billing_config` + `get_fees` + `get_expenses`. Pull the native `get_matter_balances` (`availableBalance`) per matter for the low-trust flag. Recency for the quiet-matter flag uses `list_matters(updatedSince)` / `LastUpdated`. Accumulate in-process; `print()` one JSON document. A single matter's read failure is a `parse_failed` row; the scan does not abort.

> **Stage join.** Smokeball stage is not a flat field on the matter. It is resolved through the stage model: `matterTypeId` → stage sets (`get_stage_sets`) → stage-to-matter mappings (`get_stage_to_matter_mappings`). The digest performs this join explicitly to label a matter's stage; it never reads a stage string directly off the matter record. Responsible attorney (`personResponsibleStaffId`) and last-activity (`updatedSince`/`LastUpdated`) are first-class in Smokeball — no field-widening caveat applies (unlike the prior Clio surface).

### Phase 2 — Reason (agent, in-context)

Per `references/algorithm.md`, assemble the digest:

1. **Group by stage/status** — open matters bucketed by their Smokeball stage (resolved via the stage-model join: `matterTypeId` → stage sets → stage-to-matter mappings); counts up top.
2. **Upcoming dates** — tasks with a near `due_date` (from Smokeball) and appointments from the calendar binding in the window, per matter. Sourced dates only; no invented deadlines.
3. **Quiet matters** — reuse `stalled-matter-nudge`'s recency model: Smokeball's native `updatedSince`/`LastUpdated` floored by latest calendar entry. Flag matters past the firm's window that are NOT legitimately waiting (open task with a future due date = waiting, not quiet).
4. **Low trust** — matters whose native `get_matter_balances` `availableBalance` is below the firm's floor (read-only; never a fund movement). This is trust, separate from AR.
5. **Held** — matters on conflict-hold surfaced in their own section (need human clearance, not a status line).
6. **Write the digest** to the firm's internal notes surface per `references/output-format.md`. Internal only.

## Trust Ceiling

**Assemble + surface autonomous; internal-only; `draft_for_review` if ever delivered externally.**

The agent MAY: read Smokeball (matters, stages, tasks, AR) + the native trust balance + the calendar binding; compute recency and flags; write the digest to the firm-internal notes surface.

The agent MUST NOT: recommend or decide a matter's next legal step; move or touch trust funds; send anything to a client; invent a date, balance, or stage; present a degraded signal as a precise one.

## Safety invariants (any violation → `fails`, no recovery)

1. **Reports, does not decide.** The digest states where matters stand and flags the quiet/low/held ones; it never prescribes the next legal step.
2. **No fabrication.** Every stage, date, and balance traces to a Smokeball read. Missing data is shown as missing, not filled.
3. **Trust read-only.** The low-trust flag reads `get_matter_balances` (`availableBalance`); zero fund-movement calls (`create_transaction`/`protect_funds`/`unprotect_funds` never invoked), enforced independently of connector capability. Trust stays separate from AR.
4. **Held matters gated.** Conflict-held matters are surfaced separately, not folded into the normal status sections.
5. **Internal + privilege.** The digest is for the principal; matter detail stays on firm surfaces.

## Pitfalls

Recommending what a quiet matter needs (flag only); reading a stage label directly off the matter instead of performing the stage-model join; conflating AR (`get_matter_billing_config`/`get_fees`/`get_expenses`) with trust (`get_matter_balances`); inventing a due date when `list_tasks` is sparse.

## Verification

1. Every open matter appears once, in the right stage bucket.
2. Upcoming dates, quiet flags, low-trust flags, and held matters are each sourced; none invented.
3. No legal-next-step recommendation appears anywhere.
4. The stage label for each matter is resolved via the stage-model join, not read off a flat field; the low-trust flag reads `availableBalance`, not AR.
5. The principal reads the practice's state in a few minutes without opening Smokeball.

## References

- `references/algorithm.md` — sectioning rules, the quiet/low/held flag logic, the recency reuse + caveat
- `references/output-format.md` — the digest structure (stage buckets, dates, flags, held) _(parity fast-follow)_
- `references/test-cases.md` — synthetic matter sets (advancing / waiting / quiet / low-trust / held) _(parity fast-follow)_

## Delivery channels + refusal fallback (law seat rule)

Email is a citation-free channel. Any output delivered by email (create_draft,
a reply, a chase, an attorney-confirm note) states the governing rule in plain
words ("responses are due 30 days from service by mail, plus five calendar
days for mail service; confirm before relying") and never as a citation: no
section numbers, no "CCP"/"CRC" references, no rule-format strings. The mail
channel enforces the legal-citation filter and will refuse the draft. Statute
citations belong only in matter-internal artifacts (memos, internal notes,
tasks). Write the FIRST draft citation-free; do not write a cited draft and
wait for the gate to teach you.

Three more first-draft rules, same rationale (the gates enforce them; a
refusal is a stalled deliverable and a full-context redraft — write it right
the first time):

- No em dashes anywhere, in any channel. Use commas, colons, or periods.
- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves). Never compose, recall, or infer a matter number, and never
  carry one over from another matter or an earlier turn. If a read returned no
  `matterNumber`, write "matter number unavailable" rather than supplying one.
  Never refer to the matter by its case caption. The matter's own caption is
  acceptable inside matter memos; cited case law is never acceptable anywhere.
- State a specific dollar figure only when it exists in an authored source
  on the matter, and name that source in the same sentence ("per the MedFin
  payoff letter dated..."). Never total, estimate, or round figures into
  existence.

If a delivery tool refuses a draft or write (citation filter, banned-typography
gate, or any other content gate): do not retry the same content, and do not
drop the work. Redraft once, and the redraft KEEPS every captured fact: the
matter, the document type, the service or event date, the method, and any
proposed deadline stated in plain words. Strip only the flagged content class
(citation formatting becomes plain words; banned punctuation becomes plain
punctuation). A delivered draft that drops the facts is the same failure as no
draft at all. If refused twice, deliver the minimal factual note (matter,
document or work item, date and method read, where the detail lives) so a
person always learns both that the work happened and what was read.

Never state that a follow-on action is handled (tracked, calendared, logged,
queued) unless the corresponding write succeeded or a specific skill run was
actually initiated; otherwise say plainly that the step still needs doing and
who or what owns it.
