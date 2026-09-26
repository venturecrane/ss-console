---
name: date-prep-brief
description: >-
  Prepares the firm for a date entering its window. When a court date or deadline on a matter's
  calendar comes within the firm's authored window, it reads the matter's file status from what
  the prep routines already produced, runs the prep steps the firm set to "Handles it", and sends
  the matter's responsible attorney (assisting staff copied) one short brief: what is done, and at
  most two numbered decisions, answerable in words. One matter per run. Never computes a date,
  never sends outside the firm, never offers a step the firm did not turn on.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, PI, Deadlines, CaseManager, Prep, Internal, Cron, FailClosed]
  smd:
    vertical: law-firm
    addon: pi
    skill_type: scheduled prep (file-status read + one internal brief)
    weight: medium # one matter's documents read per run, plus at most the steps the firm set to handles
    action_class: read + internal_write # reads one matter; the brief goes to firm staff only through casework_brief; steps at handles make the routines' own internal writes
    content_ceiling: connective # status and questions about the file; never legal work product, never advice
    cron: true
    connectors:
      - smokeball # PracticeManagement - calendar events, files, memos, tasks, staff (read); the step routines' own writes
---

# Date Prep Brief

A great case manager does not tell an attorney that a date exists. Smokeball already
shows the date. They find out whether the file is ready for it, do the parts they
can, and bring the attorney only the decision that needs the attorney. This routine
is that, for one matter per run (spec: `docs/specs/operator/case-manager-deadline-work.md`,
Job 2).

## What the pre-run already did

The gate (`pre_run.py`) ran before you woke and did everything code can do. It is
the only reason you are awake: no authored `case_manager.date_prep`, no date in
window, nothing to offer, or no routable owner, and you would not be running.

- It picked **one** date on **one** matter: the nearest date entering the window
  that has never been briefed.
- It read that matter's **file status** as facts: file names and dates, the day
  each prep routine last ran on this matter (its own `[Operator]` memo marker), and
  the records-request roster with each provider's chase count and last chase day.
- It built the **closed decision catalog**: the only steps you may offer, each with
  the level the firm set and the facts (`basis`) that made code offer it.
- It resolved the recipients (the matter's responsible attorney, assisting staff
  copied) and wrote the envelope `casework_brief` reads. You never see or
  type an address.

All of it is on the Script Output line under `date_prep`: `matter_number`,
`event` (`event_id`, `date`, `subject`, `days_out`), `file_status`, and `catalog`.

## What you do

1. **Read the one matter.** `get_matter` for its caption, then read the documents
   the catalog and file status name (`read_document` on those file ids). Witness
   names, exhibit contents and what a record says are document content: they are
   yours to read on this matter and nobody else's. Read no other matter.
   `get_memos_on_matter` on this matter is allowed when a question needs it.
2. **Run the steps at `handles`.** For each catalog entry whose `level` is
   `handles`, read that routine's procedure with `read_file` on
   `/app/skills/<skill>/SKILL.md` and carry it out on this matter with the entry's
   `params` (see `references/decision-catalog.md` for what each step is). Internal
   writes only, under the routine's own rules and ceilings. Run them in the order
   the catalog lists them, and **right after each one finishes, call
   `casework_step_done`** (no arguments): it records that step, backed by the memo
   its routine filed on this matter, so a person hears it was done even when no
   brief goes out. If it says no memo was filed, the step did not finish; do not
   call it again for that step. If you cannot read the skill file, or the step
   fails, say so as a done line ("I could not finish the binder: ...") rather
   than approximating it.
3. **Choose at most two decisions** from the catalog entries at `prepares`: the one
   or two a great case manager would actually bring the attorney. Each question is
   one plain sentence the attorney can answer in words, naming what the Operator
   will do on a yes ("If yes, I'll finalize it for your paralegal to serve."). Never offer
   anything not in the catalog; the tool refuses it.
4. **Call `casework_brief` once** with `done` (short lines, what is ready, each a
   fact you read this turn; a step you recorded with `casework_step_done` is
   listed for you in the catalog's own words, so do not repeat it) and `decisions`
   (`[{catalog_id, question}]`, one or two). Code renders the subject, the frame
   and "Reply here and I'll take it from there." You write no greeting and no
   sign-off.
5. **If no decision remains** after step 2, call nothing. No decision, no message:
   the spec's rule 7. The gate will not wake again for this date today. The steps
   you recorded are not lost: the next message the attorney gets (the daily
   deadline digest, the task review, or the next brief on this matter) carries
   them as one "Done since last time" line, rendered from the record.

Full brief shape and wording rules: `references/output-format.md`.

## Rules that do not bend

- **One matter.** The fence refuses a second matter's content in one session, and
  it is right to: never read, name or compare another matter.
- **Traceable or absent.** Every caption, name, date and document title in the brief
  was read this turn from this matter. A date comes from the event, a file, a memo
  marker day or a chase day in the facts. If a fact is unknown (`file_status.unread`
  names a part the gate could not read), say it is unknown; never fill it.
- **Levels bind.** A step at `surfaces` is never in the catalog and never done. A
  `prepares` step is offered and, on a yes, produces a draft a person reviews. A
  `handles` step is done in step 2. Exposure ceilings still govern every write.
- **Internal only.** The brief goes to firm staff through `casework_brief`.
  Nothing here reaches a client, an opposing party or a court.
- **No computed dates, no legal judgment.** Compare an authored date to today; never
  derive a deadline, never assess merit, never advise.
- **No em dashes** and no codes. The attorney answers in words ("yes on 1, leave 2").

## What happens on a reply

The attorney's reply is handled by `matter-inbox-router` through `reply_verdicts`: a
"yes" writes an `approved` row carrying the step, and the router runs that step's
routine on this matter with its `params`. You do not wait for it.
