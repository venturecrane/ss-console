---
title: The Case File Manager
section: product
order: 6
owner: captain
review_every: 90
last_reviewed: "2026-10-28"
summary: What the law-firm Case File Manager does and never does, how a firm asks for a review, how its weekday schedule is switched on, what the firm configures, what pages SMD, and what it costs to run.
sources:
  - label: The case-file-manager skill
    href: operator/skills/case-file-manager/SKILL.md
  - label: The schedule's cron step
    href: operator/skills/case-file-manager/pre_run.py
  - label: The job's stages
    href: operator/runners/medchron/medchron/casefile/run.py
  - label: The re-check (only what changed)
    href: operator/runners/medchron/medchron/casefile/recheck.py
  - label: The numbered changes
    href: operator/runners/medchron/medchron/casefile/propose.py
  - label: Applying approved changes
    href: operator/runners/medchron/medchron/casefile/apply.py
  - label: Low limits and new files
    href: operator/runners/medchron/medchron/casefile/flags.py
  - label: The monthly billing count
    href: operator/runners/medchron/medchron/casefile/billing.py
  - label: The File Status
    href: operator/runners/medchron/medchron/casefile/book.py
  - label: The ranked list workbook
    href: operator/runners/medchron/medchron/casefile/workbook.py
  - label: The firm inputs (casefile-firm.yaml schema)
    href: operator/runners/medchron/medchron/casefile/firm.py
  - label: The schedule (broker)
    href: operator/workspace_broker/casefile_schedule.py
  - label: The scheduled submit (broker)
    href: operator/workspace_broker/casefile_scheduled.py
  - label: Staging the firm inputs
    href: operator/bin/lib/stage-casefile-inputs.sh
  - label: The paused-schedule digest line
    href: operator/bin/lib/work_health_casefile.py
  - label: Lane-proof runbook
    href: docs/runbooks/operator/lane-proof.md
---

The Case File Manager is a law-firm job (personal injury add-on) that does what a case assistant does to keep files current. It reads each file in a caseload, files a current **File Status** in each matter, ranks the caseload by what needs attention first, and numbers the changes it would make. Nothing in a file changes until a named person at the firm approves a change by its number. It runs when a firm asks for one, and, once the firm says yes, on a standing weekday schedule.

## What it does

A review covers a **caseload**: every Open and Pending matter one person assists on, found by name on the firm's staff list in Smokeball, or a list of matter numbers. For each file it:

- **Reads what is in it.** The documents, tasks, calendar, memos and the Medicals tab. A file with nothing new since its last review is already current and is not read again (see the cost section below).
- **Files a File Status.** A PDF in the matter's File Status folder: where the case stands, insurance and limits, what is still out, which tasks the file already proves done, and the questions only the firm can answer. Each figure is checked in code against the document it came from.
- **Ranks the caseload.** Each file goes into one of four tiers: Pull today, This week, This month, or On track. Code decides the tier from the record and the firm's thresholds: statute, court and government-claim dates first, then settled files with items open, stale negotiations, overdue tasks, low limits and new files. Every reason names its date and source. The ranked list is a workbook filed in the firm's own library matter. Its sheets are the priorities, Proposed changes, Needs you, Records to order, Low limits, New files, Scanned bills not read, and the files not reviewed this run.
- **Numbers the changes it proposes.** There are three kinds, and each is proposed only after every check already passed:
  - **Task closures.** A task the file proves done. The reader names the proof, and a second reader tries to refute the closure; a closure is proposed only when both agree.
  - **Medicals rows.** A new row on the Medicals tab from a bill whose text was read, with the charge found in that bill and both service dates. Bills that are scans the job did not read are listed, never entered.
  - **Calendar entries.** An upcoming specialty appointment the file names that is not already on the matter's calendar that day. A time is used only in a time zone the document states; otherwise the entry is all-day on that date.
