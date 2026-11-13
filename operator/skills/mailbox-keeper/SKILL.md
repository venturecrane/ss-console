---
name: mailbox-keeper
description: >-
  Keeps one managed mailbox in code, with no model turn. It
  runs on Google Workspace only. A scheduled pre_run asks the broker to read the inbox to its last page, move
  settled SMD Ops alert threads (an alert and its recovery, all from the alert
  sender with DKIM passing) to Trash, and on the day's first run email the
  authored digest recipient one list of the people waiting on a reply, the SMD
  Ops alerts that have not cleared, and the count trashed. A later run the same
  day only trashes. A failed read trashes and sends nothing. The skill never
  reads, replies to or drafts a message in a turn.
version: 0.1.0
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
    action_class: read + internal_write # trashes settled alert threads in the managed mailbox (recoverable 30 days) and emails the authored digest recipient
    content_ceiling: staff # the list goes to one authored SMD address, never outside SMD
    connectors: [] # the governed Workspace broker (google_auth), not a connector
---

# Mailbox Keeper

**This skill has no turn.** Its cron rows run `pre_run.py`, which makes one
broker call, `mailbox_keeper_run`, and always prints `{"wakeAgent": false}` as
its last line. Every decision is code in the broker
(`operator/workspace_broker/mailbox_keeper.py`); no model reads a message.

If this skill is ever invoked in a turn: do nothing, send nothing, trash
nothing, and say the keeper runs on its schedule only.

## What each run does

1. **Reads the inbox as it is now.** `in:inbox` over the last `window_days`,
   every page, then every thread in full. A read error, a page of the wrong
   shape, more than `max_pages` pages, or a full page with no next token stops
   the run: nothing is trashed, nothing is sent, and the heartbeat row says
   `mailbox_keeper_failed`.
2. **Trashes settled SMD Ops alert threads** (Rule 1). Only when every message
   is from the alert sender with DKIM passing for its domain in the mailbox's
   own Authentication-Results, the newest message replies to an alert and opens
   with the recovery paragraph, every alert in the thread has its recovery, and
   the owner wrote nothing in it. The whole thread goes to Trash in one call
   (kept 30 days).
3. **Lists, once each** (Rule 1b and Rule 2): SMD Ops alerts that have not
   cleared, the two kinds that never clear on their own (a scheduled message
   that did not go out; a reply that did not go out or a limit that stopped the
   work), which are never trashed, and the people waiting on a reply: the newest
   message in an inbox thread from a person (not a list, not a no-reply or
   system sender, not the seat itself, not the owner) with no reply from the
   owner after it, older than `needs_you_after_hours`.
4. **Sends one email a day** to `digest_recipient`, rendered by code, sections
   in this order: waiting on a reply, SMD Ops alerts not cleared, notices that
   do not clear, then the count trashed. An empty day sends one line:
   "Nothing waiting on you. Trashed N settled alerts." A later run the same
   seat-local day only trashes.

Everything else in the mailbox is untouched.

## Settings (scalars, ADR 0075)

| Setting                      | Type                                    | Default                        | Meaning                                                                                                                  |
| ---------------------------- | --------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------ |
| `mailbox`                    | address                                 | required                       | The managed mailbox (`google_auth.managed_mailboxes[].address`).                                                         |
| `owner_addresses`            | comma-separated addresses               | the mailbox                    | Whose messages mean the owner engaged (a thread he wrote in is never trashed; a person he answered is not waiting).      |
| `digest_recipient`           | address                                 | none (no list is sent)         | The one recipient of the daily list. Must be on the seat's outbound surface.                                             |
| `needs_you_after_hours`      | number, 1 to 720                        | 24                             | How old a person's message must be to be listed.                                                                         |
| `window_days`                | whole number, 1 to 60                   | 14                             | How far back the inbox is read.                                                                                          |
| `require_alert_state_header` | true or false                           | false                          | Also require `X-SMD-Alert-State: resolved` on the recovery. Turn on once the fleet-alerts worker that stamps it is live. |
| `system_senders`             | comma-separated addresses or `@domains` | none                           | More senders that are never a person.                                                                                    |
| `seat_inbox`                 | address                                 | the seat's own AgentMail inbox | The seat's own address, never a person.                                                                                  |
| `timezone`                   | IANA zone                               | America/Phoenix                | The day the once-a-day list is counted in.                                                                               |
| `max_pages`                  | whole number, 1 to 200                  | 20                             | A read with more pages than this fails closed.                                                                           |

## State (gone means gone)

`/opt/data/mailbox-keeper/` (`SMD_MAILBOX_KEEPER_DIR`): `listed.json`, the ids
already listed and the date of the last list, and `trashed.jsonl`, one line per
trashed thread (time, thread id), so a trash can be found in Gmail's Trash and
undone. Ids only, no message content.

## Rehearsal at $0

`mailbox_keeper_run` with `"mode": "dry_run"`, as root through the seat's
broker socket: it reads and decides and returns each thread id with the rule it
fell to and the counts. Nothing is trashed, sent or recorded.

## How to Run

The seat's cron rows run `pre_run.py` with `wake_policy: pre_run_decides`.
