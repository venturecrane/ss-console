# Classification rules (task-list-keeper)

The code is `classify.py`; this page states the same rules in words. They are
closed lists: a word or a document kind not listed here is not a signal.

## Which tasks are reviewed

Open tasks in Smokeball whose due day is before today. Probe artifacts
(`[SMD-PROBE ...]` subjects) are excluded, as everywhere else. A task drops out
of the review while any of these holds (`casework_view.py`):

- a write on it is authorized and has not landed or failed yet;
- the Operator already handed it over once (`named`);
- a person held its line, approved "leave it open", or a `kept` row stands,
  within `case_manager.task_cleanup.keep_quiet_days` (pack default 30).

A line nobody answered is offered again at the next review.

## The classes, checked in this order

1. **at_stake** (first, so no evidence can close it):
   - the subject names one of: lien, liens, payoff, settlement, disburse,
     disbursement, trust, retainer, fee, fees, costs, invoice, payment, refund,
     check, Medi-Cal, Medicare, Medicaid, court, hearing, trial, motion,
     deposition, mediation, arbitration, sanctions, statute, SOL, summons,
     judgment, appeal, subpoena (whole words, any case);
   - the subject carries a priority marker (CRITICAL, URGENT, HIGH PRIORITY);
   - the task is tied to a court event on its matter;
   - the matter has a court date from today through the escalation window
     (`escalation.escalation_window_days`, pack default 14);
   - the matter's calendar could not be read this run.
2. **done**, with the evidence atom it rides on:
   - the subject names a topic and a document of that kind is on the matter,
     dated on or after the task (its created day, else its due day), and every
     distinctive word of the document's name also appears in the subject:
     - service (serve, served, service) and a "proof of service" document:
       `document:proof_of_service:<day>`;
     - verification (verify, verified, verification) and a "verification"
       document: `document:verification:<day>`;
     - records (record, records) and a "records" document:
       `document:records:<day>`; a document named as a request, an
       authorization, an invoice, a receipt or a no-records statement is
       paperwork about records, not the records, and never counts;
   - AND the document's own text was read (its text layer or a cached
     transcription; never a billed read) and names the matter's client: with
     one client, that surname is in the text; with several, the task names
     exactly one of them and the text names that one. An unread or unreadable
     document, a client list that did not fully resolve, or a read past the
     run's cap (`SMD_CASEWORK_READ_BUDGET`, default 40) is no evidence. A
     name alone never proves a task done: on 2026-09-22 a file review closed
     9 of 82 tasks against documents whose names matched and whose contents
     did not (the client's own dec page for a 3rd party one, a coverage letter
     for a ledger, an auto insurance card for a health card);
   - the records chase for this task is resolved in the escalation ledger:
     `ledger:records_chase_resolved`.
3. **stale**:
   - the matter's status is closed, archived or cancelled:
     `matter_status:<status>`;
   - the task repeats another open task on the same matter (same subject once
     the provenance stamp, case and punctuation are folded out); the copy with
     the latest due day stays, the others are stale: `subject:duplicate_on_matter`.
4. **open**: everything else.

## Whose task it is

A task is the Operator's own when its subject starts with the `[Operator]`
provenance stamp the connector adds to every Operator write, or its id is in
`case_manager.own_tasks.legacy_task_ids` (tasks from before the stamp). No
Smokeball field answers this: the task's creator is the consenting human for
every Operator write, and a task read never echoes its owner.

## Why the Operator cannot finish its own task (closed phrases)

Chosen by the subject, first match wins:

- money or a court word: "money or a court date rides on it, so a person should own it"
- verification: "the signed verification is not on file yet"
- service: "it needs a person to confirm what was served and when"
- records: "the records are not on file yet"
- discovery, interrogatories, admission, production: "it needs a person to confirm the discovery dates"
- otherwise: "it needs a person to decide the next step"
