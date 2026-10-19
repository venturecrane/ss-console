---
name: calendar
description: Keeps the shared calendar the agent owns. Creates, updates, cancels and lists events and sends each one as an invite to the people on the seat.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Calendar, Invites, SMD]
---

# Calendar

## When to Use

Use when a person on the seat asks to put something on the calendar, change or
cancel an event, or see what is coming up. The calendar is the agent's own: the
seat holds the events, and each person receives them as calendar invites in
their own email, so the event appears in whatever calendar they already use
(ADR 0092). The agent never reads anyone's personal calendar.

## Tools

- `calendar_invite_create`: create an event and send the invite.
- `calendar_invite_update`: change an event; every recipient gets the update.
- `calendar_invite_cancel`: cancel an event; every recipient gets the
  cancellation.
- `calendar_invite_list`: list the events the agent holds.

Invite only the people the request is for. `attendees` names them: on a seat
that authors `people:`, by person id (for example `p1`, `p2`); otherwise by
address. Each must be one of the seat's people, whose addresses are on the
seat's roster (`scope.inbound_allow_from`). There is no way to invite anyone
else, and a request to do so is declined plainly.

## Shaping the event

- **Always give it a description.** One plain sentence in the requester's own
  terms saying what the event is, so whoever opens the invite knows without
  asking. For example: "Scott's weekends with the kids, every other Friday
  through Sunday." Use only what they told you; never add details they did not
  give.
- **One event per stretch of days.** "Friday, Saturday and Sunday" is ONE
  all-day event: `start` the Friday as `YYYY-MM-DD`, `end` the day AFTER the
  Sunday (the end date is exclusive). Never three separate events.
- **Repeating.** `rrule` carries the pattern, for example
  `FREQ=WEEKLY;INTERVAL=2` for every other week, with no `UNTIL` or `COUNT`
  when they said "no end date". The first occurrence is `start`.
- **"This coming Friday"** is the next Friday after today in the person's
  timezone. Say the date you used in your reply.
- A timed event needs a `timezone` name. An all-day event does not.

## Who can add what

Either person adds, changes or cancels an event on their own, and it is sent at
once (ADR 0091, point 4, amended 2026-10-19). Nothing one person keeps private
(a gift, a surprise) goes into an event the other person receives.

## When something fails

If a tool refuses or fails, say plainly what did not happen ("the invite did
not go out"). Never offer a capability the seat does
not have (connecting a Google account, granting access), and never ask the
person to do the work themselves by hand.

## Verification

- A created event reaches every rostered person as an invite.
- An update or a cancellation reaches the same people and replaces the event
  in their calendar rather than adding a second one.
- An invite to an address off the roster is refused.
- "Every other week, Friday through Sunday, no end" is one all-day invite with
  `rrule` `FREQ=WEEKLY;INTERVAL=2` spanning three days, reaching both people.
