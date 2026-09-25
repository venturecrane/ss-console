---
name: deadline-miss-escalator
description: >-
  Escalates a deadline that is near or already missed. Walks an approaching or missed
  firm-authored deadline up a ladder: re-surface, re-route, then notify a named human, so a
  critical date never slips silently. Internal-only; tracks authored dates, never computes one.
version: 0.4.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Deadlines, Escalation, Internal, Cron, DraftForReview]
  smd:
    vertical: law-firm
    skill_type: scheduled escalation (internal surfacing + named-human notify)
    action_class: read + internal_write
    cron: true
    connectors:
      - smokeball # PracticeManagement - list_tasks (read, due_date) for authored task deadlines; appointment dates via the mail/calendar binding
---

# Deadline Miss Escalator

Watches the firm's **authored** critical dates and, when one enters the escalation window or goes overdue without being handled, walks it up an escalation ladder so it cannot slip silently. It consumes the same authored dates `deadline-and-sol-tracker` surfaces; where the tracker is the standing mirror, this is the alarm that something on that mirror needs a human now.

Like the tracker, it is held to the hardest line: **it works to dates a human authored; it never computes one.** Its only arithmetic is comparing an authored date to today.

## When to Use

Runs **scheduled** (Hermes no-agent cron, ADR 0021 Stream B). A `pre_run.py` polls the authored dates each tick and only wakes the agent when a deadline actually needs a rung run - otherwise it writes a `SUPPRESSED_WAKE` heartbeat row and stays quiet. Never invoked interactively.

## The escalation ladder

Three rungs, chosen by proximity (arithmetic on authored dates only). All rungs are **internal** - nothing client- or tribunal-bound is ever sent.

1. **Re-surface** (outer window) - refresh the date on the firm-internal surface with an elevated flag, so it stands out from the standing tracker view.
2. **Re-route** (near window) - flag the matter to the responsible humans on the internal surface. Smokeball returns the responsible attorney directly (`personResponsibleStaffId`, resolved via `get_staff`), so re-route can target the matter's responsible attorney; it falls back to the firm's authored `escalation.red_flag_recipients` when no responsible attorney is set.
3. **Notify** (within the notify window, or overdue) - deliver a triaged alert to a person. Recipient selection follows the case-alert routing rule (`references/case-alert-routing.md`): under `matter_staff` routing each item routes to its matter's assigned staff (grouped one alert per recipient); under `central` routing (or when the routing block is unauthored) delivery goes to the firm's authored `escalation.red_flag_recipients`, exactly as before. Each item is numbered, and the reader answers in plain words ("got it on 1"); no code appears in the email. This is an **internal alert to a person inside the firm**, not a client message - there is no external send.

**Held matters** route to **clearance**, not the ladder: a matter on CONFLICT-HOLD with an approaching date is surfaced for human clearance and never gets a client-facing step.

## Fire once, acknowledge per item (the escalation ledger)

An alert fires **once**, then re-fires only after the firm's authored
`escalation.refire_days` window (pack default 3), never daily. State lives in the
escalation ledger (`references/algorithm.md`): a broker-owned append-only JSONL
the agent reads but never writes directly.

Acknowledgement is **per number**, in plain words. Each needs-you item carries
its own number; each "Also open" matter and each matter under "Open without a
task id" carries one number that covers all of its items. The reader replies in
the digest's own thread ("got it on 1 and 3", or "all"). The number on the
page is the `n` on the item's `fired` rows, and the broker ties those rows to
the thread the digest went out in (`references/algorithm.md`), so a reply can
only quiet items from the digest it answers. Reading the numbers out of the
reply, matching them to rows, writing the `acked` rows, and wording the
confirmation are all done in code by `reply_verdicts` (which answers every
plain-word reply, and takes the digest path on its own when the thread holds
digest rows); the turn chooses nothing and sends the tool's `confirmation_text`
verbatim. A reply that names no
number ("thanks") or an unknown number writes nothing and gets a question back.

An ack is a **snooze, not a tombstone**: the item goes quiet for
`escalation.ack_snooze_days` (pack default 7), then re-surfaces if it is still
open in Smokeball. Only resolution in Smokeball closes an item.

