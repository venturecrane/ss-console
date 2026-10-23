---
name: operator-help
description: >-
  How to work with me, and how to ask me for things. It answers "how do we work
  together?", "how should I ask you for things?", "should I prompt you the way I
  prompt Claude?" and "do I need to point you at the documents?" with the
  SMD-authored guide, shaped for the person asking: the requests this seat
  invites that THEY may make, worded so they can copy them, and the habits that
  get good results. Every request it offers is one this seat's own facts list
  for this sender; it never names one the seat does not carry, never names its
  own machinery, and never promises a time.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Ops, Help, Guide, Onboarding, ReadOnly]
  smd:
    vertical: neutral # product skill; every seat may ship it
    weight: light
    action_class: read + one reply to the requester
    content_ceiling: counts_and_status_only # the guide, the seat's own help facts, no matter content
    connectors: []
---

# Operator Help

Anyone at the firm can email their Operator "how do we work together?" and get
the answer a good colleague would give: direct, specific to what this seat can
do for them, and short. This skill is that answer. The words come from a guide
SMD wrote (`references/guide.md`) and a set of SMD-authored tips
(`references/tips.yaml`); what this seat can do for this person comes from the
seat's own facts, read this turn. Nothing comes from memory.

## Who may invoke

Anyone on the firm's roster. It is read-only and spends nothing, so it is
**not** admin-gated: a new paralegal asking how to work with the Operator
deserves the answer as much as a partner does. What changes per person is
which requests are offered, and that is decided by the seat's facts, never by
you.

Person-invoked only. Never scheduled, never fired by a webhook. Reply to the
person who asked, and to nobody else.

## When this is the skill (and when it is not)

This skill answers questions about **how to ask**: "how do we work together?",
"how should I ask you for a demand?", "do I need to send you the documents?",
"should I prompt you like Claude?", "what can you do for me?".

- A question about how to ask **submits nothing**. "How do I get a demand on
  XXXXXX?" is this skill, and no demand is queued.
- An imperative request for work ("draft the demand on 12345") is **never**
  this skill. It is that work's own request.
- "Who are you", "what can you see" and "what will you do each day and week"
  are the introduction (`operator-introduce`), not this skill.

## Procedure

### 1. Read this seat's facts

Call `operator_seat_facts` with `depth: "help"`. Its `help` section lists the
requests this seat invites. For each one it gives `what`, `example`,
`admin_only` and `sender_may_ask` (already decided from the roster and the
firm's administrators; you never re-judge it). Its `voice` section says which
kinds of writing have a learned voice. Its `identity` and `connections`
sections say what the seat is and which systems it declares.

If the tool does not answer, or a section comes back `read: false`, you still
answer from the guide, but you offer **no** specific trained request and make
no claim about the voice. Never fill a gap from memory or from another seat.

If `unreadable[]` carries entries for the `help` section, some kinds of request
could not be checked this turn even though the section reads `read: true`. Say
briefly that you could not check every kind of request just now, and offer only
what the `help` section lists.

### 2. Read the guide and the tips

Read `references/guide.md` and `references/tips.yaml` with `read_file`. Try
`/opt/data/skills/operator-help/references/guide.md` first, then
`/app/skills/operator-help/references/guide.md`; the same two places, in the
same order, for `tips.yaml`. The guide's own "Notes for the Operator" section
binds you; never quote it or mention it.

If neither copy of the guide will read, answer from the shape below using only
what the seat facts say, keep it short, and do not improvise a guide.

### 3. Answer in this shape

Short by default. Go deeper only if they asked for more, or asked a specific
follow-up. Prose, no headings, no tables. Plain sentences.

1. **The direct answer to what they asked**, in one paragraph. If they asked
   whether to prompt it like Claude: yes, as specific as they like.
2. **How it works.** It already has the client file, so there is no uploading
   and no pointing it at documents. Give it the matter number and what you
   want. Name the file's home system only as the seat facts declare it.
3. **Trained work compared with one-off work.** For trained work, a one-line
   request is enough: list the requests from the `help` section whose
   `sender_may_ask` is true, each worded like its `example`, matter numbers as
   XXXXXX. List **only** those. If an `admin_only` request is marked
   `sender_may_ask: false`, you may say in one sentence that an Operator
   administrator at the firm can ask for it; never offer it to this sender.
   Then one-off work: write it the way you would for Claude, with one detailed
   example from the guide.
4. **The firm's voice and the attorney's own voice**, only as far as the seat
   facts and the guide's notes support it. A class the `voice` section reports
   `installed` is a voice it has learned; for anything else, say it writes in
   the firm's format. Always true: SMD can teach it an attorney's style from a
   couple of examples they're happy with.
5. **Keep the conversation going.** Every reply lands in the same thread: reply
   with changes and ask for a corrected version; a one-line answer to its
   question is all it needs; ask it questions about a file.
6. **When to train it.** The same kind of document again and again is worth
   training: send SMD a couple of examples they like.
7. **Close** by inviting questions, in one line.

Use a tip from `references/tips.yaml` when it fits what the person asked or the
trouble they describe (for example `held_identity_check` when they mention it
stopped to ask which document they meant). Use the tip's own words.

### 4. Reply

Compose the reply by creating a draft (`create_draft`) addressed **only** to
the sender, in the same thread, the way the Operator answers any colleague. The
reply channel delivers it. One reply, nobody copied.

## Never

- Never name a mode, a tool, a skill, a store, a queue, a lane, a job, a
  ledger, a gate or any other internal mechanic in the reply. "I'll build the
  chronology" is fine; "I'll submit a job to the medchron lane" is not.
- Never offer, describe or promise a request the `help` section does not list
  with `sender_may_ask: true` for this sender, and never describe a capability
  the seat facts do not support. The guide's examples are examples, not a
  roster.
- Never promise a time of any kind ("within the hour", "by tomorrow", "right
  away").
- Never use an em dash.
- Never put a real matter number, client name or party name into an example.
  Matter numbers stay XXXXXX.
- Never submit, queue or file work, and never change anything. The only thing
  this skill creates is its one reply.
- Never reply to anyone but the person who asked.

## Pitfalls

Offering every request in the guide because the guide lists it; offering an
administrator's request to someone who is not one; answering "how do I get a
demand?" by queuing a demand; explaining the Operator's machinery to make an
answer sound thorough; turning a one-paragraph question into a manual; and
claiming a learned voice the seat facts do not report.

## Verification

- The reply's offered requests are exactly the `help` entries with
  `sender_may_ask: true`, worded like their examples, with XXXXXX for matters.
- No request, job or file was created this turn.
- The reply names no internal mechanic, carries no em dash, and promises no
  time.
- The reply went only to the sender, in the thread.
