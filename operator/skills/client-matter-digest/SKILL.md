---
name: client-matter-digest
description: >-
  Drafts a proactive client update on a matter's status. Covers what's happened, what's coming,
  and what's needed from the client, from Smokeball reads, in the firm's voice, for a human to
  send. Reports status; never advises or predicts.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Client, Status, Proactive, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: drafting (client-facing)
    action_class: read + external_send
    connectors:
      - smokeball # PracticeManagement — matters, tasks (read) for the status facts
      - email # customer-bound — the drafted client update (send is draft-for-review); calendar via the mail/calendar binding (Google/M365), not Smokeball
---

# Client Matter Digest

Drafts a proactive update to the client about where their matter stands — recent activity, upcoming dates, and anything the firm needs from them — assembled from Smokeball and written in the firm's voice for a human to send. It is the outbound, scheduled counterpart to the reactive `matter-status-responder` (which answers one client who asked). Same status facts, opposite trigger: the firm reaching out before the client wonders.

It **reports status; it never advises, predicts, or commits.** It tells the client what has happened and what is scheduled — it does not say what will happen, whether an outcome is likely, or what the client should do legally. Every fact traces to Smokeball (or the calendar binding); the message ships under a human reviewer's identity.

## When to Use

Use when the firm wants to keep clients informed on a cadence instead of waiting for "where are we?" emails — the proactive-communication habit that distinguishes a well-run practice. Most matters benefit; the firm authors which matters or practice areas get a digest and how often.

Runs scheduled (e.g., a fortnightly per-matter cadence the firm sets).

## Prerequisites

Reads Smokeball (`get_matter`, `list_tasks`) for the status facts, the **mail/calendar binding** (Google/M365) for upcoming appointments, and the customer-bound **Email** connector to draft the client update. Whether the update sends or drafts follows the firm's authored `external_send` ceiling (`draft_for_review` recommended; see `operator/references/send-posture.md`).

## How to Run

```
hermes run client-matter-digest                    # all matters due for a cadence update
hermes run client-matter-digest --matter <id>      # one matter's client update
```

## Procedure

Two phases. The per-matter fetch uses the governed connector tools directly; the client-appropriate framing and drafting stay in the agent's reasoning loop.

### Phase 1 — Fetch (mediated connector reads)

**Do NOT run the fetch through `execute_code`** — the `code_execution` action class is unauthorable on customer seats holding gateway credentials (the #1841 custody guard; ss #1917) and the call is REFUSED. For each matter due for an update, pull `get_matter` (status), `list_tasks` (open items, including anything `waiting on client`), and calendar appointments from the **mail/calendar binding** (upcoming dates the client should know) as ordinary governed tool calls, keeping each read tight (status facts and in-window dates, never full documents). A matter that can't be read is `parse_failed`; the run continues. If a firm's matter count ever makes per-matter reads untenable, that is the ss #1917 batch-fetch design conversation — never `execute_code`.

### Phase 2 — Reason (agent, in-context)

Per `references/algorithm.md` and `references/voice.md`:

1. **Select client-appropriate facts.** What progressed since the last update, what is scheduled, and — most useful — **what the firm needs from the client** (a signature, a document, a decision). Internal-only detail (billing internals, strategy notes) stays out.
2. **Frame as status, not advice.** "Your hearing is scheduled for [date]" — yes. "We expect a favorable ruling" / "you should accept the offer" — never. The line between informing and advising is the central discipline.
3. **Draft in the firm's voice** (`voice.md`) — warm, plain, professional; no legalese, no false reassurance, no invented progress. If little has happened, say so honestly rather than manufacture activity.
4. **Surface for review.** The drafted client update is surfaced; a human reviews and sends it under their own identity. Nothing goes to the client autonomously.

## Trust Ceiling

**Read + draft autonomous; client send is draft-for-review (`draft_for_review`, non-raisable).**

The agent MAY: read Smokeball status facts (and calendar via the binding); select client-appropriate content; draft the update in the firm's voice; surface it for review.

The agent MUST NOT: send to a client autonomously; give legal advice or predict an outcome; invent progress, a date, or a status; include privileged internal detail; promise firm behavior the engagement has not authored.

## Safety invariants (any violation → `fails`, no recovery)

1. **External send follows the authored ceiling.** Whether the digest sends or drafts is the firm's authored `external_send` ceiling, not a fixed rule (`draft_for_review` — shipped under a human reviewer's identity — is the recommended starting posture). See `operator/references/send-posture.md`.
2. **Status, not advice.** Reports what happened and what is scheduled; never advises, predicts an outcome, or recommends a legal action.
3. **No fabrication.** Every fact traces to a Smokeball read (or the calendar binding). A quiet matter is described as quiet, not dressed up.
4. **No internal leakage.** Billing internals, strategy, and other firm-only detail never enter a client-facing draft.
5. **No uncontracted promise.** The draft states status; it does not promise timelines or outcomes the engagement has not authored.

## Matter identifiers (projected, never composed)

- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves). Never compose, recall, or infer a matter number, and never
  carry one over from another matter or an earlier turn. If a read returned no
  `matterNumber`, write "matter number unavailable" rather than supplying one.

## Pitfalls

Drifting from "here's where things stand" into "here's what we expect / what you should do" (advice/prediction — the cardinal error); manufacturing progress to fill a quiet update; leaking internal notes or billing detail into a client message; an over-reassuring tone that implies an outcome; letting a draft send without human review.

## Verification

1. Every status fact in the draft traces to a Smokeball read (or the calendar binding); nothing invented.
2. No sentence advises, predicts an outcome, or recommends a legal step.
3. No privileged internal detail appears in the client-facing text.
4. The draft is surfaced for review; no autonomous send occurs.
5. Quiet matters are described honestly, not embellished.

## References

- `references/algorithm.md` — client-appropriate fact selection, the status-not-advice line, the cadence logic
- `references/voice.md` — the firm's client-communication voice (warm, plain, no false reassurance) _(parity fast-follow)_
- `references/output-format.md` — the client update structure _(parity fast-follow)_
- `references/test-cases.md` — fixtures incl. active matter, quiet matter, waiting-on-client, and an advice-bait case _(parity fast-follow)_