**Codes already sent keep working.** Digests sent before 2026-09-25 carried
per-item `ACK-XXXXXX` codes and a blanket `ESCALATION_ACKNOWLEDGED`. The rows
still carry those codes (`token`), so a reply quoting them takes the legacy
path below. New digests print no code.

## On a case-manager seat (only when `case_manager:` is authored)

A firm that turns on the case-manager jobs (`docs/specs/operator/case-manager-deadline-work.md`) moves work out of this alarm and into the routines that own it. `casework_filter.py` reads the casework ledger after the escalation-ledger join and before the wake decision, and every change below applies only when the firm's customer.yaml authors a `case_manager:` block. Without one, the run is byte-identical to a seat that never heard of it (`test_case_manager_golden.py`).

- **The Operator's own tasks leave the digest** (when `case_manager.own_tasks` is authored): a task whose subject carries the `[Operator]` stamp, or whose id is in `own_tasks.legacy_task_ids`, is closed or handed over once by `task-list-keeper`, never re-alarmed here.
- **A task the review holds leaves the digest**: a proposal is out, a person said to leave it (held, a kept row, or an approved keep, for `keep_quiet_days`), an approved write is on its way, or the task was handed over.
- **A court date with a date-prep brief leaves the digest, with a backstop.** Once every decision in the brief is answered, the date is the brief's. While any is unanswered, the date comes back here as soon as it is inside `notify_days`: an unanswered brief never silences a court date.
- **The overflow line.** When `case_manager.task_cleanup` is authored, a recipient's overdue tasks past the top five collapse into one line, "N more overdue tasks are in Monday's task review." (the day read from the firm's own `task-list-keeper` cron entry; "the next task review" when the schedule names no single day). Those tasks are not raised here (no `fired` row): they are the review's. Court dates and not-yet-overdue tasks past the top five stay in "Also open".

A seat with no `case_manager:` block (ashton-price today) sees none of this.

## Prerequisites

Reads Smokeball (`list_tasks` `due_date`) for authored task deadlines and the mail/calendar binding (`list_calendar_entries`) for appointment-style court/hearing dates, plus the firm's escalation-acknowledgment state. `python3` for the `pre_run.py` and fetch block. Internal output + the existing red-flag alert channel only. No write to funds, matters, or dates; no external send.

## Procedure

1. **Pre-run (cron, no agent):** `pre_run.py` compares each authored date to today and joins the escalation ledger. Wakes the agent iff some open, in-range item **should fire now** (never fired, or its re-fire window elapsed, or an ack has snoozed out); otherwise writes `SUPPRESSED_WAKE` and prints `{"wakeAgent": false}`. Audit-write failure falls back to wake (the date must not go dark).
2. **On wake - the alert is dispatched FOR you, deterministically (WS-RENDER).** The gate rendered everything - recipients, subject, the full triaged body per `references/output-format.md`, the ledger appends - into a dispatch envelope, and the seat delivers it out of turn through the full gate before your first tool call, then records the `fired` events itself. You compose no digest, send no alert, and append no `fired` events. Your Script Output says `dispatch_expected: true` when this is in play, and a context note tells you what was dispatched. Your ONLY remaining duties on a dispatch wake:
   - **Unroutable or fallback-routed matters** (named in the dispatch note or the wake line): `create_memo` on each such matter naming the alert and the unassigned state, per `references/case-alert-routing.md` steps 5-6. Memo, not task. Nothing else.
   - **Failure note.** If the Script Output shows `dispatch_expected: true` and NO dispatch note was injected into your context, or the note says delivery failed: send the red-flag recipient this one line with `smd_send_message`, exactly, and nothing else: "The deadline digest run failed and needs attention; no digest was delivered this run. The items are in Smokeball and the tracker view."
   - **Otherwise end the turn.** The `plans` and `digest` on the wake line are forensics, not a compose instruction.

   When the wake carries `plans` or a `digest` but **no `dispatch_expected`**, the gate could not build the rendered dispatch at all: send the red-flag recipient the same one-line failure note and end the turn. Never compose a digest to cover the gap.

   When the wake carries **no plans and no dispatch_expected** (a fail-open `decision_basis` such as `no_audit_writer_fail_open`, `suppress_heartbeat_failed_fail_open`, `customer_slug_unset_fail_open`, or `pre_run_crashed_fail_open`), the gate woke blind: do not compose a digest from memory - send the red-flag recipient the one-line failure note above. **`digest_degraded_audit_unavailable`** means the gate judged its own digest unfit to send (no matter numbers resolved) and could not record the withholding: same one-line note, nothing else. (The quiet sibling `digest_degraded_suppressed` never wakes a turn - the withholding is recorded and the ops pager carries it.)

   **A blind wake now normally carries `dispatch_expected: true` with `dispatch_variant: failure_note` (2026-09-02).** The gate renders the failure note itself and dispatches it out of turn like any other body, so you compose and send NOTHING - the first branch of this step applies, not the paragraph above. That paragraph survives only for the narrow floor where the gate could not render the note either (no authored red-flag or fallback recipient, or `render.py` unavailable), in which case no `dispatch_expected` appears.

   This changed because the instruction alone did not hold. On 2026-09-02 the Smokeball credential expired, this exact fail-open path fired, and the turn composed a digest body out of nothing and sent it instead of the one line above. An instruction to you is not a control; a rendered envelope is. Treat everything in this paragraph as a backstop to the envelope, never as the primary path.

   **Provenance boundary (unchanged).** `last_raised` in the wake payload records what THE OPERATOR raised, and only after a send succeeded: a null value is "no prior raise on this item", never "not raised". `ACK` codes remain the #1935 class: in any legacy reply you write (step 3), print only a code a tool call this run returned or the reader quoted.

