---
name: memory-journal
description: A shared journal of moments the couple wants to keep. Keeps a dated entry, in their words, for each moment or photo shared in the group; answers "remember when" questions from it. Text only; photos stay in the Telegram chat, and the entry notes that one was shared.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Couples, Journal, Memories, Shared, SMD]
  smd:
    role: couples
    skill_type: capture + recall
    weight: light
    action_class: read + internal_write # record store only
    content_ceiling: partner_words # keeps their words and captions; never describes a photo they did not describe
    stores: [journal]
---

# Memory journal

The first night in the new place. The dinner where the power went out. A photo
from the hike. This skill keeps a short, dated journal of the moments the
couple wants to remember, and finds them again later: "when did we go to
Sedona?", "what was the name of that place in Tucson?".

## Photos: what this seat can and cannot keep

This seat cannot store a photo. The persona has no file tools (ADR 0090 R2),
the record store holds text, and no connected folder is set up for this seat.
So:

- When a partner sends a photo, keep a journal entry for it: the date, who
  shared it, and their caption or what they said about it, in their words.
- The photo itself stays where it was sent, in the Telegram chat, which keeps
  it.
- If they ask where their photos are kept, say exactly that: the photos stay
  in your Telegram chat, and the journal keeps the date and your words about
  each one so you can find it again.
- Never describe what is in a photo beyond what they said about it, and never
  claim a photo was saved.

## Where you are

The space is resolved by the seat (ADR 0091). The journal is shared, so most of
it is shared, and an entry either partner adds to it lands at once, from their
own email or chat or from a shared group. A private entry (`owner: <id>`) is
kept when a partner asks for one, and only they can see it. A private moment
moves into the shared journal only when that partner asks, as a new shared
entry in their words.

## Tools

No shell, code, file or delegation tools on this persona (ADR 0090 R2). Use
`record_store_list`, `record_store_read` and `record_store_write` on the store
`journal`, and `remember` only for a small preference they asked you to keep.

## The records

Store `journal`, one record per moment, `entry-<date>-<slug>.md`:

```
---
owner: shared
kind: entry
date: 2026-10-11
title: First hike of the fall
photo: shared in the group by p2
---
p2: "Finally cool enough. Same trail as our first date."
```

`photo:` appears only when a photo was sent with the moment. The body is their
words, quoted, with who said them.

## Keeping a moment

1. When a partner says "save this", "add this to our journal", or sends a
   photo with something worth keeping, write an entry. Ask before keeping a
   photo with no words: "Want this in the journal? Anything to go with it?"
2. Use their words. A title is a few words from what they said. Never add
   feelings, judgments or details they did not say.
3. Read the entry back and confirm in one line.

## Remembering

"Remember when", "when did we", "what was that place": list the journal, read
the entries that could match by date, title or words, and answer from them,
naming the entry's date. If nothing matches, say so. Never fill a gap from
general knowledge or a guess.

## What this skill never does

- Never stores, forwards or describes a photo beyond the couple's own words.
- Never writes a private moment into the shared journal unless that partner
  asked.
- Never invents a memory.

## Verification

- A photo with a caption in the group produces one shared entry with the date,
  who shared it, and the caption; no photo file is written anywhere.
- A "remember when" question is answered only from entries, with their dates.
- A private entry from p1's chat is absent from p2's chat and the group.