- **Flags low limits.** The reader copies each policy's limits as printed; code decides "low" from the firm's thresholds (a per-person limit at or under the firm's figure, or an other-side carrier the firm names). Limits that do not read as limits are printed as written and never flagged.
- **Flags new files.** A file opened within the firm's window that has none of the firm's opening tasks is flagged with the ones it lacks. Nothing is created.

The person who asked gets one email that carries counts only (how many files, how many read, how many already current, how many proposed changes) and says where the list is filed. A matter number, a name or a date from a document never goes in an email.

## What it never does

- **It changes nothing without approval.** A Named Administrator approves by replying in the list's own thread with the numbers ("yes to 1, 3 and 4", or "yes to all"). Before each approved change is made, the record is read again, and a change whose task or Medicals tab moved since it was proposed is refused, never guessed. Each change made is read back from Smokeball. A scheduled list more than 10 days old cannot be approved; the reply is told to use today's list.
- **Tasks about money, a court date or a statute stay with the firm.** The firm's authored words (`at_stake_terms`) and statute-reminder patterns keep those tasks open whatever the file shows, and the File Status says so in those words.
- **It contacts no one outside the firm.** No vendor, provider or carrier is chased. Records to order are listed for a person; nothing is sent.
- **It reads nothing in the conversation.** The email turn only queues the job; the reading runs as a job on the seat, under checks a conversation does not have.

## How a firm asks for one

A Named Administrator (`scope.admins` in the seat's `customer.yaml`) emails the Operator, for example "Please review Pat Example's files and tell me which need attention first," or names matter numbers. The Operator replies once that the review is queued, with no timing. A request from anyone else is declined politely, and the broker checks the sender again on submit. A name that matches no one, or more than one person, on the staff list holds the job, and the firm is asked whose files it meant.

On a seat that has never run this kind of job, the job waits and team@ gets `NEW KIND OF ASK` until SMD runs the proof and accepts it ([lane-proof runbook](https://github.com/venturecrane/ss-console/blob/main/docs/runbooks/operator/lane-proof.md)). There are three proofs, one per kind of job: `case-file-manager.full` (a review), `case-file-manager.recheck` (a re-check of what changed) and `case-file-manager.apply` (making approved changes).

## Switching on the weekday schedule

The schedule is the firm's yes in writing, then a reviewed change to the seat's `customer.yaml` ([Changing a Client's Operator](/admin/playbook/changing-a-clients-operator)). In the `case-file-manager` skill entry:

- `initiation.scheduled: true`;
- `settings.scheduled_caseloads`: the people whose caseloads are reviewed, as Smokeball shows their names, separated by `;`;
- `settings.scheduled_recipients`: the Named Administrator who receives each list;
- the cron row, which is the switch itself: `{ skill: case-file-manager, schedule: '19 6-13 * * 1-5', pre_run: pre_run.py, wake_policy: pre_run_decides }`.

The cron fires hourly on weekday mornings and wakes no model; the broker decides everything in the firm's time zone. Each business day (a weekday not on the firm's `holidays` list) queues one **re-check** per caseload; the first business day of the month queues a **full read** of every file instead. A caseload whose last job is still running waits for the next hourly fire. Each list arrives as a new email to the recipient, so each list is its own thread and approval numbers bind to that list. Re-check jobs are held until the seat's `case-file-manager.recheck` proof is accepted.

## The firm inputs

Everything specific to the firm lives in `casefile-firm.yaml` and its `prompts/` (the reader contract, the reader's and the second reader's instructions), in the private engagements repository under the client's folder. The key set is closed: a misspelled key is an error, never a default. The keys an administrator is most likely to change:

| Key | What it sets |
|---|---|
| `per_job_cap_usd`, `monthly_budget_usd` | The most one job may spend, and the month's total; once the month's budget is spent, new jobs are refused |
| `at_stake_terms`, `statute_reminder_patterns` | The words that keep a task with the firm |
| `file_status_folder`, `caseload_statuses` | Where the File Status is filed; which statuses count as a caseload (Open and Pending) |
| `low_limits` | `per_person_usd`, `bills_trigger_usd` and `carrier_patterns`; zero and empty switch the flag off |
| `new_matter` | `opened_within_days` and the `opening_tasks` a new file should carry |
| `timezone`, `holidays`, `scheduled_cutoff_hour` | The firm's zone, its days off, and the local hour (default 14) after which a caseload with no job that day is a missed day |

The inputs are staged to a seat by the casefile staging step of provisioning (`operator/bin/lib/stage-casefile-inputs.sh`, run by `operator/bin/reprovision.sh <slug>`). It refuses any file that is not merged on engagements main, or that does not validate against this checkout's runner. **Release the image first, then stage.** A seat's older image refuses keys it does not know, so a new key reaches a seat only after `fleet release` has put the image that knows it there ([Deployment & Release](/admin/playbook/deployment-release)). A reprovision ends a running job, so stage when the lane is idle.

## What pages SMD

Each of these reaches team@ as a `SHORTFALL` once, not every day; while it lasts, the hourly work digest repeats one line a day ([Work Health](/admin/playbook/work-health)):

| What happened | What it means | Who acts |
|---|---|---|
| A missed weekday | At the firm's cutoff hour, a caseload had no job that day | An agent looks at the caseload's last job |
| The budget is spent | A scheduled run was refused, most often because the month's budget is used; the same page covers an unauthored filing target or setting | You: decide with the Captain whether the budget changes. Agent: a setting that is missing |
| Smokeball under 7 days | The firm's Smokeball connection has under seven days left, or its age is unknown; nothing is queued, and approved changes wait too | You get the firm to re-consent Smokeball |
| Reply unproven | The requester's reply could not be shown sent within 20 minutes of the job finishing | An agent finds out whether the person got their answer |

A held job and a failed job page as they do for every lane ([The Administrator's Week](/admin/playbook/administrators-week)).

## The billing count

The month's first scheduled job counts the caseload's Open and Pending files from the matter list before it reads anything, so the count stands even if the read that follows fails. Smokeball's own status is used, and the matter ids are kept on the seat beside the counts so the number on the invoice can be traced to the files it counted. The emailed list's first line carries it, in the form "Count on the 1st: N Open, M Pending." Only the counts leave the seat.

## What it costs to run

A re-check pays only for what changed. A file whose documents did not change since its last review is re-ranked in code from its last record, with no model call, and its File Status says "Current (re-ranked; no document changed since ...)". A file with new documents is read with its last record and only the new or changed documents, and the reader answers with the changes to that record rather than rewriting it. A file is read whole when it has no record yet, when its record is older than `full_reread_days` (30), or when a bulk upload changed more documents than `bulk_recheck_docs` (200). The scheduled monthly full read reads every file whole. The Operator's own writes (its File Status, its stamped tasks, calendar entries and memos) never count as a change. Spend per job and per month is held by the two caps above.
