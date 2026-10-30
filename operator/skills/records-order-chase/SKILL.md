---
name: records-order-chase
description: >-
  Emails a weekly list of records orders not yet in. The orders are the medical-records orders
  the Operator placed with the firm's records vendor. On a schedule, reads every order placed through the Operator,
  asks the vendor where each request stands, and sends the one staff member the firm named each
  request not yet completed, with a follow-up note to the vendor ready for a person to send.
  Sends nothing to the vendor or to any provider. Rendered and sent by the pre-run, not composed;
  the woken turn sends nothing.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: [python3]
metadata:
  hermes:
    tags: [Law, PI, MedicalRecords, Records, Chase, Report, Internal, Cron, FailClosed]
  smd:
    vertical: law-firm
    addon: pi
    skill_type: scheduled report (no-model pre-run renders and dispatches one internal email)
    weight: light # one broker read, one connector-venv read per run; no model composition
    action_class: read # the list goes to one authored internal recipient through the out-of-turn dispatch
    content_ceiling: connective # matter numbers, client names, facilities, request numbers and statuses as the systems hold them
    cron: true
    connectors:
      - smokeball # PracticeManagement: the matter's number and its client contact's name (read only); the records vendor's order read rides the same connector
---

# Records Order Chase

The records chase the firm agreed to (letter 416, item 10): each week, the
Operator checks with the records vendor on the records ordered through the
Operator that have not come in, and prepares the follow-up for a person.

It is deliberately narrow. It never chases a provider, never decides which
records are missing or which facility to order from, never places or changes
an order, and never reads the records that arrived to judge whether they are
complete. It is not `medical-records-chaser` (the roster-and-document chase
that drafts to providers), which letter 416 does not include.

## What the pre-run does (all of it)

`pre_run.py` runs on the cron row and does the whole job in code (execution
class N, ADR 0050):

1. Reads every records order the Operator placed: the confirmed act rows
   (`ACT_COMMITTED` for the place-order tool), through the broker's read-only
   `records_orders_placed` verb. An order the firm placed on the vendor's own
   site has no such row and is never listed.
2. For each matter with an order, reads the matter's number and its client's
   name from Smokeball, and the vendor's requests for the matter
   (`orders_for_matter`), in the connector venv (`pull.py`). At most 60 matters
   a run, longest waiting first; the rest are counted in the email.
3. Selects every request the vendor does not show as completed (`report.py`).
   Only `Completed` (or `Complete`) reads as in. Any other status, including one
   never seen before, is listed with the vendor's own word for it.
4. Lists under "could not be checked" every matter the vendor would not answer
   for (an error, or a 404 on a matter we ordered on) and every order whose
   matter is not on record. A failed read never looks like a quiet week.
5. Renders the email (`references/output-format.md`): per request, the matter
   number and client as Smokeball holds them, the facility, the vendor's
   request number, the date ordered, how long ago, the vendor's status, and a
   follow-up note to the vendor a person can copy and send. Writes the
   provenance handoff (every matter number paired with its order dates) and
   the dispatch envelope, and wakes the turn so the seat sends it out of turn
   through the full gate.

**Quiet weeks.** No order placed through the Operator yet: no email, and the
run records `no_orders_placed`. Orders exist and every request is in: a short
"nothing outstanding" email, so a checked week reads differently from a run
that did not happen.

**Failures send nothing.** No authored recipient, a ledger that cannot be read,
the vendor not connected on the seat, a read that could not run: each records
its own status on a SUPPRESSED_WAKE row and sends no failure note to the firm.

The recipient is `settings.recipient` on this skill in customer.yaml. Unset
means nothing is sent.

## Arming it on a seat

Arming is a `customer.yaml` change (the skill entry, its recipient, the cron
row). As with `post-intake-daily-list`, the publish is refused for a seat whose
running image does not carry this skill yet, and the seat adopts the config at
its next boot after the image that carries it. Ashton & Price ships with the
cron row commented: the firm switches it on.

## What you do when woken

Nothing. The list was rendered and dispatched before your first tool call;
your Script Output carries only `dispatch_expected`, a status and counts.
Compose nothing, send nothing, read nothing, and end the turn with `[SILENT]`.

**Woken without `dispatch_expected`:** do nothing and end the turn with
`[SILENT]`.

## Rules that do not bend

- Internal only. One recipient, the one the firm named. Nothing goes to the
  vendor or a provider; a person sends each follow-up.
- Never says a request came in unless the vendor shows it as completed.
- Never reads a record that arrived.
- A failed run sends nothing, not a failure note and not a partial list.
- No matter number, name or facility in the Script Output, a heartbeat row, or
  a log line.
