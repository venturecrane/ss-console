---
name: mailbox-keeper
description: >-
  Keeps one managed mailbox with no model turn. It runs on Google Workspace
  only. An hourly pre_run asks the broker to move settled SMD Ops alert threads
  to Trash and file the rest of SMD Ops mail under the authored label, out of
  the inbox. On the first run of the day at or after the authored hour it emails
  the authored digest recipient one list of what needs the owner. With
  classify set, a model with no tools reads each remaining inbox thread inside
  the broker and returns one value; code then lists the decisions with a
  recommendation and a reply draft in the thread, files receipts under the
  authored label and archives notices. A failed read files and sends nothing.
  The skill never reads, replies to or drafts a message in a turn.
version: 0.2.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Ops, Mailbox, Gmail, Scheduled, ClassN, NoTurn, FailClosed]
  smd:
    vertical: neutral # SMD's own operations seat; nothing vertical about it
    weight: light # no turn at all: the pre_run makes one broker call
    action_class: read + internal_write # trashes settled alert threads (recoverable 30 days), labels and archives threads, writes reply drafts into the owner's Drafts, and emails the authored digest recipient
    content_ceiling: staff # the list goes to one authored SMD address, never outside SMD
    connectors: [] # the governed Workspace broker (google_auth), not a connector
---

# Mailbox Keeper

**This skill has no turn.** Its cron row runs `pre_run.py` hourly, which makes
one broker call, `mailbox_keeper_run`, and always prints `{"wakeAgent": false}`
as its last line. Every action is code in the broker
(`operator/workspace_broker/mailbox_keeper*.py`). When the seat sets
`classify: true`, a model READS mail inside the broker and returns a value per
thread; it has no tools and cannot act, and code floors bound what its value can
do (below).

If this skill is ever invoked in a turn: do nothing, send nothing, file
nothing, and say the keeper runs on its schedule only.

## What each run does

1. **Every hour: SMD Ops mail.** Reads the inbox's SMD Ops mail (from the alert
   sender, the last `window_days`), every page, then every thread in full. A
   read error, a page of the wrong shape, more than `max_pages` pages, or a full
   page with no next token stops the run: nothing is filed, and the heartbeat
   row says `mailbox_keeper_failed`.
   - A settled alert thread (every message from the alert sender with DKIM
     passing for its domain in the mailbox's own Authentication-Results, the
     newest a recovery, every alert in it recovered, the owner wrote nothing)
     goes to Trash, kept 30 days.
   - Every other verified SMD Ops thread gets the `alert_label` label and leaves
     the inbox. One the owner wrote in, or one that failed the sender check,
     stays in the inbox.
   - The SMD Ops kinds that are the owner's to act on (the triage's "N need you",
     NO REPLY, NEW KIND OF ASK, a sign-in about to expire or dead, a cost
     anomaly; `mailbox_keeper_ops.py`) are recorded BEFORE their thread leaves
     the inbox and named in the next list, once each.
   - The first run with a label also files older SMD Ops mail, once.
