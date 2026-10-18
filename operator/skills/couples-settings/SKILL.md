---
name: couples-settings
description: The couple's schedule and settings, by talking. Set in conversation with the agent, check-in times, gratitude prompts, dates-to-remember lead time, the weekly agenda, quiet hours, the timezone, check-in topics, pauses. Changes that affect only the person asking apply at once; anything that touches the other partner or both waits until both have said yes.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Couples, Settings, Schedule, Spaces, SMD]
  smd:
    role: couples
    skill_type: conversational settings
    weight: light
    action_class: read + internal_write # couples_settings_read / couples_settings_change; no send tool
    content_ceiling: setting_values # restates the setting the seat rendered; authors nothing else
    stores: []
---

# Couples settings

The couple tells you how they want things to run, in plain words, on whatever
channel they are using. There is no form and nothing for them to fill in. You
turn what they say into a change, the seat keeps it, and from then on the
scheduled messages come on that rhythm.

Nothing is scheduled until the couple asks. Until they say so there are no
check-ins, no gratitude prompts and no quiet hours, and the timezone is not
known.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use
only these two:

- `couples_settings_read`: what is set, as this conversation may see it. It
  lists the people (each with an `id`), the timezone, the check-in topics, the
  routines you can see (each with an `id` and a plain description), this
  person's own settings when you are in their private conversation, and any
  change still waiting for both partners.
- `couples_settings_change`: one to six changes applied together. Each change
  is one `op`:

| They say                                       | `op` and fields                                                                                               |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| "We're on Pacific time"                        | `set_timezone` `{timezone: "America/Los_Angeles"}`                                                            |
| "Check in with us about work and money"        | `set_checkin_topics` `{topics: ["how work is going", "money"]}`                                               |
| "No messages after 9pm" (about themselves)     | `set_quiet_hours` `{person, start: "21:00", end: "08:00"}` (ask when quiet hours end if they did not say)     |
| "Messages any time again"                      | `clear_quiet_hours` `{person}`                                                                                |
| "Remind me of birthdays a week ahead"          | `set_dates_lead_days` `{person, lead_days: 7}`, plus a `dates-to-remember` routine for them if they have none |
| "Skip money in my check-ins for now"           | `set_paused_topics` `{person, topics: ["money"]}`                                                             |
| "Check in with me Tuesdays and Fridays at 8pm" | `add_routine` `{kind: "couples-checkin", for: <their id>, days: ["tue", "fri"], time: "20:00"}`               |
| "Move it to 8:30"                              | `change_routine` `{routine: <id>, time: "20:30"}`                                                             |
| "Pause the check-ins for two weeks"            | `pause_routine` `{routine: <id>, until: <the date two weeks from today>}` for each check-in routine           |
| "Start them again"                             | `resume_routine` `{routine: <id>}`                                                                            |
| "Stop the gratitude prompts"                   | `remove_routine` `{routine: <id>}`                                                                            |

Routine kinds: `couples-checkin`, `gratitude-prompts` and `dates-to-remember`
are for one person (`for` is a person `id`); `weekly-checkin-agenda` and
`travel` (price watches) are for both together (`for: "shared"`). "Send us
both a gratitude prompt Sunday mornings" is two `gratitude-prompts` routines,
one for each person, in one call. `for` and `person` are always an `id` from
the people list, never a name or an email address.

Days are `mon` to `sun`. Times are 24-hour `HH:MM` on the quarter hour
(`:00`, `:15`, `:30`, `:45`) in the couple's own timezone. If someone asks for
8:10, ask whether 8:00 or 8:15 suits. "Mornings" or "evenings" alone is not a
time: ask for one. Never fill in a day, a time, a timezone or an end of quiet
hours they did not give you.

## The timezone comes first

A routine needs the couple's timezone. If `couples_settings_read` shows
`timezone: null`, ask which timezone they are in before adding a routine, then
send the timezone and the routine in the same call. The timezone affects both
partners, so it is decided together (below).

## Who decides

The seat decides this from the change, not from you:

- **Theirs alone, applied at once**: the person's own quiet hours, their own
  dates lead time, the topics they skip, and their own gratitude or
  dates-to-remember routines.
- **Decided together, applied only when both say yes**: the timezone, the
  check-in topics, any check-in routine (even one for the person asking), the
  other partner's settings or routines, and anything shared.

When a change is decided together, the seat itself sends the other partner the
exact change and asks them to reply `agree` or `reject`. The person asking has
already said yes by asking. Tell them, restating the lines the tool gives you,
that it is waiting on their partner. Do not call the tool again for it and do
not look for another way to make it land. Either partner can `reject`; it
expires in 7 days; only one such change can wait at a time. If the tool says
another change is still waiting, tell them so.

Send a person's own changes separately from joint ones: a call that mixes them
is decided together as a whole.

The other partner is shown the setting, never the conversation. Do not add to
it, explain it, or pass along anything else the person said.

## Confirming

When the tool says `DONE`, reply with exactly the lines it gave you, and any
note it adds (for example that a routine falls in someone's quiet hours). Add
no times, days or promises of your own. When it says `HELD`, say it is waiting
on the other partner, with the same lines. When it says `refused`, tell the
person plainly what to change, in one or two sentences.

## "What do you have set up?"

Call `couples_settings_read` and list the routines by their descriptions, the
timezone, the topics, and (in their private conversation) their own quiet
hours and lead time. In someone's private conversation you see their own
routines plus shared ones and check-ins; you never see, and never mention,
the other partner's private routines. Say "nothing yet" when nothing is set.

## What this skill never does

- Never schedules anything the couple did not ask for, and never invents a
  time, a day or a timezone.
- Never names an email address as who a routine is for.
- Never tells one partner about the other partner's private settings or
  routines.
- Never announces a joint change as done before both have agreed.
- Never relays a partner's private words in a proposal or anywhere else.

## Verification

- "Check in with me Tuesdays and Fridays at 8pm" with no timezone set: you ask
  for the timezone first.
- p1's own quiet hours apply at once and the confirmation restates them.
- A check-in routine for p1 waits until p2 replies `agree`; p1's second
  `agree` changes nothing.
- "What reminders do you have?" from p1 lists nothing that is p2's alone.
