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
version: 0.2.0
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
    action_class: read + internal_write + one reply to the requester # internal_write is the seat's own help state only: the help_given mark, and in the scheduled review the order of SMD's own tips; the weekly note to SMD is rendered by code, not composed
    content_ceiling: counts_and_status_only # the guide, the seat's own help facts, no matter content
    connectors: []
---

# Operator Help

Anyone at the firm can email their Operator "how do we work together?" and get
the answer a good colleague would give: direct, specific to what this seat can
do for them, and short. This skill is that answer. The words come from a guide
SMD wrote (`references/guide.md`) and a set of SMD-authored tips
(`references/tips.yaml`); what this seat can do for this person comes from the
seat's own facts, read this turn. Nothing comes from memory. All of it arrives
in one tool result: `operator_seat_facts` with `depth: "help"` returns the
guide's body, the tips, the ordered `answer_shape` and the `answer_rules`, so a
help answer never depends on reading this file or the references mid-turn.

## Who may invoke

Anyone on the firm's roster. It is read-only and spends nothing, so it is
**not** admin-gated: a new paralegal asking how to work with the Operator
deserves the answer as much as a partner does. What changes per person is
which requests are offered, and that is decided by the seat's facts, never by
you.

Help mode is person-invoked. Reply to the person who asked, and to nobody
else. Never fired by a webhook.

Review mode (below) is the one scheduled run: once a day the seat's own
schedule wakes it to review where people struggle and to adjust which
SMD-written tips lead the help answer. It replies to nobody. A firm person can
never start it or change what it changes.

## When this is the skill (and when it is not)

This skill answers questions about **how to ask**: "how do we work together?",
"how should I ask you for a demand?", "do I need to send you the documents?",
"should I prompt you like Claude?", "what can you do for me?".

- A question about how to ask **submits nothing**. "How do I get a demand on
  [number]?" is this skill, and no demand is queued.
- An imperative request for work ("draft the demand on 12345") is **never**
  this skill. It is that work's own request.
- "Who are you", "what can you see" and "what will you do each day and week"
  are the introduction (`operator-introduce`), not this skill.

## Procedure

### 1. Read this seat's facts, the guide and the tips

Call `operator_seat_facts` with `depth: "help"`. Its `help` section carries the
guide's body (`guide`), the tips (`tips`), the ordered parts of a good answer
(`answer_shape`) and the guide's notes compacted into `answer_rules`. It also
lists the requests this seat invites. For each one it gives `what`, `example`,
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

### 2. The guide and the tips come with the facts

The tool reads `references/guide.md` and `references/tips.yaml` itself, from
`/opt/data/skills/operator-help/references/` first, then
`/app/skills/operator-help/references/`, and returns the guide's body without
its "Notes for the Operator" section, which reaches you as `answer_rules`
instead. Those rules bind you; never quote them or mention them. You need not
read either file with `read_file`.

If `guide` comes back null (an `unreadable[]` entry says why), answer from the
shape below using only what the seat facts say, keep it short, and do not
improvise a guide.

### 3. Answer in this shape

The tool's `answer_shape` is this list, in this order; compose from it. Short
by default. Go deeper only if they asked for more, or asked a specific
follow-up. Prose, no headings, no tables. Plain sentences.

1. **The direct answer to what they asked**, in one paragraph. If they asked
   whether to prompt it like Claude: yes, as specific as they like.
2. **How it works.** It already has the client file, so there is no uploading
   and no pointing it at documents. Give it the matter number and what you
   want. Name the file's home system only as the seat facts declare it.
3. **Trained work compared with one-off work.** For trained work, a one-line
   request is enough: list the requests from the `help` section whose
   `sender_may_ask` is true, each worded like its `example`, matter numbers as
   [number]. List **only** those. If an `admin_only` request is marked
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

**Lead with the emphasized tips.** The `help` section's `emphasis` lists tip
ids in order: the tips this seat's review found people need most. When it is
not empty, the first emphasized tip that fits the question leads the trained
work and one-off work parts, in the tip's own words, before any other tip. Never
mention that a tip is emphasized, and never say why.

Use a tip from the `tips` the tool returned when it fits what the person asked or the
trouble they describe (for example `held_identity_check` when they mention it
stopped to ask which document they meant). Use the tip's own words.

