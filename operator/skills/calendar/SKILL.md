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

## Privacy

On a seat that authors `people:`, the shared calendar is a shared space. A
request made in one person's private conversation that would put something on
the shared calendar is a write from a private space to a shared one: show the
person the exact event text that will land and wait for their yes before the
invite is sent (ADR 0091, enforcement point 4). Nothing private to one person
goes into an event the other person receives.

## Verification

- A created event reaches every rostered person as an invite.
- An update or a cancellation reaches the same people and replaces the event
  in their calendar rather than adding a second one.
- An invite to an address off the roster is refused.
