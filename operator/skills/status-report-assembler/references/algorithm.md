# Status Report Assembler - Per-Client Algorithm

Detailed prose procedure preserved for graders. The SKILL.md's `## Procedure`
section delegates the per-client × per-connector fetch loop to `execute_code`
(ADR 0021 Stream A) and references this file for the per-client reasoning
rules. This file is the source of truth for what "good status assembly" looks
like; the section-by-section rules, voice matching logic, and anomaly
surfacing have not changed from the pre-`execute_code` version of the skill.

## Inputs the agent receives from Phase 1

`execute_code` emits one JSON document with shape:

```
{
  "window_days": 7,
  "client_count": <N>,
  "clients": [
    {
      "client_slug": "<slug>",
      "config": { ...customer.yaml client block... },
      "pm": { ...PM-tool activity payload or null... },
      "analytics": { ...GA4 report payload or null... },
      "paid_media": { ...paid-platform payload or null... },
      "crm": { ...CRM pipeline payload or null... },
      "prior_reports": [ ...last N shipped reports... ]
    },
    ...
  ]
}
```

Any connector that returns invalid JSON appears in the payload as
`{"error": "parse_failed", "fallback_id": "<slug>.<connector>", "raw_excerpt": "..."}`
rather than aborting the batch. The agent treats `parse_failed` as a degraded
data source - the affected section in the draft renders a `[TBD: <connector>
returned error]` marker, not invented content.

## Per-section assembly rules

### "What shipped"

A PM-tool item enters the section iff all of:

1. The item's status transitioned to a "done" state (Done, Closed, Released,
   Shipped - per the PM tool's status mapping in `config.pm_status_map`)
2. The transition timestamp falls within the report window
3. The item is client-visible (has the client's label/tag/project membership)
4. The item is NOT internal-only (excluded labels: `internal`, `team`, `meta`)

Group by epic/category, not chronologically. Owner scanning wants to see
"what got moved" not a Tuesday-Wednesday-Thursday timeline. 2-6 bullets
typical; group if the team shipped >6 things. Past-tense, active voice.
Items in flight do NOT enter shipped - even if "almost done." The line is
the PM status change, not the agent's interpretation.

### "Results"

A metric is included iff all of:

1. The client's SOW lists it as a tracked metric (default per service:
   paid-media clients get CPL/CPA/CTR/spend; SEO clients get sessions /
   conversions / keyword rank deltas; CRM clients get pipeline movements)
2. The metric has data for the report window AND a comparable prior window
3. The data source is healthy (no GA4 sampling warning, no Meta API error,
   no pixel-misfire indicator)

Format: `Metric: value (delta % WoW) [source]`. Every metric carries source
attribution. If data is degraded (sampling, missing days, API error), the
metric is included with a flag: `"Sessions: 12,400 (GA4 sampling at 50% -
reduced precision)"`. If a metric dropped, surface honestly with one line of
context where the agent has evidence - never invent attribution.

### "Blockers"

A blocker enters the section iff all of:

1. The PM tool / Slack thread shows an explicit waiting-on-client state
2. The waiting item has a clear next-step the client needs to take
3. The blocker has been open more than 2 business days

Each blocker: artifact + waiting-since-date + next-step-ask. If multiple
blockers exist, list each as a separate bullet - do not roll up. If the
agency itself is the blocker (something the team owes), that does NOT enter
the client-facing draft; it goes in the internal Slack alert instead.
Omit the section entirely if there are no client-side blockers.

### "Next week"

A next-week item enters the section iff all of:

1. The agency's project plan lists it as a planned start in the next 7 days
2. The item is client-visible
3. The dependency status is known: confirmed-start, contingent-on-named-
   dependency, or `[TBD]` placeholder

3-5 items typical. Too many doesn't read; too few makes the client wonder
what they're paying for. The agent NEVER invents next-week items from "the
client's broader goals." If it's not in the plan, it's not in the report.

### "One thing" ask

The agent surfaces ONE ask per report. Source priority order:

1. **A confirmed pending decision the client owes** - budget approval, asset
   approval, copy review. These get priority because they unblock work.
2. **An information request needed to plan next sprint** - e.g., "Will the
   Q3 launch date confirmed for end of June still hold?"
3. **A relationship-maintenance question** - e.g., "Anything you'd like us
   to dig into for the Q3 review?" Used only when there's no pending
   decision or info request.

Multiple pending asks → agent picks the most-blocking and surfaces only
that one. Owner can manually add others when editing. Omit the section if
no ask this week.

## Voice-matching

To match a client's prior-thread voice, the agent reads `prior_reports[]`
in the payload (the last N shipped reports the agency sent to this client):

