---
name: task-list-keeper
description: >-
  Keeps the firm's task list honest, like a case manager. Each week it sorts every
  overdue task into open, done, stale, or money-or-court at stake; closes the Operator's own
  finished tasks and hands the rest over once; and sends each attorney one numbered proposal for
  their matters, answered in plain words. Nothing on a firm task changes until a person says yes,
  and a task with money or a court date on it is never offered for closing.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, Tasks, CaseManager, Internal, Cron, ProposeThenAct]
  smd:
    vertical: law-firm
    skill_type: scheduled review (internal proposal + approved writes)
    action_class: read + internal_write
    cron: true
    connectors:
      - smokeball # PracticeManagement - tasks, matters, staff, document listing, calendar (read); update_task (approved or record-proven writes only)
---

# Task List Keeper

The firm does not lack awareness of its tasks; it lacks someone with the time to keep the list honest. A list with dozens of stale "overdue" tasks is a list nobody trusts, and the deadline digest then spends its lines on tasks that are finished, duplicated, or the Operator's own leftovers. This routine is the case manager's weekly pass over that list (spec: `docs/specs/operator/case-manager-deadline-work.md`, Jobs 1a, 1 and 3).

It is configured, never assumed: the firm's `case_manager:` block in customer.yaml turns on each part at the level it wants. No block, no work (ADR 0035).

| Part                              | Config         | Surfaces it                                | Prepares it for you                                                    | Handles it                                                           |
| --------------------------------- | -------------- | ------------------------------------------ | ---------------------------------------------------------------------- | -------------------------------------------------------------------- |
| The Operator's own tasks (Job 1a) | `own_tasks`    | named once, nothing written                | done ones proposed for closing; the rest handed over once              | done ones closed on the record's evidence; the rest handed over once |
| The firm's overdue tasks (Job 1)  | `task_cleanup` | done and stale named once, nothing written | one numbered proposal per attorney; writes only what a person approves | same as prepares: a firm task never changes without a person's yes   |
| Routine handled quietly (Job 3)   | `quiet`        | "Done since last time" line                | same                                                                   | the line plus a memo on the matter                                   |

## When to Use

Runs **scheduled** (Hermes no-agent cron, weekly on the firm's authored schedule). Never invoked interactively: the review exists only as the pre_run's envelope, and forcing it means forcing the cron job (`hermes -p operator cron run <jobid>` via seat-probe), never composing a review in a chat turn.

## Procedure

1. **Pre-run (cron, no agent).** `pre_run.py` does all of the thinking in code: it reads the `case_manager` block, pulls the overdue open tasks with their matters, staff, document listings and court calendar (`pull.py`), joins the casework ledger and the records-chase resolutions, sorts every task (`classify.py`, `references/classification.md`), plans who gets what (`review.py`), and renders every sentence (`lines.py`, `references/output-format.md`). It writes the casework envelope and the provenance handoff, then wakes the agent only when there is a message to send or a close to make. Otherwise it writes a `SUPPRESSED_WAKE` heartbeat row (`nothing_to_review`, `pull_failed`, `case_manager_unauthored`) and stays quiet.
2. **On wake.** Your Script Output says `casework_expected: true`. Call `casework_finish` with no arguments. When it returns status `writes_queued`, make exactly `writes` calls to `mcp_smokeball_update_task` (any arguments you pass are replaced by the stored write the pre_run queued, and a call beyond the queue is refused), then call `casework_finish` again. When it returns status `sent`, it has rendered and sent every message itself. You compose nothing, send nothing yourself, and write no ledger row.
3. **Memos (Job 3).** When `casework_finish` returns `memos`, call `create_memo` once per entry with exactly its `matter_id` and `text`, and nothing else.
4. **End the turn.** If `casework_finish` reports that there was no envelope, or that it refused one, end the turn without sending anything: the heartbeat row already records the run.

**Replies** to a `[Tasks]` message arrive through `matter-inbox-router`, which calls `reply_verdicts`. Code reads the reply ("yes to all", "all except 3", "leave 2"), writes `approved` and `held` rows, and queues exactly the approved writes; the turn sends the tool's `confirmation_text` verbatim. Nothing here parses a reply.

## What each class may receive

- **at_stake** (money or a court date rides on it): never listed for the firm, never closed. The deadline digest keeps watching it. An Operator-own task in this class is handed over once, with the reason "money or a court date rides on it".
- **done**: a close is proposed (firm task) or made on the record's evidence (Operator-own, level handles). The evidence atoms ride the row.
- **stale**: a close is proposed.
- **open**: "leave it open" is proposed; answering it quiets the task for `keep_quiet_days`.

An Operator-own task it cannot finish is handed over **once**, to the matter's assisting staff when one is on the matter and on the roster (else the routed recipient), as a numbered "assign it to you" line. It is never raised again.

## Trust Ceiling

**Read + internal proposal + internal writes that a person approved or the record proves; zero external send; zero legal judgment.**

The agent MAY: call `casework_finish` and follow its queue; call `create_memo` with the memo text the pre_run rendered.

The agent MUST NOT: compose or edit a review message; pass its own arguments to `update_task` (they are replaced; the gate refuses a call beyond the queue); close, reassign or re-date any task a person did not approve, except the Operator's own done task at level `handles`; write the casework ledger file (every row goes through the broker's `casework_event_append` verb); send anything to a client, an opposing party or a court.

