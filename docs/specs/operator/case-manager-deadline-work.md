# Operator deadline work: the case manager, not the alarm

Status: **Spec, Captain-directed 2026-09-25.** Product feature; names no client.
Supersedes, for firm-staff messages, the ACK-code reply contract in
`operator/skills/deadline-miss-escalator/SKILL.md` ("Fire once, acknowledge per
item") and `references/output-format.md` ("The confirmation reply").

## 1. The objective

The Operator competes with a hire, not with software (ADR 0037, tenet 1). For
deadlines, the hire it competes with is a great paralegal or case manager. The
test for every message the Operator sends a firm's attorney or paralegal about a
date is one question: **would a great case manager have sent this?**

A great case manager does not tell an attorney that a date exists. Smokeball
already shows the date. They find out whether the file is ready for it, do the
parts they can, and bring the attorney only the decision that needs the
attorney, in plain words, answerable in a sentence.

## 2. The client problem

A firm does not lack awareness of its deadlines. It lacks someone with the time
to prepare for them, chase what is missing, and keep the task list honest. The
symptom a firm feels: a task list nobody trusts (hundreds of stale "overdue"
tasks is normal on day one), dates that arrive with the file not ready, and
tools that add reading instead of removing work.

## 3. Are we solving it today? No.

Evidence from the pilot seat's own inbox (Gmail, `from:pilot-smokeball@agentmail.to`,
2026-08-27 to 2026-09-25, 41 messages):

| What a great case manager does        | What the Operator sends today                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Checks the file is ready for the date | "matter 2026-PI-105, court-date 2026-10-02 (due in 7 days) [ACK-XFGVBG] a court date the firm authored" (2026-09-25). No status, no prep, no question.                                                                                                                                                                                                                                          |
| Keeps the task list honest            | The same tasks re-sent every 3 days, "overdue by 57 ... 78 days" (ACK-K96VZS on 9/3, 9/6, 9/9, 9/12, 9/15, 9/18, 9/21, 9/24). These are tasks the Operator itself created in the July rehearsals and never closed (`operator/grading/runs/l2-pilot-smokeball/2026-07-05-disc-1-run-01.md:24`; `2026-07-06-l2-round-2.md:114-124`, `:164-176`). The Operator is chasing its own unfinished work. |
| Handles the routine quietly           | 13 codes in one message ("Admin confirms", 2026-09-24), which is the overflow band below the top five, not a routine class (`digest_items.py:33`, `render.py:195-217`).                                                                                                                                                                                                                         |
| Answers are conversation              | "Reply with the ACK code(s) above ... Reply ESCALATION_ACKNOWLEDGED to ack every item quoted in this message" (`render.py:302-308`).                                                                                                                                                                                                                                                            |

Industry check (web research, 2026-09-25): no alerting or practice-management
product asks a person to type a code back. PagerDuty does not accept email acks
at all (support.pagerduty.com/docs/notifications); Clio and Smokeball reminders
inform and deep-link, and the work is closed in the system of record
(help.clio.com 9206714957211; support.smokeball.com 6083101771927). Google SRE:
"If a page merely merits a robotic response, it shouldn't be a page."

## 4. The product: three jobs a case manager owns

The posture, Captain-set: **the Operator is capable of all of it; the firm chooses
all or part.** The pitch is "we can do all of these; would you like us to?"
Each job and each step inside it is a routine the firm turns on at the level it
wants, in the existing tier language: "Surfaces it", "Prepares it for you",
"Handles it" (`src/lib/portal/operator/tier-language.ts:16-19`).

### Job 1. Keep the task list trustworthy

- **Sort** every overdue task into: really open; already done (the document is on
  file, the event happened, the record shows it); stale (the matter moved on, or a
  duplicate); money or court at stake (never changed without a person).
- **Propose, then act.** Each attorney gets their own matters, grouped, with a
  suggested call per task. Nothing changes until approved. Approval is a reply in
  words ("yes to all", "leave the Rivera ones").
- **Keep it clean** as a standing job, so a task never sits 78 days overdue again.
- **The Operator's own tasks come first.** A task the Operator created and cannot
  finish is the Operator's problem to close or hand over once, in one sentence,
  never a recurring alert. This is the pilot's entire "overdue" band today.

### Job 2. Prepare for what is coming

A court date or deadline entering its window starts the prep work, not an email.
The Operator pulls the file status from what its prep routines already produce
(binder index and gaps from `trial-binder-assembler`, records currency from
`medical-records-chaser`, discovery status from `discovery-response-tracker`,
motion picture from `motion-calendar-tracker`), does the steps the firm has turned
on, and then writes one message: what is done, what is not, and the one or two
decisions left.

