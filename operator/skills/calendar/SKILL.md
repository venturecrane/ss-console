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

- `calendar_create`: create an event and send the invite.
- `calendar_update`: change an event; every recipient gets the update.
- `calendar_cancel`: cancel an event; every recipient gets the cancellation.
- `calendar_list`: list the events the agent holds.

Invites go only to addresses on the seat's roster (`people[].email`). There is
no tool to invite anyone else, and a request to do so is declined plainly.

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
