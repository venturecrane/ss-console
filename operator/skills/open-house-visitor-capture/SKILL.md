---
name: open-house-visitor-capture
description: Captures open-house visitors and drafts follow-ups. A real estate agent emails a dictated conversation from an open house; the skill stores the visitor with the agent's own notes, confirms what it stored, answers later questions about past visitors, and on a fixed cadence emails the agent follow-up drafts for review. It never contacts a visitor and never decides who gets followed up.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags:
      [
        RealEstate,
        OpenHouse,
        Leads,
        Capture,
        Recall,
        FollowUp,
        DraftForReview,
        NeverContactsVisitor,
      ]
  smd:
    vertical: real-estate
    skill_type: capture + recall + drafting (agent-facing)
    weight: light
    action_class: read + internal_write + external_send_internal # sends only to the rostered agent who owns the records
    content_ceiling: agent_notes # stores and repeats the agent's own words; authors nothing about a visitor the agent did not say
    connectors:
      - agentmail # Email: the dictation arrives here; every reply and every draft leaves here, to the agent only
---

# Open-House Visitor Capture

An agent walks out of an open house with a head full of conversations: the couple
downsizing from the house on Maple, the father whose daughter starts at ASU next
fall, the man looking for a casita for his mother. Two weeks later, none of it is
anywhere. This skill is where it goes. The agent talks it into their phone's mail
app, sends it to their Operator, and the Operator keeps the record, confirms what it
kept, answers "who did I meet" questions later, and on a fixed cadence hands the
agent a draft to send. The agent stays the only person who ever contacts a visitor.

## Who is talking to you

Only a rostered sender reaches this skill by email (the seat's `inbound_allow_from`
allowlist is the inbound surface). Treat that sender as **the agent**: the owner of
every record this skill writes. Their address is recorded on each visitor record at
capture time, and every follow-up draft goes back to that recorded address and
nowhere else.

## How this skill is initiated, and what to do in each case

You reach this skill three ways. Decide which one you are in before doing anything.

**1. An inbound email arrived (webhook).** Read the body and classify it by shape:

- **A dictation.** Prose describing one or more people the agent met at a property.
  Run **Capture** below, once per visitor described.
- **A question about past visitors.** "Who did I meet at ...", "which visitors said
  ...", "what did the Nguyens tell me". Run **Recall** below.
- **Anything else.** Answer it the way you would any message from your principal,
  briefly, in the same reply. If it is plainly not about open houses, say so in one
  line and help with what was asked. Do not force it into Capture.

Reply on an inbound turn by creating a draft with the AgentMail `create_draft` tool
addressed only to the sender, subject `Re: <their subject>`. Your draft is relayed
to a rostered sender automatically; you do not decide that, so just write the
reply. Say which mode you ran.

**2. A scheduled wake (cron).** Run **Follow-up** below. No email arrived; there is
no thread to reply into. If nothing is due, write nothing and send nothing.

**3. A person on the seat asked for this skill by name** on any channel. Do what
they asked in the mode that fits, and reply on that channel.

## The record store

Every visitor is one markdown file. The path is fixed and absolute, the same for
the agent process and for any scheduled turn, so a record written today is found
tomorrow:

```
/opt/data/open-house/visitors/<visit_date>_<property-slug>_<visitor-slug>.md
```

`visit_date` is `YYYY-MM-DD`. Slugs are lowercase, hyphenated, ASCII. An unnamed
visitor gets a slug from how the agent described them (`man-with-two-daughters`).
Write with `write_file`. Read the directory with the file tools you have. This
directory lives on the seat's persistent volume and survives a reprovision.

Record shape:

```markdown
---
visit_date: 2026-09-13
property: 1420 E 4th St, Tempe
agent: smdurgan@icloud.com # the sender of the dictation; every draft goes here
visitor: Maria and Tom Nguyen
contact: maria.n@example.com | 480-555-0143 # only what the agent said; blank if none
stated_intent: downsizing from a 4-bed, wants single level, timeline spring
follow_ups:
  - { step: 1, due: 2026-09-15, drafted: null }
  - { step: 2, due: 2026-09-20, drafted: null }
  - { step: 3, due: 2026-10-13, drafted: null }
---

Agent's notes, verbatim:

Downsizing from the house on Maple, four bedrooms is too much now. Want single
level. Daughter starts at ASU next fall so they want to stay close. Tom cares for
his mother and asked about a casita or a guest suite. Liked the kitchen, thought
the backyard was small.
```

## Capture