1. ≥ 3 prior reports → voice-match against them (formality level, paragraph
   density, salutation style)
2. 1-2 prior reports → voice-match but flag as "limited sample" in the
   Slack alert
3. 0 prior reports (new client) → use the agency's default voice from
   `config.report_voice_default` and flag as "first report - calibration
   needed"

Per-client voice overrides at `customer.yaml: clients.{slug}.report_voice`
take precedence over inferred voice. Override fields: `formality_level`
(`formal` | `business-casual` | `casual`), `salutation_pattern`
(`first-name` | `formal` | `none`), `signoff` (per-client requested
sign-off). The agent honors overrides over defaults, then voice-matches
against prior threads on top of that.

## Anomaly surfacing

The agent flags anomalies the owner should know before sending. Each
appears in TWO places: as a `> NOTE:` line in the draft above the affected
section, AND in the summary Slack alert at run end. Thresholds:

- **Result drop ≥ 25% WoW** on any tracked metric (without obvious
  explanation like holiday)
- **Blocker ≥ 7 business days open** on the same item
- **Shipped count = 0** for a client whose retainer should produce work
  this week (likely a scope or capacity issue)
- **Next-week count = 0** with no confirmed plan visible

A `> NOTE:` line lets the owner decide whether to address in the report or
out-of-band. The Slack alert guarantees the anomaly surfaces even if the
owner skims the draft.

## Tie-breakers and edge cases

- **Multiple PM tools in use.** If a client's work spans Asana + Notion,
  the agent aggregates from both. PM-tool source attribution survives into
  the draft (linkable items go to their respective tools).
- **Missing prior-week comparison.** If WoW data isn't available (e.g.,
  the agency just started tracking 4 days ago), use MoM or last-30-days
  as the comparable. Format clearly: `"Sessions: 12,400 (last 30 days -
no WoW comparison yet)"`.
- **Client has multiple stakeholders.** The draft is addressed to the
  primary contact per `config.primary_contact`. CC list (if configured)
  surfaces in the Slack alert; the owner adds CCs when sending.
- **Client engagement on hold.** If the engagement is paused per the SOW,
  the agent does NOT produce a status draft. It posts a one-line Slack
  note: `"{Client} engagement paused per SOW; no draft."`

## Summary Slack post (one per run, internal)

After all per-client drafts are written, the agent posts ONE summary
thread to the agency's `client-status-drafts` channel. The format
(per `references/output-format.md`):

```
*Status drafts ready - Week of {Mon DATE}*

Drafts written:
- {Client Name} - drafts/{client-slug}/status-{YYYY-MM-DD}.md ({word count})
- ...

Flagged for review:
- {Client Name}: voice-match uncertain - limited prior-thread history
- {Client Name}: result anomaly - sessions down 22% WoW, surfaced in draft

_Run finished {ISO timestamp} · skill version {hash}_
```

The Slack post is the owner's morning trigger - they read the summary,
open each flagged draft first, then the rest.

## Why `execute_code` and not the original 7 sequential steps

The original 7-step procedure (preserved in git history before this
rewrite) executed every per-client / per-connector call in the parent
agent's conversation context. For a 20-client agency with 6 connectors
the context bloat was ~120 separate tool-call result blocks before the
agent could write the first sentence of the first draft. Token cost was
high; per-message reasoning quality degraded as the context filled.

`execute_code` collapses the fetch loop into a single child process.
The parent receives ONE JSON document - typically 15-25k tokens covering
every client's PM activity, metrics, paid-media, CRM, and prior reports.
The agent then iterates the structured payload in its reasoning context,
producing one draft per client + one summary Slack post. Per-tool
intermediate results never enter the conversation; only the final
structured payload does.

## What this algorithm is NOT

- **Not autonomous.** Trust ceiling stays `draft_for_review`. Drafts land
  in `customer_notes/drafts/{client_slug}/` and the owner ships from their
  own inbox.
- **Not invented.** Every metric, every shipped item, every next-week
  priority traces to a connector payload row OR a `[TBD]` placeholder.
  Where the agency's plan has no record of a next-week priority, the
  agent leaves `[TBD]` for the owner - it does not infer goals from
  "the client's broader context."
- **Not voice-fabricating.** The agent voice-matches against prior shipped
  reports the agency itself sent. With < 3 priors, the Slack alert flags
  for calibration rather than silently extrapolating.
- **Not hiding internal misses.** Agency-side blockers stay out of the
  client-facing draft. The owner sees them in the Slack alert and decides
  how to surface (or not) in the actual reply.
