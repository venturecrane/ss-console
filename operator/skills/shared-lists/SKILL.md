---
name: shared-lists
description: Shared lists, chores, expenses and renewals. Keeps the couple's lists in the shared store, tracks who is doing which chore, records shared expenses and the split the couple chose, and puts renewals on the calendar as invites ahead of their dates.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: [calendar]
  commands: []
metadata:
  hermes:
    tags: [Couples, Lists, Chores, Expenses, Renewals, SMD]
  smd:
    role: couples
    skill_type: capture + recall + calendar invites
    weight: light
    action_class: read + internal_write # record store and agent-sent invites to the seat's people
    content_ceiling: partner_words
    stores: [lists]
---

# Shared lists

The grocery list, the weekend projects, who is taking the recycling out, what
they spent on the vet and how they split it, when the car registration is due.
This skill keeps all of it in one place both partners can see and add to.

## Where you are

The space is resolved by the seat (ADR 0091). The group is `shared` and reads
shared records only. A partner's private chat is `person:<id>` and reads their
own records plus shared ones.

- Either partner adds to a shared list alone, from their own email or chat or
  from a shared group. It lands at once.
- Anything a partner wants kept to themselves goes on a private list
  (`owner: <id>`) instead, which lands at once and only they can see.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use:

- `record_store_list`, `record_store_read`, `record_store_write` on the store
  `lists`; `couples_settings_read` for the couple's `timezone`.
- `calendar_invite_create`, `calendar_invite_update`, `calendar_invite_cancel`,
  `calendar_invite_list` (skill `calendar`), for renewals and chore dates.
  Attendees are person ids.

## The records

Store `lists`, one record per list, `<kind>-<slug>.md`. `kind` is `list`,
`chores`, `expenses` or `renewal`.

```
---
owner: shared
kind: list
title: Groceries
---
- [ ] oat milk (p1)
- [x] coffee (p2)
```

```
---
owner: shared
kind: expenses
title: Household, October
split: 50/50
---
2026-10-03 | vet | 180.00 | paid by p1
2026-10-09 | groceries | 92.40 | paid by p2
```

```
---
owner: shared
kind: renewal
title: Car registration
due: 2026-12-01
calendar_uid: ...
---
```

Every edit reads the record first and writes it whole (`overwrite: true`),
then reads it back before you confirm.

## Lists and chores

- Add, check off and remove items as asked. Note who added an item only when
  it helps them.
- Chores: who has which chore and how often, as the couple says. If they want
  a reminder, send an invite to the partner who has the chore, on the schedule
  they named (`rrule`, for example `FREQ=WEEKLY;BYDAY=SA`).
- Never decide who should do a chore, and never keep score of who does more.

## Expenses

1. Record each expense as they state it: date, what, amount, who paid.
2. The `split` is whatever the couple chose. Never propose a split or judge
   one. If none is recorded, ask them, in the group, how they want to split.
3. When asked where things stand, show the working line by line so both can
   check it: the total each paid, each share under the split, and the
   difference. Say it is a running total from what they told you.
4. Never connect to a bank, card or payment app, and never move money.
   Settling up is theirs to do.

A partner's private spending stays private: if they record an expense in a
private list, it never appears in a shared total (persona rule 3).

## Renewals

1. Record the renewal and its due date.
2. Offer a reminder ahead of it. Create an all-day invite on the reminder date
   they choose, to both partners or to the partner who handles it, and record
   its `uid`.
3. When it is renewed, update `due` to the next date and move the invite with
   `calendar_invite_update`.

## What this skill never does

- Never pays, buys, orders or renews anything.
- Never takes a side on chores or money.
- Never shows one partner's private list to the other.

## Verification

- An item added in the group appears for both partners at once.
- An item a partner adds from their own email or chat lands on the shared list
  at once.
- A private list is absent from the other partner's chat and the group.
- An expense total shows its working and matches the recorded lines.
