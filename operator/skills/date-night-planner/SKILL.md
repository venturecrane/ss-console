---
name: date-night-planner
description: Date night ideas with a tried, loved, skip history. Suggests ideas from what the couple has enjoyed before and, when asked, from a web search; keeps the history in the shared store; proposes a chosen night to both partners as a calendar invite.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: [calendar]
  commands: []
metadata:
  hermes:
    tags: [Couples, DateNight, Ideas, History, SMD]
  smd:
    role: couples
    skill_type: ideas + shared history + calendar invite
    weight: light
    action_class: read + internal_write # web search reads; record store and agent-sent invites to the seat's people
    content_ceiling: partner_words + cited_search_results
    connectors:
      - brave # WebSearch, for new ideas only when asked
    stores: [date-nights]
---

# Date night planner

"What should we do Friday?" This skill answers from what the couple already
knows they like, keeps a running list of ideas and how each one went, and when
they settle on one, sends both of them a calendar invite.

## Where you are

The space is resolved by the seat (ADR 0091). The shared group is `shared` and
reads shared records only. A partner's private chat is `person:<id>` and reads
their own records plus shared ones. Date night is a shared thing, so most of
this skill happens in the group. A partner planning a surprise in their private
chat keeps that plan private (see below).

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use:

- `record_store_list`, `record_store_read`, `record_store_write` on the store
  `date-nights`; `couples_settings_read` for the couple's `timezone`.
- `web_search`, for new ideas, only when the couple asks for something new.
- `calendar_invite_create`, `calendar_invite_update`, `calendar_invite_cancel`,
  `calendar_invite_list` (skill `calendar`). Attendees are person ids.
- `remember`, for a small preference they asked you to keep ("no seafood").

## After a web search

A web search brings outside content into the conversation, and the seat treats
everything after it with care:

- **Saving.** In a partner's private chat, an idea saved after a search is kept
  in that partner's own space (`owner: <id>`). The seat marks it "from a web
  search" (`from_web: true`). Sharing it with both still goes through "share
  it": the seat holds the shared write and shows the exact text, marked as from
  a web search; show it word for word and ask them to reply "share it". In the
  shared group, nothing can be saved after a search; ask the partner who wants
  it kept to tell you in their private chat.
- **Calendar.** After a search, calendar invites are refused for the rest of
  that conversation. Save the find privately as above, then tell the person to
  say "put it on our calendar" in a new message in a fresh conversation (in
  Telegram, `/new` starts one), where you read the saved idea and propose the
  invite. Do not suggest any other way, and never claim an invite was sent.

So do every write the conversation already needs (a verdict on last week, an
idea they named themselves) **before** you search, and when you search, say
what you found and where, and let them choose.

## The records

Store `date-nights`, one record per idea, `idea-<slug>.md`:

```
---
owner: shared
kind: idea
idea: Sunset hike at Papago, then tacos
status: loved
last_tried: 2026-10-10
---
Added by p2. Loved it; want to go back when it is cooler.
```

`status` is one of `idea`, `planned`, `tried`, `loved`, `skip`. `skip` means
they do not want it suggested again. Notes are their words.

## Suggesting ideas

1. List the store. Start from what they `loved` and have not done lately, then
   `idea` entries they added but have not tried. Never suggest a `skip`.
2. Offer two or three, briefly. If they ask for something new, or the list is
   thin, search the web for things near them that fit what they asked for, and
   name the source of each suggestion. Do not present a search result as
   something you know is open, available or a good fit; say it is what the
   search shows.
3. Use what they told you they prefer. Never assume a preference.

## Recording how it went

After a date, either partner can say "loved it", "fine", or "never again".
Update the idea's `status` (`loved`, `tried`, `skip`), `last_tried`, and add
their note in their words (`overwrite: true` after reading the record). In the
group this lands at once. From a private chat it is a write to the shared
store: the seat holds it and shows the exact text; show it word for word and
ask them to reply "share it". A partner who would rather keep their verdict to
themselves can simply not share it.

## Proposing the night

When they pick an idea and a time:

1. Create the invite with `calendar_invite_create`: `title` the idea in their
   words, `start` and `end` in the couple's `timezone` (from
   `couples_settings_read`, passed as `timezone`; if it is not set yet, ask), `location` if they gave one,
   `attendees: ["p1", "p2"]`.
2. Set the idea's `status: planned` in its record.
3. From a private chat, the invite and the record each go through "share it"
   before they land.

## A surprise date (private chat)

A partner planning a surprise keeps it in their own space: write the idea with
`owner: <id>` instead of `shared`, and if it needs a calendar slot, invite only
the planner (`attendees: ["<id>"]`). Never put a surprise on a shared invite or
in a shared record, and never mention it in the group.

## What this skill never does

- Never suggests an idea marked `skip`.
- Never books, buys or reserves anything, and never contacts a venue.
- Never reports a search result as confirmed fact.
- Never reveals one partner's private surprise or private verdict.

## Verification

- An idea marked `skip` never appears in suggestions.
- A chosen night arrives as one invite to both partners, in the couple's
  timezone.
- An idea saved after a web search lands in the asking partner's own space,
  marked from a web search, and reaches the shared store only after "share it".
- After a web search, an invite is reported as not sent, never as sent.
