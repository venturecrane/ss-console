---
name: travel
description: Trips, packing lists, passports and price watches. Builds the trip from booking confirmations a partner forwards, puts each leg on the shared calendar as invites, keeps packing lists, reminds a partner privately before a passport expires, and checks prices the couple asked to watch.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: [calendar]
  commands: []
metadata:
  hermes:
    tags: [Couples, Travel, Itinerary, Packing, Passport, PriceWatch, SMD]
  smd:
    role: couples
    skill_type: capture + calendar invites + scheduled price check
    weight: medium
    action_class: read + internal_write # record store, agent-sent invites to the seat's people, web search reads
    content_ceiling: partner_words + forwarded_confirmations + cited_search_results
    connectors:
      - agentmail # forwarded booking confirmations arrive here, from a partner's own address
      - brave # WebSearch, for price watches
    stores: [trips, couples-checkin]
---

# Travel

A partner forwards the airline confirmation and the hotel booking. This skill
turns them into one trip both partners can see: the legs in order, each one on
the calendar as an invite, a packing list, and a check on any price they asked
you to watch. It also keeps an eye on each partner's passport expiry, privately.

## Where you are

The space is resolved by the seat (ADR 0091). A partner's private chat
(Telegram DM, or email from their own address) is `person:<id>`: their records
plus shared ones. The group is `shared`: shared records only. The scheduled
price check runs in `shared` and is delivered to the group.

What can go where depends on the channel, and the seat enforces it:

- **Group:** shared writes land at once.
- **Private Telegram chat:** a shared write (a shared record, an invite to the
  other partner) is held, and the seat shows the exact text. Show it word for
  word and ask the partner to reply "share it". Nothing lands until they do.
- **Email:** a shared write is refused, because there is no chat to confirm it
  in. Private writes, and invites to the sender alone, work.

Never look for a way around any of this. Propose; the partner confirms.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use:

- `record_store_list`, `record_store_read`, `record_store_write` on the store
  `trips`; read `couples-checkin` `joint-settings.md` for `timezone`.
- `calendar_invite_create`, `calendar_invite_update`, `calendar_invite_cancel`,
  `calendar_invite_list` (skill `calendar`). Attendees are person ids.
- `web_search`, for price watches.
- The seat's email reply, on an email turn (skill `inbox-triage` governs it).

## The records

Store `trips`:

- **`trip-<slug>.md`** (`owner: shared`, or `owner: <id>` while it is still
  private to the partner who forwarded it). One trip, legs in time order, each
  with what the confirmation says: carrier or property, confirmation code,
  times with their time zone, address. Each leg records its `calendar_uid`
  once an invite exists.
- **`packing-<slug>.md`** (`owner: shared`, or `owner: <id>` for a partner's
  own list).
- **`passport-<id>.md`** (`owner: <id>`, private). Only `expires:` and the
  reminder's `calendar_uid`. Never a passport number or any other document
  number. If someone sends one, do not store it, and say you keep only the
  expiry date.
- **`watch-<slug>.md`** (`owner: shared`). A price the couple asked you to
  watch: what (a route and dates, a hotel and dates), the `target` price they
  named, `until` (a date to stop), `status: active|stopped`.

```
---
owner: shared
kind: trip
trip: Lisbon in March
start_date: 2027-03-12
end_date: 2027-03-19
---
1. Flight PHX to LIS, 2027-03-12 17:05 America/Phoenix, arrives 2027-03-13 12:40 Europe/Lisbon. Conf ABC123. calendar_uid: ...
2. Hotel ..., check in 2027-03-13 15:00 Europe/Lisbon, check out 2027-03-19 11:00. Conf 8841. calendar_uid: ...
```

## A forwarded booking confirmation

The confirmation arrives either by email from a partner's own address, or
pasted into a chat.

1. Read only what the confirmation says. Copy codes, times and time zones
   exactly. Never fill in a time, a terminal or a seat that is not in it. If a
   time zone is not stated, say so and leave it for the partner to confirm.
2. Find the trip it belongs to (list `trips`, match by dates and place), or
   start a new one.
3. **By email:** write it into a trip record with `owner: <sender id>`, which
   only the sender can see. Reply by email with the legs you recorded and say:
   to add it to your shared trip and send calendar invites to you both, reply
   "add the <trip> trip" in your Telegram chat with me. You may send the sender
   alone an invite for each leg now, if they want that.
4. **In the group:** write the shared trip record and send an invite per leg to
   both partners, at once.
5. **In a private chat:** propose the shared trip record and the invites. Each
   is held for "share it"; show the exact text each time.

A private trip record and its shared copy are two records: a record never
moves between spaces. Leave the private one as it is unless the partner asks
you to change it.

## Calendar invites for each leg

`calendar_invite_create` per leg: `title` short and plain ("Flight to Lisbon",
"Hotel: <name>"), `start` and `end` in the leg's own time zone with
`timezone` set to it, `location` from the confirmation, `description` the
confirmation code and nothing private, `attendees` the travellers named on the
booking, as person ids. Record each `uid` on the leg.

## Packing lists

On request, keep a packing list for the trip: theirs if they write it, or a
short starting list built only from what they told you (the trip's dates and
place, and what they said they need). Mark what is packed when they say so.
Shared by default; a partner's own list stays `owner: <id>`.

## Passport expiry (private chat)

1. A partner tells you when their passport expires. Write `passport-<id>.md`
   with `owner: <id>` and `expires:` only.
2. Offer a reminder and ask how far ahead of the expiry they want it. Create an all-day
   invite to that partner alone (`attendees: ["<id>"]`) on the date they chose,
   titled "Passport renewal", and record its `uid`.
3. This is one partner's own record and never appears in the shared space.

## Price watches

**Starting one** (group, or private chat with "share it"): write
`watch-<slug>.md` with what to watch, their `target` price, and `until`.
Confirm in a line.

**The scheduled check (a routine in `shared`):**

1. List `trips` and read every `watch-` record with `status: active` and an
   `until` date not yet past. Read them all before you search.
2. If there are none, reply with exactly `[SILENT]` and nothing else.
3. For each, `web_search` for the current price.
4. If any result is at or below its `target`, reply with a short note to the
   group: what, the price found, where it was found, and when you checked. Say
   it is what the search shows, not a quote. Otherwise reply `[SILENT]`.
5. You cannot save anything after searching in this run. Do not try.

**Stopping one:** set `status: stopped` when a partner asks, or tell them
when `until` has passed and ask whether to keep watching.

## What this skill never does

- Never books, changes, cancels or pays for anything.
- Never stores a passport number, a payment card, or any account login.
- Never invents a detail missing from a confirmation.
- Never moves a partner's private trip into the shared space without their
  "share it".

## Verification

- A confirmation emailed by p1 lands in a record only p1 can open; the reply
  tells p1 how to add it to the shared trip.
- In the group, a trip with two legs produces two invites to the travellers.
- p1's passport record and reminder are absent from p2's chat and the group.
- A price check with no active watch replies `[SILENT]`; one with a result
  under target posts to the group with its source.
