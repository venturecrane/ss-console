---
name: asset-collection-follower
description: Chases brand assets and access from new clients. Drafts the onboarding asset checklist and the follow-up chase emails.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Marketing, Agency, Onboarding, ClientHandoff]
  smd:
    vertical: marketing-agency
    action_class: read + internal_write + external_send (gated)
    connectors:
      - gmail
      - google_drive
      - slack
      - quickbooks
---

# Asset Collection Follower

When a new client signs the SOW (signal: first invoice issued in QBO + tagged "new-engagement"), drafts the brand-asset and platform-access checklist email. Tracks responses; drafts follow-ups at 48h, 96h, and 7d cadence. Logs progress to a per-client tracking note. Autonomous on read + log + cadence calculation; draft-for-review on every outbound message.

## When to Use

Brand-asset handoffs are the canonical agency-onboarding delay: clients are excited at signing, then disappear for 1-3 weeks while the agency waits for logos, brand guides, Google Ads MCC access, Meta Business access, brand fonts, CMS credentials. The agency cannot start the work it's already billing for. Weeks 1-2 of an engagement get burned on email pinging.

A consistent follow-up cadence (48h gentle ping → 96h second ping → 7d final + escalation) recovers most of those weeks. The owner just doesn't have the cycles to run the cadence manually across all new clients. This skill runs it.

## Prerequisites

Requires QuickBooks (signed-SOW signal), Gmail (drafts), Google Drive (checklist template), and Slack (escalations) connectors. See frontmatter.

## How to Run

Triggered by a QBO webhook on new-client invoice creation (specifically: invoice tagged with the agency's "new-engagement" custom field). Manual invocation for a specific client:

```
hermes run asset-collection-follower --client "Acme Co" --stage initial
hermes run asset-collection-follower --client "Acme Co" --stage 48h
hermes run asset-collection-follower --client "Acme Co" --stage 96h
hermes run asset-collection-follower --client "Acme Co" --stage 7d
```

## Procedure

1. **Initial checklist (T+0).** On signal of signed SOW, draft the asset-collection email per the agency's checklist template (Drive path in `customer.yaml`). The email itself is per-engagement-type (brand-only, paid-only, full-service) - agent picks the right template based on the engagement description in the QBO invoice/PM tool. Draft to owner.
2. **48h follow-up.** If checklist items not yet received (signal: items not present in agency's per-client asset folder OR client hasn't replied), draft a gentle reminder. Reference the specific items still missing. Draft to owner.
3. **96h follow-up.** If still missing, draft firmer ping - name the items, offer to set up a 15-min call to walk through it together (lowers the barrier).
4. **7d escalation.** If still missing at day 7, ESCALATE: Slack alert in `new-clients` channel, tag owner. The owner takes over - likely calls the client. No further drafts from the agent until owner says go.
5. **Internal logging.** Per-client tracking note at `customer_notes/onboarding/{client}.md` with the cadence so far + what items remain + any client responses. Autonomous.
6. **Stop trigger.** When all checklist items are received (signaled by agent or owner marking the per-client tracking note complete), the cadence stops.

### Trust Ceiling

**draft_for_review** for every external email. The agent never sends to a new client without owner review - the relationship is fragile in the first 7 days, and the cadence's tonal calibration is judgment work.

Internal logging + Slack posts are **autonomous** (these are agency-internal, no external blast radius).

The agent MAY:

- Read QBO invoices + PM tool engagement data (to determine checklist template)
- Read the agency's per-client asset folder (to verify what's missing)
- Read prior threads with this client (to match voice + see any responses agent missed)
- Draft outbound emails to the drafts folder
- Update the per-client tracking note autonomously
- Post Slack alerts to `new-clients`

The agent MUST NOT:

- Send any email to the client
- Modify the SOW or engagement scope
- Adjust the checklist mid-cadence (template is owner-controlled)
- Escalate before day 7 unless the client explicitly says "delay" or "concerns"
- Continue cadence after items received

### Voice Rules

- Warm but not gushy. The relationship is new; don't over-engineer enthusiasm.
- Itemized lists, not paragraphs. Clients scanning on phones need to see specifically what's needed.
- Offer the path of least resistance: a Drive folder for them to dump files into, or a Google Form, or whatever the agency authored.
- Never reference timeline ("starting Monday") without owner confirmation. The engagement clock is the owner's call.
- At 96h+: offer a short call. Some clients aren't email-shaped.

## Pitfalls

Common failures: pinging on items the client already sent (verify asset folder first), over-enthusiastic voice on the initial draft, continuing cadence after items received.

## Verification

1. Every new-client signal produces an initial-checklist draft within 1 hour.
2. Follow-up drafts surface exactly the missing items, not a generic ping.
3. 7d escalation is unmissable in Slack.
4. Voice consistent across the cadence - same tone day 1 and day 7.
5. Zero false positives: the agent doesn't follow up on items the client has actually sent.

## References

- `references/voice.md` - onboarding voice
- `references/output-format.md` - cadence stages, exact draft structure per stage
- `references/categorization-rubric.md` - engagement-type to checklist-template mapping; what "received" means per item
- `references/test-cases.md` - synthetic new-client signals (5 engagement types × 4 cadence stages = 20 fixtures)

## Cost estimate (filled by grading)

- Typical tokens-in per cadence step: ~4K
- Typical tokens-out per draft: ~600
- Tool calls per cadence step: ~6
- Typical cadence: ~4 steps per client × ~3-8 new clients/month

Monthly per customer at typical volume: <$2 in tokens, <$0.50 in tool calls.