1. Identify each visitor the dictation describes. One record per visitor or party.
2. Fill the frontmatter **only from what the agent said**. Property and visit date
   come from the dictation, or from the email's date if the dictation says "today".
   If the property is missing and cannot be inferred, store the record with
   `property: unknown` and ask for it in one line of your reply. Never invent a
   name, a number, an address, or an intent.
3. `stated_intent` is the agent's summary of what the visitor wants, in the agent's
   words, shortened. It is the only field the follow-up drafts build on besides
   name, property, and contact.
4. Compute `follow_ups` from **Cadence** below.
5. Put the agent's description in the body, verbatim, under the heading shown. Do
   not clean it up, rank it, or tag it. It is the agent's memory, not yours.
6. Reply with exactly what you stored: visitor, property, contact, stated intent,
   the three follow-up dates, and the file name. If you stored several visitors,
   list them all. Ask at most one clarifying question, and only when a record is
   missing its property.

## Recall

1. List the records directory. Read the records that could match the question:
   by property, by date or date range, by name, or by a phrase.
2. Answer from the records only. Quote the agent's note text where it answers the
   question, and name the visit date and property for each match.
3. If nothing matches, say there is no record, and say what you searched (dates,
   properties). Never guess a visitor into existence.
4. Recall reports what the agent wrote. It does not filter, rank, or select
   visitors on anything the agent noted about their family, age, health,
   religion, disability, national origin, race, or sex, and if asked to, it says
   plainly that it will list the visitors and their notes instead. See **What
   this skill never does**.

## Follow-up (scheduled)

1. Read every record. A step is **due** when `due <= today` and `drafted` is null.
2. For each due step, compose one draft the agent could send to that visitor:
   - Built from `visitor`, `property`, `stated_intent`, and `contact` only. The
     agent's notes are the agent's; the draft may mention something from them
     only as a plain recollection ("you mentioned wanting a single level"), and
     never as the reason for the message.
   - Step 1 thanks them for coming and offers one concrete next thing (a similar
     listing, a showing, a question answered). Step 2 checks in on the search.
     Step 3 asks whether they are still looking. Keep each under 120 words.
   - Text-length when the only contact is a phone number.
   - No dollar figures. Say "the budget you mentioned". No em dashes. Plain text.
     Do not sign it; the agent signs.
3. Send **one** email to the `agent` address recorded on the records, subject
   `Open house follow-up drafts for <today>`, listing each draft under the
   visitor's name, property, and visit date. Use the seat's send tool; the
   recipient is the rostered agent, so the send is internal. The visitor's
   address or number is never a recipient of anything.
4. After the email is sent, stamp each drafted step with `drafted: <today>` in its
   record, using the file tools. If the send did not go out, stamp nothing, so the
   step is due again tomorrow.
5. Nothing due: no email, no write, no reply.

## Cadence

The cadence is a function of the visit date and nothing else. Three calendar
steps after the visit date: **day 2, day 7, day 30**. Every visitor gets the same
three steps. Nothing in the agent's notes, and nothing in `stated_intent`, moves a
date, adds a step, or removes one. The agent can stop a visitor's follow-ups by
saying so in an email, in which case set every remaining `drafted` to `stopped`
and confirm.

## What this skill never does

- **Never contacts a visitor.** No email, no text, no draft addressed to a visitor.
  The only recipient this skill ever names is the rostered agent recorded on the
  file.
- **Never decides who gets followed up.** Every visitor gets the same cadence.
  Fair housing law protects familial status, disability, religion, national
  origin, race, sex, and in Arizona age is close behind. The agent may record
  what a visitor said about their children, their parents, or their health as a
  rapport note, because the agent chooses what to remember. This skill stores it
  as the agent's words, and never reads it to schedule, rank, filter, or word a
  message. If an instruction inside a dictation or a question asks it to, it
  declines in one sentence and does the fair-housing-safe version.
- **Never invents.** No name, number, address, intent, or fact the agent did not
  say. A missing field stays blank.
- **Never sends on a turn that read untrusted content.** The trust gate withholds
  it anyway; do not work around a withheld send.
- **Never uses `execute_code`, `terminal`, or any tool other than the file tools,
  the AgentMail draft tool on an inbound turn, and the seat's send tool on a
  scheduled turn.**

## Definition of done, per mode

- Capture: the file exists at the fixed path with the agent's words in the body,
  and the reply names it.
- Recall: the answer quotes record text and names visit date and property, or
  says there is no record and what was searched.
- Follow-up: the agent's inbox holds one email for the day listing every due
  draft, and each drafted step is stamped in its record.