## Safety invariants (any violation -> `fails`)

1. **Nothing at stake is closed.** A task whose subject names money or a court step, carries a priority marker, ties to a court event, or sits on a matter with a court date in the window (or an unread calendar) is `at_stake`, and the broker refuses a close proposed or closed by record on it.
2. **A firm task changes only on a person's yes.** At every level. The approval, the thread and the number are joined by code; the model never supplies a task id.
3. **The record is the evidence.** A "looks done" line names a document of the task's kind, dated on or after the task, whose every distinctive word the task also names. The atoms ride the row.
4. **Once means once.** A handover is never repeated (the broker refuses a second `named`); a line a person held stays quiet for `keep_quiet_days`.
5. **No invented content.** Every value in every line was read this run; every sentence is a constant in `lines.py`. A value the record lacks renders as its authored absence.
6. **Heartbeat integrity.** Every quiet run writes a `SUPPRESSED_WAKE` row; a heartbeat that cannot land wakes.

## Pitfalls

Treating Smokeball's creator field as "the Operator made this" (it records the consenting human for every write; the provenance stamp or the authored `own_tasks.legacy_task_ids` list is the only signal); closing a task because some document of its kind exists on the matter (one proof of service must not close every service task); offering a close on a lien or a court task; re-sending a handover.

## Verification

1. With no `case_manager` block the run is a `SUPPRESSED_WAKE` with basis `case_manager_unauthored`.
2. A lien task is never in a proposal or a close.
3. A reply "yes except 2" changes exactly the approved tasks and no others (the census diff equals the approved ids).
4. A second run after a handover sends no handover.
5. Rendering is deterministic: the same pull and ledger produce the same envelope, and every date in a line is paired with its matter in the handoff.

## References

- `references/classification.md` - the closed rules for open, done, stale and at_stake, and the evidence atoms
- `references/output-format.md` - the proposal, the handover, the closes and the "Done since last time" line
- `casework_ledger.py` - vendored, byte-identical to `operator/workspace_broker/casework_ledger.py`
- `casework_view.py` - canonical here; the deadline-miss-escalator carries the copy so both routines read the ledger the same way
- `done_since.py` - canonical here; the deadline-miss-escalator and date-prep-brief carry copies, so the "Done since last time" line reads the same in the review, the digest and the brief (pinned by its sync gate)
- `escalation_ledger.py`, `routing.py`, `digest_items.py`, `broker_writer.py`, `skill_helpers.py` - vendored copies, pinned by their sync gates
- `tests/selector_test.md` - selector targets this skill for "clean up the task list", not the deadline alarm
- `pre_run.py` + `test_task_list_keeper.py` - the no-agent decision and its tests