2. **The day's first run at or after `send_after_hour`: the list.** The same
   pass over the whole inbox, then:
   - With `classify: true` (and the seat's cost breaker allowing it): a model
     reads each inbox thread it has not read at its newest message (not SMD Ops
     mail, not the seat's own list, not a thread whose newest message is the
     owner's). One call per thread, no tools, thinking off, the thread as plain
     text with quoted history stripped, capped at 8,000 characters (newest
     kept). It returns `kind` (decision, receipt or notice), `what`, and for a
     decision `recommend`, `reply_needed` and `draft`; for a receipt `amount`.
   - Code floors: a thread from a person, from a `watch_senders` match, with a
     person still waiting on the owner, or that the owner wrote in, is always a
     decision and is never archived. A receipt's amount must appear verbatim in
     the email and parse; else it is a decision. A figure in `what` or
     `recommend` that the email does not contain sends the thread to "could not
     read"; one in the draft drops the draft. Anything off the schema is "could
     not read" and stays in the inbox.
   - A reply draft is made in the thread when the decision needs one, no draft
     is already in it (his own included), and the thread is unchanged: To is the
     Reply-To (else the From), From is the alias the mail was received at when
     the mailbox authors it as a send-as, threaded with In-Reply-To and
     References, with `draft_signoff` appended by code.
   - Before sending, the run waits a per-machine few seconds and asks AgentMail
     whether this seat already sent today's list. Sent: nothing more happens. A
     lookup that fails: no send (`send_check_failed`).
   - The list goes to `digest_recipient` (subject `Mailbox: N need you`, or
     `Mailbox: nothing needs you`): what needs him, each decision with its
     recommendation and whether a draft is in the thread; how many listed before
     are still open; what was filed and archived; what could not be read; what
     happened to SMD Ops mail. With `classify` off, or the meter refusing,
     the people waiting on a reply (older than `needs_you_after_hours`) are
     listed by rule instead.
   - After the send, each receipt and notice is re-read and filed only if its
     newest message is unchanged: a receipt gets the `receipts_label` label and a
     row in `expenses.jsonl`; a notice is archived. The ledgers are written last.

Everything else in the mailbox is untouched.

## Settings (scalars, ADR 0075)

| Setting                      | Type                                    | Default                        | Meaning                                                                                                                         |
| ---------------------------- | --------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `mailbox`                    | address                                 | required                       | The managed mailbox (`google_auth.managed_mailboxes[].address`).                                                                |
| `owner_addresses`            | comma-separated addresses               | the mailbox                    | Whose messages mean the owner engaged (a thread he wrote in is never trashed or archived; a person he answered is not waiting). |
| `digest_recipient`           | address                                 | none (no list is sent)         | The one recipient of the daily list. Must be on the seat's outbound surface.                                                    |
| `needs_you_after_hours`      | number, 1 to 720                        | 24                             | How old a person's message must be to be listed by rule (classify off).                                                         |
| `window_days`                | whole number, 1 to 60                   | 14                             | How far back the inbox is read.                                                                                                 |
| `require_alert_state_header` | true or false                           | false                          | Also require `X-SMD-Alert-State: resolved` on the recovery.                                                                     |
| `system_senders`             | comma-separated addresses or `@domains` | none                           | More senders that are never a person.                                                                                           |
| `watch_senders`              | comma-separated addresses or `@domains` | none                           | Automated senders that still need him (a state tax office): always a decision, never archived.                                  |
| `seat_inbox`                 | address                                 | the seat's own AgentMail inbox | The seat's own address, never a person; its mail is never read by the model.                                                    |
| `timezone`                   | IANA zone                               | America/Phoenix                | The day the once-a-day list is counted in.                                                                                      |
| `max_pages`                  | whole number, 1 to 200                  | 20                             | A read with more pages than this fails closed.                                                                                  |
| `alert_label`                | text                                    | none (SMD Ops mail stays)      | The Gmail label SMD Ops mail is filed under.                                                                                    |
| `send_after_hour`            | whole number, 0 to 23                   | 6                              | The first scheduled run at or after this seat-local hour sends the list.                                                        |
| `classify`                   | true or false                           | false                          | A model reads the remaining threads. Authoring it (either value) also gives the broker the seat's model key at boot.            |
| `classify_model`             | model id                                | none (required to classify)    | The model that reads (Claude Sonnet 5.5: the request turns thinking off with `between_tools`).                                  |
| `receipts_label`             | text                                    | none (required to classify)    | The Gmail label filed receipts get; the durable record he can see.                                                              |
| `cost_fence_cents`           | number, 0 to 1000                       | none (required to classify)    | The most one run may spend, in fractional cents. The seat's `safety.sticky_stop.cost_cap_daily_cents` bounds the day.           |
| `draft_signoff`              | text                                    | none                           | Appended by code to every reply draft.                                                                                          |

## State (gone means gone)

`/opt/data/mailbox-keeper/` (`SMD_MAILBOX_KEEPER_DIR`; layout and every file in
`operator/workspace_broker/mailbox_keeper_store.py`): `listed.json`,
`classified.json`, `expenses.jsonl`, `spend.json`, `trashed.jsonl`. The broker's
copy of the seat's model key is `model.json` beside its other credentials,
rewritten or removed on every boot.

## Rehearsal

`mailbox_keeper_run` with `"mode": "dry_run"` through the seat's broker socket
reads and decides and returns each thread id with the rule it fell to, at $0.
With `"classify": true` the model also reads every candidate thread (spending
inside the fences) and each result comes back: kind, what, recommend, draft,
amount, and the floor applied. Nothing is filed, drafted or sent.

## How to Run

The seat's cron row runs `pre_run.py` hourly with `wake_policy: pre_run_decides`.
