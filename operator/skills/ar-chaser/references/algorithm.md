# AR Chaser - Per-Invoice Algorithm

Detailed prose procedure preserved for graders. The SKILL.md's `## Procedure`
section runs the per-invoice fetch as mediated connector reads (ss #1917 -
`execute_code` is unauthorable on customer seats holding gateway credentials)
and references this file for the cadence decisions, voice matching,
payment-promise detection, and relationship-health surfacing that constitute
the actual judgment work. This file is the source of truth for what "good AR
chasing" looks like.

## Inputs the agent assembles in Phase 1

The mediated fetch yields, per overdue invoice, the equivalent of this shape
(as tool-call results, not one document):

```
{
  "as_of": "<aging snapshot timestamp>",
  "overdue_count": <N>,
  "invoices": [
    {
      "invoice_id": "<qbo invoice id>",
      "client_slug": "<slug>",
      "invoice": { ...full QBO invoice row... },
      "payment_status": { ...QBO payment-status detail... },
      "prior_threads": [ ...last 5 Gmail threads with this client... ]
    },
    // OR - skipped because payment cleared between snapshot and run:
    {
      "invoice_id": "<id>",
      "client_slug": "<slug>",
      "skipped_reason": "paid_since_snapshot",
      "payment_status": { ...QBO confirming paid... }
    },
    ...
  ]
}
```

Any connector that returns invalid JSON appears in the payload as
`{"error": "parse_failed", "fallback_id": "...", "raw_excerpt": "..."}`
rather than aborting the batch. The agent treats `parse_failed` for QBO
payment-status as a HARD STOP for that invoice - it does NOT draft when
it cannot confirm the invoice is still unpaid. The flagged invoice
surfaces in the Slack summary as "could not verify payment status; owner
to check QBO manually."

## Pre-draft filters

Before scoring cadence, the agent applies two filters:

1. **Paid-since-snapshot skip.** Any payload entry with `skipped_reason:
paid_since_snapshot` is omitted from the day's drafts. These appear in
   the Slack summary as `"{client} - INV-{id}: skipped (paid since snapshot)"`
   so the owner can see the cross-check fired and saved them from sending
   a chase on a paid invoice.
2. **Payment-promise detection.** Scan `prior_threads` for recent (within
   the last 14 days) outbound mentions of payment from the client matching
   patterns like:
   - "payment is in process"
   - "we'll get to it next week"
   - "approved internally, going through AP"
   - "[specific date or week] for payment"

   If such a mention exists and the named date has not yet passed, the
   draft shape changes from "dunning sequence step" to "acknowledgment
   of the promise + offer to follow up after the named date." The agent
   does NOT just repeat the next cadence step over a fresh payment promise.

## Cadence stages

A days-overdue value drives the cadence stage. The thresholds are:

### 7-13 days overdue - gentle reminder

- Tone: gentle, blame-the-postal-system; assume good faith and process slowness.
- Draft includes: invoice number, amount, due date, days overdue, a polite "any chance you can take a look" ask, and an offer to resend the invoice if it never arrived.
- Voice: matches the client's existing thread tone (formal / business-casual / casual per their prior messages).
- DO NOT reference late fees, terms violation, or service implications at this stage. Doing so reads as escalation on a postal-delay-equivalent timeline.

### 14-29 days overdue - firmer; ask if they need anything

- Tone: firmer; assume there's friction on the AP side.
- Draft includes: invoice details, an explicit "is there anything you need from us to process this" offer (new format, fresh copy, different payee details, a call to clarify), and a soft mention of the original payment terms.
- Voice: still matches the client's tone - firmer doesn't mean adversarial.
- DO NOT mention legal action, collections, or service pause at this stage.

### 30-44 days overdue - direct; reference payment terms; offer call

- Tone: direct without being adversarial. The client is clearly past the negotiated terms.
- Draft includes: invoice details, explicit reference to the SOW's payment terms, an offer to schedule a call to discuss any issues blocking payment, and a clear statement that the agency would like to resolve before this affects services.
- Voice: matches client tone; "professional and clear" overrides "casual" if their prior tone was very casual.
- This is the LAST stage where the agent drafts an email autonomously. The 45+ stage is Slack-only.

### 45+ days overdue - ESCALATE; no draft

- The agent does NOT draft an email at this stage. The escalation needs human judgment.
- Slack `@<owner>` mention in `ar-drafts` with:
  - Client name + invoice ID + amount + days overdue
  - Cadence history (what drafts were sent at 7/14/30 days)
  - Prior-thread summary (any payment promises, any disputes, any silence)
  - Recommendation: "call the client" / "pause services per SOW" / "escalate to accounts team"
- Owner decides the next action; the agent does not pre-draft another templated message.

## Voice-matching

To match the client's existing tone, the agent reads `prior_threads` and
calibrates:

1. **Salutation.** Match the client's last outbound greeting style (first
   name / formal / none).
2. **Formality level.** Mirror sentence length and word choice from the
   most recent shipped agency message to this client.
