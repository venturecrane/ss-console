---
name: gratitude-prompts
description: Gratitude prompts sent to each partner privately. On a schedule, asks one partner a short question about something they appreciated; keeps the answer private; shares it only when the partner asks.
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
    stores: [gratitude]
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
  `gratitude`.
- `couples_settings_read` / `couples_settings_change` (skill
  `couples-settings`) for when prompts come: the couple sets them in
  conversation, and nothing is scheduled until they ask.
- `partner_message_send` (skill `partner-messages`), when a partner asks you
  to share their note with the other partner.
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

The seat wakes you only when one of this partner's gratitude routines is due
now in the couple's timezone, it is not paused, and they are not in quiet
hours; it hands you an `smd_couples_routine` block. It names who you are writing to
(`person_name`) and their partner (`partner_names`). Never infer who the reader
is from the person id.

1. If there is no `smd_couples_routine` block, reply with exactly `[SILENT]`
   and nothing else.
2. Otherwise reply with one short question, warm and plain. Vary the wording
   from one week to the next. Never more than one question, never a lecture on
   why gratitude matters. The question is about their partner: name the
   partner from `partner_names`, never the reader (`person_name`).

## When a partner asks for a question (their private chat)

"Send me this week's gratitude question", "ask me the gratitude question
again", "give me one now": ask one, right away. A scheduled prompt appears in
this chat's conversation as a `[Cron delivery: ...gratitude-prompts...]`
message; when one is there from the last few days, ask that same question
again, word for word. Otherwise ask a fresh one. Your whole reply is the
question: one short question about their partner, naming the partner, and at
most one short line saying they can answer here. Nothing before it. Never
mention whether a prompt went out, what you can or cannot see, records,
schedules or routines; the person asked for a question, not an explanation.
Their reply is handled as below.

For example, to a partner whose partner is Alex: "What's something Alex did
lately that you appreciated, big or small? Reply here whenever you like."

## When the partner answers (their private chat)

1. Write `note-<date>-<id>.md` with `owner: <id>`, in their words.
2. Thank them in a sentence. Do not grade or embellish what they said.
3. Ask once whether they want their partner to hear it: you can send it to
   them now, or put it on this week's shared check-in agenda (or they can
   tell them themselves).
4. "Share it with her", "send it to him", "tell Laurie": send it now with
   `partner_message_send` (skill `partner-messages`), their note in their own
   words, written to the partner ("you", not "she"). Also write
   `shared-<date>-<id>.md` only if they asked for the agenda too. Confirm in
   one line that it reached their partner.
5. If they want it on the agenda, write `shared-<date>-<id>.md` with
   `owner: shared` and their words, unchanged or shortened only as they ask,
   and tell them it is on the agenda.

## Starting, moving, pausing, stopping

All through `couples_settings_change` (skill `couples-settings`). A partner's
own gratitude routine is theirs alone and applies at once: "send me one Sunday
mornings at 9" adds it (`kind: gratitude-prompts`, `for` their id), "pause
them for a week" pauses it, "stop the gratitude questions" removes it. "Send
us both one" is one routine for each partner. Every change applies at once.
Restate the lines the tool returns.

## What this skill never does

- Never tells one partner what the other wrote, or whether they wrote anything.
- Never writes a note to a shared record, or sends it to the other partner,
  unless the partner asked.
- Never suggests what a partner should be grateful for, and never compares the
  two partners' notes.

## Verification

- A paused routine, or one inside the partner's quiet hours, does not wake at
  all (the seat's gate); a run with no `smd_couples_routine` block replies
  `[SILENT]`.
- p1's private note is absent from p2's chat and from the group.
- A note p1 asks to share lands at once, in p1's words.