3. **On a rostered internal reply (routed here by the inbox skill):**
   - **A reply in a `[Deadlines]` thread in plain words** (numbers, "all", or no number at all): call `reply_verdicts` with no arguments, then send its `confirmation_text` to the replier verbatim, and nothing else. **An empty `confirmation_text` means send no reply at all** (the tool found no verified reply, an automatic reply, or a sender who is not rostered); never compose one to fill the gap. The tool reads the reply and the thread from the verified inbound message itself; you never pass it a number, an item, or a thread. If it refuses (no digest rows in this thread, an unknown number, nothing named), its `confirmation_text` is the question to send back; send that verbatim too. Never write an `acked` row yourself for a plain-word reply.
   - **A reply quoting legacy `ACK-XXXXXX` codes or `ESCALATION_ACKNOWLEDGED`** (a digest sent before the numbered format): run the per-code procedure - resolve each code against `escalation_state` output, emit an `acked` event per code with `escalation_append` (`ack_token`), and reply enumerating what was acked and counting what remains, per the legacy confirmation template in `references/output-format.md`.
4. **Never compute, never send to a client.** No date is produced; no client/tribunal-bound message is drafted or sent.

**Manual firing.** Post-render, an interactive invocation of this skill is a deliberate no-op: the alert exists only as the pre_run's rendered dispatch, so manual firing means forcing the cron job itself - `hermes -p operator cron run <jobid>` via seat-probe - never composing an alert in a chat turn.

## Trust Ceiling

**Read + internal surface + internal named-human notify; zero date computation; zero external send.**

The agent MAY: read authored dates; compare them to today; read the escalation ledger (`escalation_state`); emit the triaged alert to the firm's authored red-flag channel; append `fired`/`acked` escalation events **with the `escalation_append` tool (or, for a plain-word reply, `reply_verdicts`, which appends in code) through the broker's validated `escalation_event_append` verb** (the broker rejects an `acked` with no prior `fired`).

The agent MUST NOT: compute or infer a deadline; send anything to a client or tribunal; move or author a date; escalate a held matter into a client-facing step; write the escalation ledger file directly (every event goes through the broker seam, so an injected reply cannot silence an alarm that never rang). **Fail-closed (ADR 0035):** if the firm has authored no `red_flag_recipients`, the notify rung has nowhere to fire - the escalator raises no named-human alert and never invents a recipient.

## Safety invariants (any violation → `fails`, no recovery)

