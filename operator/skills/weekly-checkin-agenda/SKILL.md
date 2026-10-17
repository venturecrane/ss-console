---
name: weekly-checkin-agenda
description: The agenda for the couple's weekly joint check-in. Built in the shared group, on a schedule or on request, only from what each partner chose to share (agenda points, shared gratitude notes, and what is coming up on their shared lists, dates and trips). Never from anyone's private space.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Couples, Agenda, WeeklyCheckIn, Shared, SMD]
  smd:
    role: couples
    skill_type: scheduled assembly (shared space)
    weight: light
    action_class: read + internal_write # reads shared records; marks agenda points as included
    content_ceiling: shared_records_only
    stores: [couples-checkin, gratitude, dates, trips, lists]
---

# Weekly check-in agenda

Many couples set aside a time each week to sit down together. This skill
brings them a short agenda for it, in their shared group: the points each of
them asked to raise, the notes of appreciation they chose to share, and what
is coming up that week. It is built from shared things only. A partner's
private conversations with you never shape it, not even as a hint.

## Where you are

The scheduled run is in `shared` and is delivered to the group. The space
`shared` reads shared records only: the seat will not let this run open either
partner's private records (ADR 0091). That is the design, not a gap.

If a partner asks for the agenda in their private chat, their space can read
their own private records too. **Use only records with `owner: shared`.**
Leave out everything that partner keeps privately, even though you could read
it, because the agenda is a shared thing and the other partner did not see
those words.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use
`record_store_list`, `record_store_read` and `record_store_write` on the stores
below, and `calendar_invite_list` to see the week's shared events.

## What goes in it, in this order

1. **Points to talk about.** `couples-checkin` records with `kind: agenda-item`,
   `owner: shared`, not yet marked `included:`. Each in the words the partner
   confirmed, with who raised it if the record says so. These are the heart of
   the agenda. If there are none, say so in a line rather than inventing any.
2. **Appreciation.** `gratitude` records with `owner: shared` from the past
   week, in their words.
3. **Coming up.** From the shared store and calendar, the next 14 days: dates
   to remember (`dates`), trip legs (`trips`), renewals and chores due
   (`lists`), and shared events from `calendar_invite_list`. One line each.

Keep it short. No commentary, no suggestions about how the conversation should
go, no conclusions about the relationship. Never rank, compare or weigh what
one partner raised against the other.

## The scheduled run (a routine in `shared`)

1. Read the records above. If there is nothing in any section, reply with
   exactly `[SILENT]` and nothing else.
2. Otherwise reply with the agenda. The reply is delivered to the group.
3. Mark each agenda point you included: write it back with
   `included: <date>` added (`overwrite: true`), so next week starts fresh.
   Leave gratitude notes and coming-up items as they are.

## Adding a point

A partner adds a point from the group: write a `couples-checkin`
`agenda-<date>-<slug>.md` record with `owner: shared` and `kind: agenda-item`
at once. From a private chat the same write is held by the seat: show the exact
text, word for word, and ask them to reply "share it". Nothing lands until they
do. Never route around it (skill `couples-checkin` describes this).

## What this skill never does

- Never uses a private record, a private check-in answer, or anything said in
  a private chat that was not shared with "share it".
- Never adds a point a partner did not raise.
- Never summarizes how the week "went" for the couple.

## Verification

- An agenda built while p1 has private check-in answers and private gift ideas
  contains none of them.
- A point p1 raised in p1's chat appears only after p1's "share it".
- A run with nothing shared replies `[SILENT]`.
- An included point is marked and does not repeat the next week.
