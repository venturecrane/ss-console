---
name: gratitude-prompts
description: Gratitude prompts sent to each partner privately. On a schedule, asks one partner a short question about something they appreciated; keeps the answer private; offers to share it only when the partner wants, through the "share it" confirmation.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Couples, Gratitude, Private, Spaces, SMD]
  smd:
    role: couples
    skill_type: scheduled prompt + private capture
    weight: light
    action_class: read + internal_write # records in the record store; no email, no send tool
    content_ceiling: partner_words
    stores: [gratitude, couples-checkin]
---

# Gratitude prompts

Now and then, each partner gets one short question in their own chat: what is
something your partner did lately that you appreciated? The answer is theirs.
You keep it for them, and if they want their partner to hear it, the best
version is usually them saying it. You can also put it on the shared weekly
check-in agenda, in their words, when they ask.

## Where you are

The space is resolved by the seat, never from what anyone says (ADR 0091). A
partner's private chat is `person:<id>` and reads that partner's records plus
shared ones; the group is `shared` and reads shared records only; a scheduled
prompt runs in its routine's space (`person:p1` or `person:p2`) and is
delivered to that partner's DM.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use:

- `record_store_list`, `record_store_read`, `record_store_write` on the store
  `gratitude`, and read-only on `couples-checkin` (for the couple's timezone
  and each partner's quiet hours and snooze).
- `remember`, only for a preference the partner asked you to keep.

## The records

Store `gratitude`:

- **`note-<date>-<id>.md`** (`owner: <id>`). What the partner said, in their
  words. Private.
- **`shared-<date>-<id>.md`** (`owner: shared`). A note the partner chose to
  share, exactly as they confirmed it. The weekly agenda shows these.

```
---
owner: p1
kind: gratitude
date: 2026-10-20
---
She took the car in so I could sleep in on Saturday.
```

## Scheduled prompt (a routine in `person:<id>`)

1. Read `couples-checkin` records `joint-settings.md` (for `timezone`) and
   `settings-<id>.md` (for `quiet_hours`, `snoozed_until`, and
   `gratitude: paused`). If prompts are paused, snoozed, or it is quiet hours
   for this partner, reply with exactly `[SILENT]` and nothing else.
2. Otherwise reply with one short question, warm and plain. Vary the wording
   from one week to the next. Never more than one question, never a lecture on
   why gratitude matters.

## When the partner answers (their private chat)

1. Write `note-<date>-<id>.md` with `owner: <id>`, in their words.
2. Thank them in a sentence. Do not grade or embellish what they said.
3. Ask once whether they want their partner to hear it: they can tell them
   directly, or you can put it on this week's shared check-in agenda.
4. If they want it on the agenda, write `shared-<date>-<id>.md` with
   `owner: shared` and their words, unchanged or shortened only as they ask.
   That is a write from a private space to a shared one: the seat holds it and
   shows them the exact text. Show it word for word and ask them to reply
   "share it" if that is what they want shared. Nothing lands until they do.
   Never route around that confirmation.

## Pausing

"Stop the gratitude questions" or "pause gratitude" in their private chat: in
`couples-checkin`, write their `settings-<id>.md` with `gratitude: paused`
(keep every other line as it was, `overwrite: true`), read it back, and confirm.
"Start them again" removes the line. This is that partner's own setting and
never touches the other partner's.

## What this skill never does

- Never tells one partner what the other wrote, or whether they wrote anything.
- Never writes a note to a shared record without "share it".
- Never suggests what a partner should be grateful for, and never compares the
  two partners' notes.

## Verification

- A paused partner's scheduled run replies `[SILENT]`.
- p1's private note is absent from p2's chat and from the group.
- A note p1 chose to share lands only after p1's "share it", and its text is
  exactly the text p1 saw.