3. **Sign-off.** Use the agency's standard AR sign-off unless `customer.yaml:
clients.{slug}.report_voice.signoff` overrides.
4. **Reference framing.** If prior threads use ticket / project / matter
   numbers, the draft references them. If prior threads use service names
   ("the Q3 campaign"), use those.

A draft that violates the voice rules (`references/voice.md`) is
downgraded - the agent surfaces it as `LOW` confidence in the Slack
summary and writes a one-line plan rather than attempting prose.

## Relationship-health signals

The agent flags anomalies in the Slack summary so the owner sees
relationship deterioration without having to ask:

- **Normally-prompt client suddenly late.** If the client's average
  days-to-pay is < 7 across the last 6 invoices and the current invoice is
  > 14 days late, flag as `relationship-health: payment cadence change`.
- **Multiple clients of same vendor stack late simultaneously.** If ≥ 3
  clients sharing a common AP provider / industry are simultaneously late,
  flag as `market-signal: cohort-wide cash flow strain` - owner may want
  to adjust terms for that segment.
- **Client gone dark across multiple drafts.** If the same invoice has
  cycled through 7/14/30-day drafts with zero client response in the
  thread, flag as `relationship-health: client unresponsive` regardless
  of whether escalation has hit 45 days.
- **Disputed amount.** If `prior_threads` contains language disputing the
  invoice amount or scope ("we never agreed to this charge", "this isn't
  what we discussed"), flag as `relationship-health: disputed; owner
must intervene before next draft`.

Flagged signals appear in the Slack summary but do NOT block draft
writing - the owner reads the flag, then decides whether to send the
draft as-is, edit, or pull the engagement.

## Per-invoice draft file layout

Drafts land in `customer_notes/drafts/ar/{client_slug}-{invoice_id}-{stage}.md`
where `stage` is one of `7day`, `14day`, `30day`, `45day-escalation`. The
45day-escalation file contains only the owner-facing Slack summary
content; there is no email draft body in that file.

Each draft file frontmatter records:

```yaml
client_slug: <slug>
invoice_id: <qbo id>
amount_usd: <numeric>
due_date: <YYYY-MM-DD>
days_overdue: <numeric>
cadence_stage: <7day | 14day | 30day | 45day-escalation>
payment_promise_detected: <bool>
voice_match_source: <gmail_thread_id | default>
flags: [<relationship-health-signals>]
```

## Summary Slack post (one per run)

After all per-invoice drafts are written, the agent posts ONE summary
thread to the agency's `ar-drafts` channel:

```
*AR drafts ready - {YYYY-MM-DD}*

Drafts written (7-44 day band):
- {Client Name} - INV-{id} - {amount} - {days} days overdue ({stage})
- ...

Escalations (45+ days; no draft):
- @{owner} {Client Name} - INV-{id} - {amount} - {days} days
  Cadence history: 7/14/30 drafts sent. Last client response: {date or "none"}.
  Recommendation: {call | pause | escalate}

Skipped (paid since snapshot):
- {Client Name} - INV-{id}

Relationship-health flags:
- {Client Name}: payment cadence change (avg 5 → current 22 days)
- {Client Name}: disputed amount - owner must intervene

Could not verify payment status (owner check QBO):
- {Client Name} - INV-{id}

_Run finished {ISO timestamp} · skill version {hash}_
```

The summary is the owner's morning trigger - they read the escalations
and disputed-amount flags first, then scan the 7-44 day drafts.

## Why mediated reads and not an `execute_code` fetch loop

An earlier revision collapsed the fetch loop into one `execute_code`
child process to keep per-invoice tool results out of the conversation
context. That path is dead on customer seats: the `code_execution` action
class is unauthorable wherever gateway-held credentials exist (the #1841
custody guard - executed code could read connector credentials from the
gateway env, bypassing tool classification), so the fetch was REFUSED
before it ran (ss #1917). The mediated reads cost context proportional to
the overdue count, and that is the accepted trade: a governed, classified,
auditable read per fact beats an ungoverned batch that never executes.

The payment-status cross-check is now N explicit tool calls, which makes
it LOOK expensive and therefore tempting to skip. It is not optional:
defending against the #1 AR pitfall (drafting a chase on an already-paid
invoice) is a correctness rule, not a cost tradeoff. If a book's overdue
count makes per-invoice reads untenable, raise the ss #1917 batch-fetch
design conversation - never reach for `execute_code`.

## What this algorithm is NOT

- **Not autonomous.** Trust ceiling stays `draft_for_review`. Drafts land
  in `customer_notes/drafts/ar/` and the owner ships from their own inbox.
  The agent never sends an email to a client.
- **Not adversarial.** Even at the 30-44 day stage the draft assumes
  good faith. References to legal action, late fees, or "collections"
  are categorically refused. The owner adds those if they decide to -
  it's a relationship call.
- **Not silently chasing paid invoices.** The per-invoice payment-status
  cross-check is mandatory before any draft. An invoice that paid
  between aging snapshot and run-time is skipped and surfaced in the
  Slack summary so the owner sees the save.
- **Not invented.** Every dollar amount, every invoice number, every
  days-overdue figure traces to a QBO row. Where data is missing
  (`parse_failed`), the invoice surfaces in the Slack summary as
  "could not verify" - never with invented numbers.
