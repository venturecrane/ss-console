---
name: deadline-and-sol-tracker
description: >-
  Surfaces court dates and deadlines by proximity. Shows the
  firm's authored court dates, filing deadlines, and
  statute-of-limitations dates as overdue, imminent, upcoming.
  Reflects dates a human entered; reads dates the Smokeball court-rules engine
  computed and presents them "unconfirmed: confirm with the responsible
  attorney," logging a bookkeeping memo on confirmation; never computes a
  limitation period itself.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Deadlines, Calendar, Internal, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: read + assembly (internal surfacing)
    action_class: read + internal_write
    connectors:
      - smokeball # PracticeManagement — list_tasks (read, due_date) for authored + engine-computed task deadlines; get_staff (resolve the responsible attorney's full name); create_memo (write the confirmation bookkeeping memo ONLY on attorney confirm of an engine-read date)
      - m365-mail # Email/Calendar binding — list_calendar_entries (read) for authored court/appointment dates
---

# Deadline and SOL Tracker

Surfaces the firm's **authored** critical dates — court dates, filing deadlines, response windows, and statute-of-limitations dates that a human has entered into the system — bucketed by proximity so nothing critical arrives by surprise. It is the date-awareness layer for the practice.

This skill is the **nearest of all the law skills to legal judgment**, so it is drawn with the hardest line: **it tracks dates it reads; it never computes one.** A statute of limitations is a legal determination — the date the deadline falls is the lawyer's to set. This skill reads the date the lawyer set and tells them it is coming. It does not calculate "three years from the incident," does not infer a filing window from a rule, and does not advise. Read in, surfaced out.

**Two provenances, read the same way, presented differently.** A date the skill reads is either **human-authored** (a person entered it as a task due date or a calendar entry — settled, surfaced as-is) or **engine-computed** (the Smokeball court-rules calendaring engine computed it and posted it into the matter — read, not settled). Per the 07-09 letter's Deadlines commitment, the Operator "reads those computed dates from Smokeball and confirms them with the attorney rather than computing its own." So an engine-computed date is presented **"unconfirmed: confirm with the responsible attorney,"** and on the attorney's confirmation the skill writes a bookkeeping memo (name, timestamp, date, source). Reading the engine's number is **not** computing one: the engine did the math, the skill only reads and confirms it. The never-computes line is unchanged.

## When to Use

Use when the firm wants a standing view of what is coming due across matters — the dates that, if missed, cause real harm. A principal otherwise reconstructs this by clicking through every matter's calendar; this assembles it, sourced and honest about what it can and cannot know.

Runs scheduled (e.g., a daily or Monday-morning scan).

## Prerequisites

Reads two distinct sources, kept explicit: **task-based deadlines come from Smokeball** (`list_tasks`, the authored `due_date`); **appointment-style entries** (court dates, hearings authored as calendar events) come from the **mail/calendar binding** (`list_calendar_entries` via Google/M365), not the Smokeball PM connector — Smokeball has no calendar resource (it is Outlook-native). Requires `python3` for the fetch block. Internal output only. No write to funds, matters, or dates.

## How to Run

```
hermes run deadline-and-sol-tracker                 # full scan, all open matters
hermes run deadline-and-sol-tracker --window 30d    # only dates within N days
hermes run deadline-and-sol-tracker --matter <id>   # one matter's dates
```

## Procedure

Two phases (ADR 0021 Stream A). The mechanical per-matter date fetch runs in one `execute_code` block; the bucketing and surfacing stay in the agent's reasoning loop.

### Phase 1 — Fetch (single `execute_code` block)

Enumerate open matters, then per matter pull `list_calendar_entries` (court dates, hearings, authored deadline events) **via the calendar binding** and `list_tasks` (tasks carrying a `due_date`) **from Smokeball**. Accumulate in-process; `print()` one JSON document of (matter → authored dates with their source type and date). A matter whose dates can't be read is a `parse_failed` row; the scan does not abort.

### Phase 2 — Reason (agent, in-context)

Per `references/algorithm.md`:

