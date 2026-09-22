# Categorization rubric - status-report-assembler

How the agent decides what's in vs. out of each section, and how to handle ambiguity.

## What goes in "What shipped"

A PM-tool item goes in the shipped section iff:

1. Status transitioned to a "done" state (Done, Closed, Released, Shipped - per the PM tool's mapping)
2. Transition timestamp falls within the report window (default: last 7 days)
3. The item is client-visible (has a label/tag/property marking it for this client, or lives in a client-specific project)
4. The item is NOT internal-only (excluded labels: `internal`, `team`, `meta`)

Items in flight do NOT go in shipped - even if "almost done." The line is the PM status change, not the agent's interpretation.

## What goes in "Results"

A metric is included iff:

1. The client's SOW specifies it as a tracked metric (default list per service: paid-media clients get CPL/CPA/CTR/spend; SEO clients get sessions/conversions/keyword rank deltas; etc.)
2. The metric has data for the report window AND a comparable prior window
3. The data source is healthy (no GA4 sampling warning, no Meta API error, no pixel-misfire indicator)

If data is degraded (sampling, missing days, API error), the metric is included with a flag: "Sessions: 12,400 (GA4 sampling at 50% - reduced precision)".

## What goes in "Blockers"

A blocker entry iff ALL of:

1. The agency's team has noted a waiting-on-client state (PM tool comment, internal Slack thread tagged `blocker`, or explicit "waiting on" status)
2. The waiting-on-client item has a clear next-step the client needs to take
3. The blocker has been open more than 2 business days (less than that, no action needed - surface only if owner asks)

If multiple blockers exist for the same client, list each as a separate bullet. Don't roll up.

## What goes in "Next week"

A next-week item iff:

1. The agency's project plan (PM tool, internal planning doc) lists it as a planned start in the next 7 days
2. The item is client-visible (per the shipped-rule definition)
3. The dependency status is known: either confirmed-start, contingent-on-named-dependency, or `[TBD]` placeholder

The agent NEVER invents next-week items from "the client's broader goals." If it's not in the plan, it's not in the report.

## "One thing" ask

The agent surfaces ONE ask per report. Source priority order:

1. **A confirmed pending decision the client owes** - e.g., budget approval, asset approval, copy review. These get priority because they unblock the agency's work.
2. **An information request needed to plan next sprint** - e.g., "Will the Q3 launch date confirmed for end of June still hold?"
3. **A relationship-maintenance question** - e.g., "Anything you'd like us to dig into for the Q3 review?" Used only when there's no pending decision or info request.

If multiple asks are pending, the agent picks the most-blocking and surfaces only that one. Owner can manually add others when editing.

## Voice-match calibration

To match a client's prior-thread voice, the agent:

1. Pulls the last 3-5 shipped reports the agency sent to this client (Gmail search by client domain + report subject pattern).
2. If ≥ 3 prior reports exist: voice-match against them (formality level, paragraph density, salutation style).
3. If 1-2 prior reports exist: voice-match but flag as "limited sample" in the Slack alert.
4. If 0 prior reports exist (new client): use the agency's default voice from `customer.yaml: agency.report_voice_default` and flag as "first report - calibration needed."

## Anomaly surfacing

The agent flags anomalies the owner should know before sending:

- **Result drop ≥ 25% WoW** on any tracked metric (without obvious explanation like holiday)
- **Blocker ≥ 7 business days open** on the same item
- **Shipped count = 0** for a client whose retainer should produce work this week (likely a scope or capacity issue)
- **Next-week count = 0** with no confirmed plan visible

Flagged anomalies appear in the Slack alert + as a `> NOTE:` line in the draft above the affected section, so the owner can decide whether to address in the report or out-of-band.

## Tie-breakers + edge cases

- **Multiple PM tools in use.** If a client's work spans Asana + Notion, the agent aggregates from both. PM-tool source attribution survives into the draft (linkable items go to their respective tools).
- **Missing prior-week comparison.** If WoW data isn't available (e.g., the agency just started tracking 4 days ago), use MoM or last-30-days as the comparable. Format clearly: "Sessions: 12,400 (last 30 days - no WoW comparison yet)".
- **Client has multiple stakeholders.** The draft is addressed to the primary contact per `customer.yaml: clients.{slug}.primary_contact`. CC list (if configured) is surfaced in the Slack alert; the owner adds CCs when sending.
- **Client engagement on hold.** If the engagement is paused per the SOW, the agent does NOT produce a status draft. It posts a one-line Slack note: "{Client} engagement paused per SOW; no draft."
