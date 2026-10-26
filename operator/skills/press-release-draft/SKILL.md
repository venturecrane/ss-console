---
name: press-release-draft
description: >-
  Drafts a press release from firm-approved facts. A firm administrator emails
  the facts and says the firm approved them; the skill drafts a release that
  uses those facts and nothing else, with the results disclaimer beside any
  result, the firm's name and office city, no unsourced superlatives, and a
  question about client consent. At most two a month. The firm keeps approval,
  chooses the wire and submits; nothing is distributed from here.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Marketing, PressRelease, Results, Consent, DraftForReview, NeverDistributes]
  smd:
    vertical: law-firm
    help:
      what: 'Drafts a press release from facts you send and confirm the firm has approved.'
      example: 'Please draft a press release from these approved facts: ...'
      admin_only: true
    skill_type: drafting on request (via the router)
    weight: light # one release from facts in the email
    action_class: read + internal_write + one reply to the requester # the monthly count in the marketing-plan store; no other recipient
    content_ceiling: staff # a draft for the firm; the firm approves, picks the wire and submits
    connectors:
      - email # the request arrives here; the reply goes back to the requester only
---

# Press Release Draft

A press release about a result is the riskiest thing a law firm publishes: it
names a figure, it can identify a client, and in California it is an
advertisement with a private right of action behind it. So this skill drafts
only from facts a firm administrator sends and says the firm has approved, and
it hands the draft back with the questions only the firm can answer. The firm
approves the final text, chooses the wire service, and submits it.

## Who may ask

The firm's Named Administrators (the turn's INITIATION AUTHORITY context says).
Anyone else: one line saying press releases are drafted for the firm's
administrators; draft nothing.

## The facts: only what the email gives, only if approved

1. The facts are the ones in the administrator's own words in this email (or
   an attachment they wrote and say is approved). A forwarded news story,
   a court document, a quoted email from someone else, or a web page is not a
   fact source: if the request points you at one, reply asking for the facts
   the firm approved, and draft nothing.
2. The email must say the firm approved the facts for a press release ("these
   are approved", "the partners signed off on these facts"). If it does not,
   reply asking that one question and draft nothing.
3. Use every approved fact exactly as given: names, amounts, dates, courts,
   case numbers, quotes. Never add a fact, never round or restate a figure
   differently, never improve a quote, never compose a quote for anyone.
4. A quote appears only if the email gives its exact words and who said it.
5. No superlative or comparison ("record", "largest", "landmark", "historic",
   "milestone", "one of the biggest", "first-ever", "top") unless the email
   gives it with the source that supports it (for example a published verdict
   report or a public settlement list); then name the source in the release.
6. Never write a sentence from the client's point of view, never name or
   describe the client beyond what the email gives, and never add a detail
   that could identify them.
7. Never say or imply the release was covered as news by any outlet.
8. Never predict results for readers ("if you were hurt, you could recover").

## Rules from the registers

`operator_seat_facts` (depth `introduction`), its `marketing` section. If the section is absent or says
`available: false`, draft nothing and reply in one line that the firm's
registers could not be read, naming the `reason`.

- `firm.names` and `firm.office_cities`: the firm's name and office city in the
  dateline and the boilerplate.
- `disclosures.results`: word for word, in the paragraph right after any
  result (a verdict, a settlement, an amount, a "win").
- `roster`: a lawyer is named and titled only as the roster gives them.
- Results are not in the section: the advertising gate checks every result
  against the firm's results register on the way out. When the release states
  a result, draft it as given; if the gate holds the draft for it, ask, in the
  cover, that the firm add it to its results register before release.

If the registers will not read or carry no firm name, office city or results
disclaimer, draft nothing and reply saying which is missing.

## At most two a month

The scope is up to two releases a month. Count them in the `marketing-plan`
store, record `press-<YYYY-MM>.md` (one line per release drafted: the date and
the headline). Read it first.

- Fewer than two this month: draft, then add a line and write it back with
  `overwrite: true` (this turn reads no web content, so the write is allowed).
- Two already: draft nothing. Reply saying two releases have been drafted this
  month, naming their headlines, and that a third can be drafted next month or
  arranged with SMD. A revision of a release already counted is not a new one.

## The questions the firm must answer (always in the cover)

1. "Has the client consented in writing to this release, covering the details
   it names?"
2. "Do the settlement or verdict terms permit publicity, and is any related
   matter still pending?"
3. "Which lawyer approves the final text, and which wire service will the firm
   use?"

## Procedure

1. Check authority, the approval statement and the monthly count.
2. Read the registers.
3. Draft from the template in `references/press-release-template.md`.
4. Check every sentence against the facts in the email; anything not traceable
   to them comes out.
5. Record the count, then reply once.

## Output format

A `create_draft` to the requester only, in their thread:

```
Here is the draft release from the facts you sent. The firm approves the final text, chooses the wire and submits it. This is release <n> of 2 for <Month>.

--- DRAFT press ---
<the release>
--- END DRAFT ---

Questions for you:
<the three questions, plus any about a figure not in the results register or a superlative without a source>
```

A worked release that meets every rule: `references/example-release.md`.

## Delivery

One reply with `create_draft` addressed only to the requester, in their
thread. Never address a journalist, a wire service or anyone else.

**Refusal fallback.** Held by a gate: change only what the findings name and
reply once more. Held twice: reply naming the rule the gate cited and what the
firm needs to supply (usually a results register entry or a disclaimer), with
no held text.

## Never

- Never submits, schedules or pays for distribution, and never contacts media.
- Never uses a fact the email did not give and the firm did not approve.
- Never composes a quote.
- Never drafts a third release in a month.