1. **Bucket by proximity** — `overdue` (past, still open), `imminent` (within the firm's near window), `upcoming` (within the scan window). Buckets are date arithmetic on authored dates only.
2. **Label the source and the provenance** — each date is tagged court-date / filing-deadline / SOL / task-deadline as authored. The label is read from how the human entered it; the skill does not classify a date as an SOL on its own. Each date also carries its **provenance**: **human-authored** (settled) or **engine-computed** (read from the court-rules engine's entry, identified by the engine's source tag / category on the entry — a firm-configuration fact confirmed at connect, never guessed). An engine-computed date is surfaced **unconfirmed** per the confirmation flow below; a human-authored date is surfaced settled, as before.
3. **Flag missing-where-expected** — if firm policy says a matter type should carry an SOL date and none is authored, surface **"no authored deadline on file"** for a human to address. This is the one place the skill points at an absence — and it points, it does not fill.
4. **Assemble the surface** — dates per matter, by bucket, each sourced and labeled, to the firm-internal surface per `references/output-format.md`.

## Engine-read dates: present unconfirmed, confirm, and log (never compute)

This flow adds **confirmation bookkeeping for dates the skill reads**. It adds no
computation — the engine computes the date, the skill reads it, and the attorney
confirms it. The read-not-compute invariant is untouched.

1. **Present unconfirmed.** An engine-computed date is surfaced with the explicit label
   **"unconfirmed: confirm with the responsible attorney."** It is never presented as a
   settled deadline, and nothing is written for it until the attorney confirms. The
   responsible attorney is read from the matter's `personResponsibleStaffId`.
2. **On confirmation, log a bookkeeping memo.** When the responsible attorney confirms an
   engine-read date, the skill writes one `create_memo` on the matter recording, exactly:
   - the **confirming attorney's full name**, resolved from `personResponsibleStaffId` via
     `get_staff` (never a bare staff id, never a guessed name);
   - an **ISO-8601 timestamp** of when the confirmation was captured;
   - the **confirmed date**;
   - the **source**: `Smokeball court-rules engine` for an engine-read date. (The shared
     confirmation-memo vocabulary also defines `proposed by Operator` for a by-hand-proposed
     date, but **this skill never proposes or computes a date**, so it only ever records
     `Smokeball court-rules engine`. Recording `proposed by Operator` here would mean the
     skill computed a date, which it must never do.)
3. **The memo is bookkeeping, not a calendar write.** This skill does not calendar. The
   confirmation writes the memo only; it does not create or move a calendar entry or a task,
   and it writes nothing before the confirm.

If the provenance of a read date cannot be determined (the engine source tag is not yet
configured), the skill does not guess: it surfaces the date and asks whether it is
engine-computed or human-authored, rather than silently treating an engine date as settled.

## Trust Ceiling

**Read + assemble + surface autonomous; internal-only; zero date computation. On the
responsible attorney's confirmation of an engine-read date, one internal bookkeeping memo.**

The agent MAY: read authored calendar entries and task due dates; read an engine-computed date and present it unconfirmed for the responsible attorney to confirm; bucket dates by proximity; flag a matter that lacks an expected authored deadline; write the surface to the firm-internal notes surface; on the responsible attorney's confirmation of an engine-read date, write one internal bookkeeping memo (name, ISO-8601 timestamp, confirmed date, source).

The agent MUST NOT: compute, infer, or estimate a limitation period or any deadline; propose a date of its own; advise on timeliness; move a date; send anything to a client; present a computed date as if authored; present an engine-computed date as settled rather than unconfirmed; write any confirmation memo before the attorney confirms.

## Safety invariants (any violation → `fails`, no recovery)

1. **Never computes a deadline.** Every date surfaced is one the skill **read** — either a human authored it, or the court-rules engine computed it and the skill read the engine's number. The skill does no date math beyond comparing read dates to today for bucketing. It never computes, proposes, or estimates a date of its own; reading the engine's date is not computing one.
2. **No legal advice.** It surfaces "this date is coming"; it never says whether a filing is timely or what the limitation is.
3. **Missing is flagged, not filled.** An absent expected deadline is surfaced as absent; the skill never supplies a plausible date.
4. **No fabrication.** Every date traces to a read with its source label and provenance — a Smokeball `list_tasks` `due_date`, a calendar-binding `list_calendar_entries` entry, or a court-rules-engine entry (surfaced unconfirmed).
5. **Internal + privilege.** The surface is for the firm; it stays on firm surfaces.
6. **Engine dates are unconfirmed until the attorney confirms.** An engine-computed date is surfaced "unconfirmed: confirm with the responsible attorney," never as a settled deadline; the confirmation memo (name, ISO-8601 timestamp, date, source) is written only on the attorney's confirm, never before.

## Pitfalls

Computing "X years from the incident" — the cardinal sin here; inferring a filing window from a court rule; labeling a generic calendar entry as an SOL the human didn't mark; presenting a missing deadline as though a date were known; **presenting an engine-computed date as settled instead of unconfirmed, or writing a confirmation memo before the attorney confirms**; recording an engine-read date's source as `proposed by Operator` (this skill never proposes a date); sending date reminders to clients (this skill is internal — client-facing date communication is a separate, reviewer-sent concern).

## Verification

1. Every surfaced date traces to a read source — a calendar-binding `list_calendar_entries` entry, a Smokeball task `due_date`, or a court-rules-engine entry — none computed by the skill.
2. Buckets (overdue/imminent/upcoming) are correct date arithmetic against today.
3. Source labels match how the human authored each date; no date is self-classified as an SOL.
4. Matters missing an expected authored deadline are flagged as missing, not filled.
5. Nothing is sent to a client; the surface is firm-internal.
6. Engine-computed dates are surfaced "unconfirmed: confirm with the responsible attorney," never as settled; a confirmation memo (attorney full name, ISO-8601 timestamp, confirmed date, source) is written only on the attorney's confirm, never before, and only ever with `source: Smokeball court-rules engine`.

## References

- `references/algorithm.md` — the proximity buckets, the read-only rule, the provenance / engine-confirm flow, and the missing-where-expected flag logic
- `references/output-format.md` — the by-matter, by-bucket date surface (plus the Plain-calendar and Missing-where-expected sections, the unconfirmed-engine-date marker, and the confirmation-memo shape)
- `references/test-cases.md` — the seven fixtures: overdue/imminent/upcoming bucketing, an authored SOL, a missing-expected-deadline matter, two adversarial cases (computation-bait, bare-calendar-not-deadline), an engine-date-unconfirmed case, and an engine-confirm-memo case
- `tests/selector_test.md` — selector targets this skill for a "what's coming due" date scan, not the digest or stalled-nudge

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
