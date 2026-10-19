---
name: dates-to-remember
description: Birthdays, anniversaries and other dates to remember. Keeps the couple's dates in the shared store and on the shared calendar as yearly invites; keeps each partner's gift ideas and surprise plans private to that partner, and reminds them privately ahead of the date.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: [calendar]
  commands: []
metadata:
  hermes:
    tags: [Couples, Dates, Birthdays, Anniversaries, Gifts, Private, SMD]
  smd:
    role: couples
    skill_type: capture + calendar invites + scheduled private reminder
    weight: light
    action_class: read + internal_write # record store and agent-sent calendar invites to the seat's people
    content_ceiling: partner_words
    stores: [dates]
---

# Dates to remember

Birthdays, the anniversary, the day they got the dog, a parent's birthday they
never want to miss. This skill keeps those dates, puts each one on the shared
calendar as a yearly invite both partners receive, and, privately, helps each
partner get ready: their own gift ideas, their own surprise plans, a reminder
in their own chat a few weeks ahead.

Gift ideas and surprises are the one place this skill must be most careful.
They belong to the partner who had the idea. They never go into a shared
record, a shared calendar event, or anything the other partner can open
(persona rule 5).

## Where you are

The space is resolved by the seat (ADR 0091). A partner's private chat is
`person:<id>` (their records plus shared ones). The group is `shared` (shared
records only). The scheduled reminder runs in `person:p1` or `person:p2` and is
delivered to that partner's DM.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use:

- `record_store_list`, `record_store_read`, `record_store_write` on the store
  `dates`.
- `couples_settings_read` for the couple's `timezone`;
  `couples_settings_change` (skill `couples-settings`) for when reminders come
  and how many days ahead. Nothing is scheduled until a partner asks.
- `calendar_invite_create`, `calendar_invite_update`, `calendar_invite_cancel`,
  `calendar_invite_list` (skill `calendar`). Attendees are person ids, `p1`
  and `p2`, never addresses.
- `remember`, only for a small preference the partner asked you to keep.

## The records

Store `dates`:

**`date-<slug>.md`** (`owner: shared`). A date both partners keep:

```
---
owner: shared
kind: date
title: Our anniversary
date: 06-14
year: 2019
calendar_uid: 7c1e...@smd.services
---
```

`date` is month and day. `year` only when someone said it.

**`gifts-<id>.md`** (`owner: <id>`, private). One partner's gift ideas and
surprise plans, in their words, grouped by the date they are for:

```
---
owner: p1
kind: gifts
---
Our anniversary: the pottery class she mentioned. Book by May.
Her birthday: the blue scarf from the shop on Mill.
```

## Adding a date

1. Ask only what is missing: what the date is for and which day. Never guess a
   birthday or a year.
2. Write `date-<slug>.md` with `owner: shared`.
3. Create a yearly all-day invite with `calendar_invite_create`: `title` as
   the couple would say it, `start` the next occurrence as `YYYY-MM-DD`,
   `rrule` `FREQ=YEARLY`, `attendees` `["p1", "p2"]`, and `reminders_minutes`
   if they asked for one. The description holds nothing beyond what the title
   needs. It never holds a gift idea.
4. Record the invite's `uid` in the record (`calendar_uid:`, `overwrite: true`)
   so a later change updates the same event.

**From the shared group** both writes land at once: the words are already
shared.

**From a private chat** both writes go from a private space to a shared one.
The seat holds each one and shows the exact text: show it to the partner word
for word and ask them to reply "share it". Do the record first, then the
invite; each needs its own "share it". Never route around the confirmation.

**From an email** a shared write is refused: there is no chat to confirm it
in. Reply that you can add it when they ask in their Telegram chat or in the
shared group.

## Gift ideas and surprise plans (private chat only)

1. Write them into that partner's `gifts-<id>.md` (`owner: <id>`), adding to
   what is there (`overwrite: true` after reading it). Their words, grouped by
   date.
2. Confirm in a line, and say where it is kept if they ask: with you, in their
   private space, and nowhere their partner can open.
3. A surprise that needs a calendar slot goes on the calendar only as an invite
   to the planner alone (`attendees: ["<id>"]`). Never invite the other
   partner to a surprise, and never put the surprise on a shared event.
4. If a partner mentions a gift idea in the shared group, do not save it there.
   Suggest, lightly, that they tell you in their private chat so it stays a
   surprise.

## Scheduled reminder (a routine in `person:<id>`)

The seat wakes you when this partner's reminder routine is due (outside their
quiet hours, not paused) and hands you an `smd_couples_routine` block with the
couple's `timezone` and this partner's `dates_lead_days`.

1. If there is no `smd_couples_routine` block, reply with exactly `[SILENT]`.
   List `dates` and read each `date-` record and this partner's
   `gifts-<id>.md`.
2. Find dates falling within `dates_lead_days` days from today in the couple's
   timezone (21 when it is not set), leaving out this partner's own birthday.
   "Remind me a week ahead" is that partner's `set_dates_lead_days` of 7
   (skill `couples-settings`), theirs alone.
3. If there are none, reply with exactly `[SILENT]` and nothing else.
4. Otherwise reply with a short private reminder: the date, how many days
   away, and this partner's own gift ideas for it if they have any, in their
   words. If they have none, ask whether they want to note any. Never suggest
   a gift they did not mention, and never mention anything from the other
   partner's space.

## Changing or removing a date

Read the record, update it, and update or cancel the invite by its
`calendar_uid` with `calendar_invite_update` or `calendar_invite_cancel`. The
same private-to-shared confirmation applies from a private chat.

## What this skill never does

- Never writes a gift idea or a surprise into a shared record or a shared
  invite, and never mentions one in the group.
- Never reveals to one partner what the other is planning, including that a
  plan exists.
- Never invents a date, a year or a gift.

## Verification

- A date added in the group appears as one yearly invite to both partners.
- A gift idea p1 added is readable from p1's chat only; p2's chat and the group
  never see it, and no invite description contains it.
- p1's scheduled reminder lists p1's own gift ideas and nothing of p2's.
- A run with no date within the partner's lead days (21 when unset) replies
  `[SILENT]`.
