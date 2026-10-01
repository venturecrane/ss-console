---
name: statute-watch
description: >-
  Emails a monthly list of unfiled cases nearing statute. On a schedule, lists every open or
  pending case whose authored statute of limitations date falls in the next three months with no
  Filed date and no Case number, soonest first, to the one staff member the firm named. Rendered
  and sent by the pre-run, not composed; the woken turn sends nothing. Not the on-request
  deadline view (deadline-and-sol-tracker) and not the missed-deadline alarm
  (deadline-miss-escalator). Never computes a date.
version: 0.2.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, PI, Deadlines, Statute, Report, Internal, Cron, FailClosed]
  smd:
    vertical: law-firm
    skill_type: scheduled report (no-model pre-run renders and dispatches one internal email)
    weight: light # reads matter layouts; no model composition
    action_class: read # the report goes to one authored internal recipient through the out-of-turn dispatch
    content_ceiling: connective # dates and counts already in the firm's records; never advice
    cron: true
    connectors:
      - smokeball # PracticeManagement - matters, matter layouts, contacts, staff, document file names (read only)
---

# Statute Watch

A monthly email to one person at the firm: every open or pending case whose
Statute of Limitation date, as entered in Smokeball, falls from today through
the next three months, and that has no Filed date and no Case number. It is the
"what is coming up that nobody has filed yet" list, delivered without anyone
having to ask for it.

It is not `deadline-and-sol-tracker` (the on-request view of every authored
date, which reads tasks and calendars and applies no "not filed" test) and not
`deadline-miss-escalator` (the daily alarm for a date that is near or missed).
It reads one field set on the matter itself and reports the unfiled ones.

## What the pre-run does (all of it)

`pre_run.py` runs on the cron row and does the whole job in code (execution
class N, ADR 0050):

1. Reads the firm's Open and Pending matters (leads excluded), and for each
   one the matter's case details: the Statute of Limitation date, the Case
   number and the Filed date.
2. Selects the cases whose statute date is today (the seat's own day) through
   the next 91 days with neither a Filed date nor a Case number, soonest first.
3. For the listed cases: the client's name from the matter title (`clients.py`;
   the contact's last name only when the title does not carry it), the
   responsible attorney's name, and how many of the case's documents carry a
   court-paper name (complaint, summons, proof of service, and the like).
4. Compares the list with last month's (`changes.py`, the state file
   `.smd/statute-watch/last-run.json`): new cases, and for every case that
   left the list, a fresh read of the matter and its case details, worded
   only as the record now shows it (filed, closed, statute date passed with
   nothing filed, statute date changed or removed, could not be checked).
5. Builds the spreadsheet in the connector venv (`workbook.py`): every listed
   case, the changes, and what is counted.
6. Renders the email per `references/output-format.md` (`render.py`), writes
   the provenance handoff and the dispatch envelope with the spreadsheet
   attached, writes the state file, and wakes the turn so the seat sends the
   envelope out of turn through the full gate. If the spreadsheet is refused
   the seat sends the body saying it could not be attached; if the body is
   refused, the counts-only skeleton.

A case whose details could not be read is counted, never dropped: the email
says how many open cases could not be checked. If the matter list itself
cannot be read, nothing is sent to anyone and the run records why.

The recipient is `settings.recipient` on this skill in customer.yaml. Unset
means no report is sent.

## What you do when woken

Nothing. The report was rendered and dispatched for you before your first
tool call; your Script Output carries only `dispatch_expected`, a status and
counts. Compose nothing, send nothing, read nothing, and end the turn. Do not
write a report of your own if a dispatch note says delivery failed: the run is
recorded and the next run is the retry.

**Woken without `dispatch_expected`** (no dispatch was prepared, whatever the
status says): do nothing and end the turn. Never compose a report, a failure
note, or any message to cover the gap; the run is already recorded.

## Manual firing

An interactive request for this report is not this skill's job: the report
exists only as the pre-run's rendered dispatch. A person who wants it now
asks for a one-off run of the cron job.

## Rules that do not bend

- Never computes a date. The only arithmetic is days between today and a date
  a person entered.
- Internal only. One recipient, the one the firm named.
- A failed run sends nothing, not a failure note and not a partial list.
- No case list in the Script Output, a heartbeat row, or a log line.