Records currency covers both kinds of provider. One still outstanding is chased on
the chaser's cadence. One already received, whose newest record on file is older
than the firm's authored `records_stale_days`, is offered an updated-records request
("Dr. Reyes's newest records are dated [date]. Want me to request an update before
trial?"); on a yes, `medical-records-chaser` prepares that request through its own
request path, as a draft at "Prepares it for you" or sent under the firm's send
ceiling at "Handles it". No threshold authored, no such offer.

### Job 3. Handle the routine quietly

Confirmations and housekeeping the Operator can do are done and mentioned in one
line. They never arrive as a request.

## 5. The message a firm receives

Rules, all of them tested against "would a great case manager have sent this?":

1. **Addressed to the person who owns the work.** The matter's responsible
   attorney, with the assigned paralegal copied (existing `matter_staff` routing,
   `references/case-alert-routing.md`). The firm's office manager may be an early
   recipient while the firm shapes it; that is configuration, not the design.
2. **Subject says the matter and the ask**, in words: "Okafor v. Grand Valley:
   status conference next Friday (Oct 2), two questions for you".
3. **Every item says what the task is.** Not "task-deadline 2026-07-08". The
   send gate's traceability rule stays (see §7); text the Operator read from the
   record is traceable and may be shown.
4. **Done, then needs you.** What the Operator already did, then numbered
   decisions. No decision, no message.
5. **One way to answer: reply in words.** "Yes on 1, leave 2." No codes, no magic
   words, nothing to learn.
6. **The Operator confirms in words** what it will now do, and names anything it
   did not understand.
7. **Nothing sent when nothing needs a person.** No "0 need you" message.

### The three pilot examples, before and after

**A. A court date (2026-09-25, matter 2026-PI-105).**
Before: `matter 2026-PI-105, court-date 2026-10-02 (due in 7 days) [ACK-XFGVBG]`.
After (bracketed values are what the prep read would supply):

> Okafor v. Grand Valley: status conference next Friday (Oct 2), two questions for you
>
> The Final Status Conference is Oct 2 at 8:30 in Dept 47; trial starts Oct 13.
> Done: the draft trial binder is in the matter [link]. [The exhibit list matches the documents on file.]
> Needs you:
>
> 1. The witness list on file is the June 18 draft. Is Priya Natarajan (biomechanics) still testifying? If yes, I'll finalize it for [paralegal] to serve.
> 2. Dr. Reyes's newest records are dated [date]. Want me to request an update before trial?
>
> Reply here and I'll take it from there.

Source for the dates and witness list: the pilot seed trial order and draft
witness list (`operator/customers/pilot-smokeball/seed/seed_data.py:516-541`).

**B. The Operator's own overdue tasks (2026-09-24, matter 2026-PI-101).**
Before: three lines of `task-deadline 2026-07-08 (overdue by 78 days) [ACK-...]`,
re-sent every 3 days. The three are the July DISC-1/DISC-2 rehearsal tasks on
2026-PI-101 (`1f5546c6`, `1ec31561`, `95de85b8`): their ack tokens, recomputed
with `escalation_ledger.token_for` from the 2026-08-24 live pull
(`operator/skills/deadline-miss-escalator/tests/fixtures/live-pull-2026-08-24.json`),
are ACK-DR8B8W, ACK-K96VZS and ACK-45ABZC. The pilot authors them as
`case_manager.own_tasks.legacy_task_ids`, because they predate the `[Operator]`
subject stamp and a task read carries no creator.
After: the Operator checks each task it created against the record. Done ones it
closes. For the rest, one message, once (illustrative: which tasks are done
depends on what the record shows at run time; the task subjects live only on the
pilot tenant):

> Three review tasks I opened in July on 2026-PI-101 are still open. Two are done in the file (proof of service is on file for both), so I've closed them. One I can't finish: [the task, as the record reads] needs someone to say [the open question]. Want me to leave it with [paralegal]?

**C. The overflow list (2026-09-24, 13 codes).**
Before: "Admin confirms (12 across 4 matters) ... [ACK-G9HJRE] [ACK-DGS5FR] ...".
After: gone as a category. Each item is either handled (one line in the daily
note), part of a Job 1 cleanup proposal, or a real decision in the numbered list.

## 6. Replies without codes

The per-item identity the codes carry stays exactly as it is internally: the
2026-07-31 incident proved that keying acks on model-composed text silences
nothing (86 fired events, 83 distinct keys;
`docs/runbooks/operator/incidents/2026-07-31-escalation-ledger-item-identity.md`).
What changes is who carries it. The person no longer does. The message does.

- **At send**, the seat records the message's numbered items: sent message id,
  and for each number the ledger `item_key`/token, the same ones the render
  already derives (`escalation_ledger.py:207-283`; overlay
  `plugins/hermes-smd-escalation/__init__.py:506-578`).
- **At reply**, the reply is matched to the message it answers (the reply's
  `In-Reply-To`/`References`, or the channel's conversation id), and the numbers
  the person wrote resolve deterministically to those tokens. The model reads the
  intent ("yes", "leave it", "done", "not this one", "all of them"); code, not
  the model, maps numbers to items.
- **Who may answer** is unchanged: a rostered internal sender, verified, with the
  acker named from the firm's authored users (ss#2152 path, overlay
  `__init__.py:51-99, 601-616`). Auto-replies are ignored (RFC 3834
  `Auto-Submitted`).
- **What an answer does** is unchanged: an acknowledgement quiets, only
  completion in Smokeball closes (`escalation_ledger.py:444-510`).
- **Ambiguity is asked, not guessed**: "I read that as yes to 1 and 3. Item 2 is
  still open. Did you mean it too?"
- Existing codes in flight keep working during the change (a reply quoting an
  `ACK-` code still resolves), so no message already sent becomes unanswerable.

## 7. Constraints carried forward (do not relax)

From the mechanism audit (2026-09-25) and the incidents it cites:

- An ack must reference an earlier witnessed raise; writes go through the broker
  verb only; timestamps are stamped server-side (`escalation_ledger.py:655-761`).
- Item identity comes from Smokeball ids, never model text; the item shown equals
  the row written (the derive/append handle pairing, overlay `__init__.py:448-578`).
- The send gate refuses untraceable identifiers, dates, dollar figures and
  captions (`digest_items.py:1-15`). Richer items must pass the gate by being
  traced to a read, not by weakening the gate.
- Only a rostered internal sender may answer; "unattributed" is an allowed state.
- Nothing in this spec sends to a firm's client, an opposing party or a court.
- Firm-configured levels bind: a step the firm set to "Surfaces it" is never done;
  "Prepares it for you" produces a draft for review.

## 8. What is delivered

One delivery, not a sequence. Plain-word replies to the deadline digest shipped
first (ss-console be2e1967, overlay 48726abb); everything below builds on them and
lands together. Each line names the act and the observation on the pilot seat that
proves it (the reachability rule in CLAUDE.md).

- **The firm's choices, written down.** A `case_manager:` block in customer.yaml:
  `own_tasks`, `task_cleanup`, `date_prep` and `quiet`, each at a level in the tier
  language, validated strictly (`src/lib/operator/customer-yaml/sections-case-manager.ts`,
  schema in `customer-yaml-schema.md`). Absent block: every job off, and the
  escalator renders byte-identical to today. The pilot authors it; a client seat
  authors it only when its agreement names the routines.
- **The Operator's own tasks (Job 1a)**, in `task-list-keeper`. Tasks it created
  (the `[Operator]` stamp, or the authored `legacy_task_ids`) are checked against the
  record, closed on the record's evidence at `handles`, or named once to the
  paralegal. Proved when the July tasks on 2026-PI-101 are completed with
  `closed_by_record` rows, one handover line was sent, a second run sends nothing,
  and the digest no longer says "overdue by 78".
- **A trustworthy task list (Job 1)**, in `task-list-keeper`: a weekly proposal per
  attorney, grouped by matter, numbered, with a suggested call per line; approval
  by reply ("yes except 2"); only approved lines are written, by a stored-payload
  replay the model cannot redirect. Proved when a reply "yes except 2" changes
  exactly the approved task ids in Smokeball and no money or court task was proposed.
- **Prep for dates (Job 2)**, in `date-prep-brief`: a date entering its window
  starts a file-status read, the steps the firm set to `handles` run, and one brief
  goes to the matter's responsible attorney with the paralegal copied: done, then
  at most two numbered decisions from a closed catalog. A "yes" runs that step's
  routine through the router. Proved when the Okafor status conference produces a
  brief before Oct 2 and "yes on 2" leaves an `approved` row and the routine's draft.
- **The routine handled quietly (Job 3)**: work closed on the record, and a
  date-prep step the Operator ran itself at "Handles it", becomes one "Done since
  last time" line in the next message that person gets (the task review, the
  date-prep brief, or the daily deadline digest), and a close earns a memo on the
  matter; never a message of its own. A step is recorded only on the memo its
  routine filed in that session, checked by the broker against its audit log.
  Proved when that line appears and the digest carries no "Also open" band.
- **One ledger of what was proposed, decided and done**, the casework ledger,
  written only through the broker, which refuses a close on money or court work,
  an approval with no raise behind it, and a raise nobody witnessed reach a person.
- **The escalator stops repeating what the other jobs own** when `case_manager` is
  authored: no Operator-own tasks, no task under a pending proposal or "leave it",
  no court date already briefed, and one line in place of the "Also open" band. It
  keeps its backstop: a court date whose decisions go unanswered still fires at
  `notify_days`.

Scheduled routines using another routine's output was the open premise here; it is
met two ways. Code reads across the prep routines' records and hands the turn
facts (`date-prep-brief/pre_run.py`), and a step a person approves runs through the
router's existing skill path, reading the routine's own procedure.

## 9. Open questions for the Captain

- **A client seat.** No client seat authors `case_manager` in this delivery. Turning
  the jobs on for Ashton & Price means new routines in its Schedule A-1, an
  amendment to what the firm signed, so it is the Captain's call and not a config
  edit. Until then its `cron: []` and its escalator are untouched.
- **The first cleanup.** Whether a firm's first task-list cleanup is part of
  onboarding or a separately quoted engagement.