1. **Never computes a deadline.** Every date is one a human authored; arithmetic is authored-date-vs-today only.
2. **No external send.** Every rung is internal - an internal surface write or an internal red-flag alert. Nothing client- or tribunal-bound goes out.
3. **Fail-closed notify.** With no authored red-flag recipient, no named-human alert fires (re-surface/re-route still run).
4. **Held matters route to clearance,** never a client-facing escalation.
5. **Heartbeat integrity.** Every quiet tick writes a `SUPPRESSED_WAKE` row and an audit-write failure forces wake; every firing tick writes an `EMITTED_WAKE` row best-effort, which can never suppress or delay the wake (#2253). A scheduled tick with **neither** row is the dead-man's-switch signal - the watch is advisory, never the firm's system of record (`compliance-floor.md`).
6. **Ledger writes are validated, never direct.** Every `fired`/`acked` event goes through the `escalation_append` tool to the broker's `escalation_event_append` verb; the agent never writes the ledger file and never reaches the broker socket via `execute_code` (that class is unauthored on customer seats and refused - ss #1915). An `acked` with no prior `fired` is rejected. An ack is a snooze, not a tombstone - only resolution in Smokeball is terminal.
7. **No invented urgency.** The triage orders by signals the record carries (task-label markers, consequential category, overdue age) and never manufactures an urgency the data does not state.
8. **A digest with zero resolved matter numbers is withheld, never sent.** When no matter number resolved and at least one lookup failed, the gate suppresses the wake (`digest_degraded_suppressed`) and the ops pager carries the withholding; authored absence (`no_number_on_record`) and partial failure still ship, with explicit absences. A turn never "fixes" a degraded digest by removing or supplying values.

## Pitfalls

Computing "X from the incident" to decide what is overdue (the cardinal sin - overdue is decided by an authored date passing, never a computed one); emailing the client a deadline reminder (this skill is internal - client-facing date communication is a separate, reviewer-sent concern); firing a named-human alert when the firm authored no recipient; escalating a held matter into a client step; suppressing a tick without a heartbeat row.

## Verification

1. Wake fires only when an open, unacknowledged matter has an authored date in the escalation window or overdue; quiet ticks write `SUPPRESSED_WAKE`.
2. Rungs are internal; no external/client send on any path.
3. `ESCALATION_FIRED` targets the authored red-flag recipient; with none authored, no alert fires.
4. Held matters surface for clearance, no client step.
5. No date is computed; overdue is decided by an authored date passing today.
6. Every rendered matter number equals a `matter_number` the wake payload or a this-turn read carries; an absent number renders explicit absence ("matter with no number on record" / "matter number unavailable"), never a GUID and never a supplied value.

## References

- `references/algorithm.md` - the in-range test, the ledger join + fire policy, the item identity + ack token, the broker append seam, and the never-computes line in code
- `references/output-format.md` - the triaged alert (Needs you today / Also open / dedup pointers) and the confirmation reply
- `casework_filter.py` + `test_case_manager_escalator.py` - the case-manager drops, the brief backstop, and the task-review line; `test_case_manager_golden.py` pins the unconfigured path byte for byte
- `casework_ledger.py` / `casework_view.py` - vendored copies (the ledger's canonical is `operator/workspace_broker/casework_ledger.py`; the view's lives in the task-list-keeper skill); do not edit the copies
- `escalation_ledger.py` - the shared ledger module (byte-identical to `operator/workspace_broker/escalation_ledger.py`; item_key, token, state, fire policy). Do not edit the copy; edit the canonical and restamp.
- `tests/selector_test.md` - selector targets this skill for "a deadline is slipping / escalate," not the standing tracker view
- `pre_run.py` + `test_escalator_pre_run.py` - the no-agent cron decision (arithmetic + ledger join) + the `SUPPRESSED_WAKE` heartbeat and its fallback-to-wake

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
refusal is a stalled deliverable and a full-context redraft - write it right
the first time):

- No em dashes anywhere, in any channel. Use commas, colons, or periods.
- In email, task, and memo text, refer to the matter by its NUMBER, taken ONLY
  from the `matterNumber` field the connector projected onto a record you read
  this turn (task, event, memo, file, and document reads all carry it when the
  matter resolves) - or, in this skill, from the wake payload's `matter_number`
  field, which is the same connector join performed on the gate's own pull this
  tick (ss #2390). Never compose, recall, or infer a matter number, and never
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