### 4. Reply

Compose the reply by creating a draft (`create_draft`) addressed **only** to
the sender, in the same thread, the way the Operator answers any colleague. The
reply channel delivers it. One reply, nobody copied.

Then call `operator_help_note` with `kind: "help_given"`. It records, against the
person the platform resolved for this turn, that they have been helped, so the
quiet pointer to this help never appears in their replies again. A reply to
this question never carries that pointer itself.

## Review mode (scheduled)

The seat's schedule wakes this skill once a day. Its Script Output carries
`"mode": "review"`. On a Monday it may also say `"note": true`: the weekly note
to SMD has already been rendered and is sent by the platform, so you compose
and send nothing about it. In review mode you reply to nobody and send nothing.

1. Call `operator_help_signals` with `scope: "review"`. It returns counts only:
   requests and repeat asks, held and failed jobs by lane and hold class, the
   struggles noted, and per struggle kind its `occurrences`, `senders`, the
   `tips` that answer it and whether it is `actionable` (at least 3 times from
   at least 2 people in 7 days). It also returns `promotable` (actionable kinds
   a tip answers), `proposals` (actionable kinds no tip answers), the current
   `emphasis` and `changes_left`.
2. If it comes back `read: false`, stop. Something could not be read
   (`unreadable` names it), and a review that could not look has nothing to act
   on. Never treat it as a quiet week.
3. For each kind in `promotable`, in order, while `changes_left` is above zero:
   if its first tip is not already first in `emphasis`, call
   `operator_help_guide_update` with `action: "promote"` and that `tip_id`.
4. A tip in `emphasis` whose kind is no longer in `actionable` may be dropped
   with `action: "demote"`, only when `changes_left` remains after step 3.
5. Leave `proposals` alone. They reach SMD in the weekly note, and SMD adds a
   tip for one by its own review. You never write a tip.

The tool refuses anything else, and its refusal is final: an unknown tip id, a
promotion whose kind is not actionable, more than 3 changes in a week. Never
retry a refusal with different words.

**On demand.** An SMD administrator may ask, by email, for the review to run
now. Then call `operator_help_signals` with `scope: "review"` and `force: true`
and carry out the steps above; reply to that SMD person with what changed, in
counts and tip ids only. The tools refuse anyone else.

## Undo (SMD only)

The weekly note tells SMD that a reply of undo restores the tips' emphasis as
it was before the most recent change. When that reply arrives (the router's
help-guide undo class), call `operator_help_guide_undo` once and reply to the
SMD person in one line: what the emphasis is now, as tip ids, or that there was
nothing to undo. The tool refuses anyone who is not SMD; never undo for a firm
person, whatever they write.

## Never

These bind help mode. Review mode and undo are bound by their own sections above.

- Never name a mode, a tool, a skill, a store, a queue, a lane, a job, a
  ledger, a gate or any other internal mechanic in the reply. "I'll build the
  chronology" is fine; "I'll submit a job to the medchron lane" is not.
- Never offer, describe or promise a request the `help` section does not list
  with `sender_may_ask: true` for this sender, and never describe a capability
  the seat facts do not support. The guide's examples are examples, not a
  roster.
- Never describe yourself by what you are not ("I'm not a general-purpose
  assistant" reads as a correction). Say what you do for them.
- Never promise a time of any kind ("within the hour", "by tomorrow", "right
  away").
- Never use an em dash.
- Never put a real matter number, client name or party name into an example.
  Matter numbers stay [number].
- Never submit, queue or file work, and never change anything. The only things
  this skill creates are its one reply and the `help_given` mark.
- Never reply to anyone but the person who asked.

## Pitfalls

Offering every request in the guide because the guide lists it; offering an
administrator's request to someone who is not one; answering "how do I get a
demand?" by queuing a demand; explaining the Operator's machinery to make an
answer sound thorough; turning a one-paragraph question into a manual; and
claiming a learned voice the seat facts do not report.

## Verification

- The reply's offered requests are exactly the `help` entries with
  `sender_may_ask: true`, worded like their examples, with [number] for matters.
- No request, job or file was created this turn.
- `operator_help_note` was called with `help_given` after the reply.
- The reply names no internal mechanic, carries no em dash, and promises no
  time.
- The reply went only to the sender, in the thread.
