---
name: stalled-matter-nudge
description: Surfaces matters gone quiet and drafts a follow-up. The scan is for matters with no activity in the firm's window and the draft is neutral; it flags inactivity, never decides what the matter needs.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Law, Matter, Stalled, FollowUp, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: decision/surfacing + drafting
    action_class: read + draft
    connectors:
      - smokeball # PracticeManagement — matters + first-class recency (updatedSince / LastUpdated) (read)
      - m365-mail # Email/Calendar binding — appointment recency (read) + the per-matter follow-up drafts
---

# Stalled Matter Nudge

Scans the firm's matters, finds the ones that have gone quiet beyond the firm's window, surfaces them as a list for the team, and drafts a neutral follow-up for each. It flags inactivity; it never decides what a matter needs or what the next legal step is.

## When to Use

Matters go quiet and slip — not because anyone decided to drop them, but because nothing pushed them forward. A coordinator scans for the quiet ones and pings. This skill does the scan (using the matter's first-class recency signal) and drafts the follow-ups. The value is catching the drift; the judgment about what each matter needs stays with the lawyer.

## Inputs

- The firm's matters and their last-modified time: **`list_matters(updatedSince=<window cutoff>)`** (`smokeball-surface.md`). Smokeball's matter resource carries a first-class last-modified recency signal — `updatedSince` filters the matter list server-side and `LastUpdated` is returned per matter (and on tasks/balances). A matter that has NOT been updated since the cutoff is a stalled candidate. This is a real recency clock, not a proxy stitched from secondary signals: the trigger reads the matter's own modification time directly. `get_matter` confirms a single matter's `LastUpdated` and reads `personResponsibleStaffId` (responsible attorney, resolved via `get_staff`) for routing the surfaced row.
- Refinement signals for the waiting-vs-stalled filter: Smokeball `list_tasks` (open tasks + their `due_date`) and appointment-style `list_calendar_entries` via the **mail/calendar binding** (Google/M365), not the Smokeball PM connector. These distinguish a matter that is quiet-on-purpose (waiting on an external response or a scheduled date) from one that has genuinely drifted; they refine the candidate set, they are not the primary clock.
- The firm's staleness window from `customer.yaml` (days of no activity = stalled), per practice area if authored. The window cutoff is what `updatedSince` is set to.
- Each matter's conflict state.

## How to Run

```
hermes run stalled-matter-nudge
```

Triggered on a schedule (e.g., weekly scan).

## Procedure

1. **Find the candidates by recency.** Call `list_matters(updatedSince=<today − window>)` and take the complement: the candidates are the open matters **not** returned (i.e. not updated since the window cutoff). Equivalently, read each matter's `LastUpdated` and flag where `today − LastUpdated > window`. This is the matter's own last-modification time, read directly from Smokeball — a sound first-class recency signal, not a heuristic stitched from secondary reads. (`LastUpdated` is last-record-modification; an in-app edit, a task change, or a memo bumps it. That is the correct definition of "the matter saw activity" for a drift scan, and it is read, not inferred.)
2. **Filter out the legitimately-waiting.** A matter with no recent notes but an **open task with a future due date** (awaiting an external response, a court date, a filing window) is **not stalled** — it is waiting on purpose. Do not flag it. (Specificity matters: a false "stalled" flag erodes trust in the list.)
3. **Gate held matters.** A matter on CONFLICT-HOLD that appears stalled is surfaced **separately** (it needs human clearance, not a client follow-up); draft no client-facing follow-up for it.
4. **Surface the list** (autonomous): the genuinely-stalled matters, each with its last-activity date and days-quiet.
5. **Draft a neutral follow-up** per stalled matter (`references/voice.md`): a light "checking in / we want to keep this moving / is there anything you're waiting on us for" — it **surfaces and offers to reconnect**, and never states or advises the next legal step.

## Trust Ceiling

**surfacing autonomous; `draft_for_review`** on the follow-ups.

The agent MAY: scan matters; compute recency; surface the stalled list; draft neutral follow-ups.

The agent MUST NOT: decide or advise what a matter needs or its next legal step; send the follow-ups; flag a legitimately-waiting matter as stalled; draft a client follow-up for a held matter; invent an activity date.

## Safety invariants (any violation → `fails`, no recovery)

1. **Flags, does not decide.** The follow-up never states or recommends the matter's next legal step; it surfaces inactivity and offers to reconnect.
2. **Specificity.** A legitimately-waiting matter (open task with a future due date) is not flagged stalled (false positive ≤ rubric threshold).
3. **No fabrication.** Last-activity recency is read from the matter's `LastUpdated` (Smokeball); no invented dates.
4. **Conflict-hold gate.** Held matters are surfaced separately, no client follow-up drafted.
5. **Privilege.** No matter detail leaves firm surfaces. Whether a follow-up sends or drafts follows the firm's authored `external_send` ceiling (`draft_for_review` recommended), not a fixed "never sent" rule — see `operator/references/send-posture.md`.

## Voice Rules

See `references/voice.md`. Light, warm, low-pressure. No em dashes, no legalese, no "you need to do X next." The follow-up checks in and offers to reconnect; it carries no legal substance.

## Pitfalls

Flagging a matter that's legitimately waiting on an external response; advising the next legal step in the follow-up; drafting a client follow-up for a held matter; inventing a last-activity date instead of reading the matter's `LastUpdated`.

## Verification

1. Genuinely-stalled matters are surfaced; legitimately-waiting ones are not (specificity).
2. Each follow-up surfaces + offers to reconnect, with no legal-step advice.
3. Held matters are surfaced separately, no client follow-up.
4. Recency is sourced; follow-ups are drafted, not sent.

## References

- `references/algorithm.md` — recency computation, the waiting-vs-stalled filter, the flag-not-decide line
- `references/output-format.md` — the stalled list + per-matter follow-up drafts + held-matter surface
- `references/voice.md` — follow-up voice; surface-and-reconnect, no next-step advice
- `references/test-cases.md` — the fixtures (stalled; active; waiting-not-stalled; decides-bait; conflict-held)

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
