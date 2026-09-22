---
name: proposal-drafter
description: Drafts proposal from meeting transcript + SOW templates.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Marketing, Agency, NewBusiness, DraftForReview]
  smd:
    vertical: marketing-agency
    weight: heavy # ADR 0049 - synthesis from a transcript into a client-facing proposal; escalate to the seat's escalation model when one is authored
    action_class: read + internal_write
    connectors:
      - fireflies
      - fathom
      - granola
      - google_drive
      - dropbox
      - slack
---

# Proposal Drafter

Takes a discovery-call transcript, extracts the prospect's stated objectives + scope signals, drafts a proposal that maps those signals to the agency's authored service-line packages + pricing matrix. The owner reads, sets prices, and ships from their own inbox.

## When to Use

The discovery-to-proposal handoff is where agency owners lose deals. After a 45-minute discovery call, the next-day proposal is supposed to: (a) prove you listened, (b) shape an engagement that's the right size, (c) land in the prospect's hands before they cool. Owners writing proposals from memory after 3-5 calls a day produces uneven quality and 24-48h delays. Deals slip through.

This skill turns a transcript + the agency's library into a 70%-complete draft. Owner reads in 10 minutes, sets the actual prices (which the agent never invents), edits the scope-fit narrative, ships.

## Prerequisites

See frontmatter.

## How to Run

Triggered when a discovery-call transcript lands in the recording app (via webhook or daily polling):

```
hermes run proposal-drafter --transcript <recording-id>
```

Manual invocation for a specific call:

```
hermes run proposal-drafter --transcript fireflies://meeting-abc123
```

## Procedure

1. **Pull the transcript.** Read the full transcript from the recording app via its MCP/Composio connector. Also pull any meeting-recorder-generated summary/notes if present (these often surface decisions the agent should reflect in the draft).
2. **Extract scope signals.** Per `references/categorization-rubric.md`, identify:
   - Stated objectives (what the prospect said they want to achieve)
   - Pain points (what's broken now)
   - Constraints (budget hints, timeline mentions, team size, current vendors)
   - Decision-maker / approval pattern (named in the call)
   - Red flags (anything that suggests scope creep, mismatch, or unrealistic expectations)
3. **Map to service lines.** The agency's `pricing-matrix.md` (in Drive/Dropbox, path in `customer.yaml`) lists authored service lines with descriptions. Match prospect's stated objectives to relevant service lines. Note where multiple service lines could apply - surface the choice to the owner, don't pick.
4. **Assemble the draft.** Use the agency's SOW template (Drive path). Fill: prospect company, objectives quoted from transcript, service-line descriptions, deliverables per service line, timeline shape (no specific dates without owner authoring), and explicit price placeholders the owner fills.
5. **Surface conversation snippets.** Beneath each section, surface the verbatim transcript line(s) that justify it ("They said: 'We've stopped tracking conversion in Q2 because the dashboard was wrong.'"). Owner can verify the agent didn't hallucinate intent.
6. **Write to drafts.** `customer_notes/drafts/proposals/{prospect}/proposal-YYYY-MM-DD.md`. Slack thread post in `proposals-drafts` channel with prospect name + draft permalink + any red flags surfaced.

### Trust Ceiling

**draft_for_review** locked. The agent NEVER ships proposals - proposals are sales artifacts with money on them.

The agent MAY:

- Read transcripts, meeting notes, the pricing matrix, SOW templates
- Write to the drafts folder
- Post Slack alerts
- Reference verbatim prospect quotes to anchor each section

The agent MUST NOT:

- Quote a price. Every dollar figure is a placeholder the owner fills.
- Invent prospect intent. If the transcript is ambiguous, surface the ambiguity, don't resolve it.
- Send the draft to anyone outside the agency.
- Promise specific timeframes ("Our team will start October 15"). The owner authors timelines.
- Reference "case studies" or "client examples" the agent didn't see in the agency's authored materials.

### Voice Rules

- Use the prospect's words for their objectives. Don't paraphrase; quote.
- Lead with their objective, not the agency's services. "You want to ship paid social against ICP-B segments." Then service.
- Avoid agency-marketing voice ("Our award-winning team..."). Plainspoken, business-to-business.
- No em dashes. No "we're excited to partner." No "We're the right fit for you" - that's the prospect's call.
- Where the call surfaced something specific the agency has the agent rephrase neutrally without commitments ("You mentioned working with X - we can speak to that approach in a follow-up if it'd help.").

## Pitfalls

Common failure modes: inventing prices or timelines (forbidden), paraphrasing rather than quoting prospect language, picking a service-line match where the transcript supports two, and hiding rather than surfacing red flags (e.g., a $20K budget vs. $50K+ matched service lines).

## Verification

1. Every section's claim has a transcript quote underneath as audit trail.
2. Service-line matches are defensible from the transcript (not generic mappings).
3. Red-flag surfacing is real: if the prospect mentioned a $20K budget but the matched service lines list to $50K+, that's surfaced, not hidden.
4. Owner edits < 30% of the words on the first pass.
5. Zero prices in the draft. Zero invented timelines.

## References

- `references/voice.md` - proposal voice + prospect-quote anchoring
- `references/output-format.md` - SOW template structure; what gets a placeholder vs an extracted value
- `references/categorization-rubric.md` - scope-signal extraction + red-flag detection
- `references/test-cases.md` - synthetic discovery transcripts (10 prospects, varied complexity)

## Cost estimate (filled by grading)

- Typical tokens-in per proposal: ~25K (45-min transcript + pricing matrix + SOW template + agency materials)
- Typical tokens-out per proposal: ~4K (the draft + transcript anchors)
- Tool calls per proposal: ~8
- Typical cadence: 2-10 per week per agency

Monthly per customer at typical volume: <$10 in tokens, <$2 in tool calls.
