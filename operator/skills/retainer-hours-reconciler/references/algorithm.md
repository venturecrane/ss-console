# Retainer Hours Reconciler - Detailed Algorithm

Detailed prose procedure preserved for graders. The SKILL.md's `## Procedure`
section delegates the per-client × per-connector fetch loop to `execute_code`
(ADR 0021 Stream A), and the SKILL.md's `## When the agent wakes` section
delegates the wake decision to `pre_run.py` (ADR 0021 Stream B). This file
is the source of truth for the bucket thresholds, projected-EOM
extrapolation math, service-line threading detection, and the wake-decision
policy that constitute the actual judgment work.

## Wake decision (pre_run.py)

The `pre_run.py` script runs on every cron tick BEFORE the Hermes daemon
wakes the agent. It must emit either `{"wakeAgent": true}` or
`{"wakeAgent": false}` to stdout and exit 0. Decision rules:

### WAKE conditions (any one suffices)

1. **Critical-band breach.** Any client's projected EOM utilization is in
   the `OVER_CRITICAL` (≥ 110%), `OVER_WARNING` (95-110%), or
   `UNDER_CRITICAL` (< 40%) band. These are the bands that need owner
   attention before month-end - letting them sit past a cron tick risks
   month-end discovery of avoidable hours-eaten or churn signals.

2. **Period boundary (mandatory cadence).** The current cron tick is the
   weekly mandatory boundary (Monday morning). The weekly Slack report
   ships every Monday even when every client is `BALANCED`, because the
   absence-of-noise itself is a signal the owner has come to rely on.
   Skipping the Monday tick would create the same alarm shape as a
   silently-broken pre-run script.

3. **Previously-critical did not auto-promote.** A client that was
   `OVER_CRITICAL` or `UNDER_CRITICAL` in the last shipped report
   continues to wake the agent until the owner has acknowledged the
   transition (recorded in the audit log). The agent never silently
   promotes a previously-critical client to `BALANCED` - see
   `## Pitfalls` in SKILL.md.

### SUPPRESS conditions

The cron tick is NOT a period boundary AND every client is in `BALANCED`
or `UNDER_WARNING` AND no previously-critical client is still pending
acknowledgment. The agent does not wake; the dashboard's watcher-health
view records the `SUPPRESSED_WAKE` audit row instead.

### FALLBACK-to-wake conditions

The pre-run script ALWAYS falls back to `{"wakeAgent": true}` when:

- The audit-log writer cannot be constructed (env var missing, D1
  unreachable, namespacing fails).
- The audit write itself raises (timeout, write conflict, D1 error).
- The connector adapter is not yet shipped (the `harvest_connector.py`
  / `toggl_connector.py` / `float_connector.py` adapter binaries are
  absent from PATH).
- Any unhandled exception inside the polling or decision logic.

The principle: a silent suppress without a trail is structurally
indistinguishable from a silently-broken pre-run. Wake on uncertainty;
let the agent surface the failure.

## Inputs the agent receives from Phase 1

When the agent does wake, `execute_code` emits one JSON document with shape:

```
{
  "window": "mtd",
  "client_count": <N>,
  "clients": [
    {
      "client_slug": "<slug>",
      "config": { ...customer.yaml client block... },
      "time_entries": [ ...per-entry rows... ],
      "sow": { ...current SOW block with retainer_hours, service_caps... },
      "as_of": "<YYYY-MM-DD>"
    },
    ...
  ]
}
```

Any connector that returns invalid JSON appears in the payload as
`{"error": "parse_failed", "fallback_id": "...", "raw_excerpt": "..."}`
rather than aborting the batch. A `parse_failed` on time-entries OR SOW
makes the client unactionable - the Slack report surfaces them as
`"{client}: data unavailable - owner check {connector}"`. The agent does
NOT invent utilization figures.

## Bucket thresholds (post-rewrite, unchanged from rubric)

Projected end-of-month utilization, linear extrapolation from current
month-to-date pace, drives bucket assignment:

| Bucket           | Projected EOM | Meaning                                          |
| ---------------- | ------------- | ------------------------------------------------ |
| `OVER_CRITICAL`  | ≥ 110%        | agency will eat hours unless action taken        |
| `OVER_WARNING`   | 95-110%       | tight; need awareness; no auto-action            |
| `BALANCED`       | 65-95%        | tracking right; report ships but quiet           |
| `UNDER_WARNING`  | 40-65%        | underdelivered; client value at risk             |
| `UNDER_CRITICAL` | < 40%         | significant underdelivery; churn risk at renewal |

The 65-95% `BALANCED` band is intentionally wide. Agency retainers have
natural week-to-week variance; flagging at 100% misses real `OVER`
trajectories, flagging at 60% catches every short week as `UNDER`.
Calibration is in `references/categorization-rubric.md`; the wide band
prevents alarm fatigue. Owner-driven rubric calibration is the dial.

## Projected end-of-month extrapolation

Linear extrapolation from current month-to-date pace:

```
projected_eom_hours = actual_mtd_hours × (calendar_days_in_month / mtd_days_elapsed)
projected_eom_pct = projected_eom_hours / contracted_monthly_hours
```

The Slack report ALWAYS labels the projection explicitly: "Projected EOM
(linear extrapolation)" - never "EOM" alone. The owner must not mistake
the projection for actual hours-spent.

Per the rubric, a projection in early month (mtd_days_elapsed < 5) is
LOW confidence; the agent flags it as `"projection: low confidence -
fewer than 5 working days of data"` in the Slack post for any client
where this applies.

## Service-line threading detection

After per-client utilization is classified, the agent examines per-
service-line breakdowns within each client. The rubric:

A scope-misalignment flag fires when any service line is at ≥ 150% of
its per-service cap WHILE another service line of the same client is at
≤ 50% of its per-service cap. The pattern signals: the SOW's nominal
hours split doesn't match what the engagement actually needs.

The flag appears in the Slack post as `"@<owner> Scope misalignment:
{client} - {service-line-A} at {pct}% / {service-line-B} at {pct}%.
Consider SOW restructuring."` The agent does NOT modify the SOW; the
owner restructures.

## Anomaly surfacing (beyond bucket assignment)

Three additional anomalies surface in the Slack post regardless of
bucket assignment:

1. **Time-entry parse failure.** Connector returned invalid data → owner
   check connector; client utilization unknown.
2. **SOW missing or unparseable.** The SOW pointer in `customer.yaml`
   does not resolve to a readable SOW → owner check SOW store; client
   utilization unknown.
3. **Zero hours in the window for an active retainer client.** Could be
   data sync delay, vacation, or a real capacity gap. Surface but do
   not bucket; let the owner decide.

## Rubric calibration policy

If the same anomaly is surfaced three weeks in a row without owner action
in the thread, the rubric is producing noise. The pitfall list in
SKILL.md calls this out explicitly. Calibration steps (owner work, not
agent work):

1. Owner adjusts the threshold in `customer.yaml.retainer_thresholds.*`
   (the bucket boundaries or the service-line-threading multipliers).
2. The agent picks up the new thresholds on the next run; no code change.
3. If the noise persists after calibration, the rubric itself needs a
   revision PR.

The agent never auto-calibrates. Self-tuning thresholds without owner
acknowledgment would convert the skill from a useful signal into a
self-justifying one.

## Auto-promotion ban (mirror-don't-gate)

A client that was `OVER_CRITICAL` or `UNDER_CRITICAL` in the last shipped
report must NOT be silently promoted to a lower-severity bucket on the
next run. The transition is owner-acknowledged work:

1. The agent's report continues to flag the client as `[was-CRITICAL]`
   until the audit log records an `OWNER_ACK_CRITICAL_TRANSITION` row
   for that client + period.
2. The owner posts in the Slack thread; an out-of-band tool records the
   ack to the audit log.
3. The next run sees the ack and stops the `[was-CRITICAL]` annotation.

This prevents the silent-promotion failure mode the pitfall list calls
out. The pre-run script also reads this state - a previously-critical
client still pending ack is a WAKE condition regardless of current
bucket.

## Why combined A.2 + B.2

A.2 (`execute_code` for the fetch loop) and B.2 (`pre_run.py` for the
wake gate) both edit the same SKILL.md. A.2 rewrites `## Procedure` to
the two-phase pattern; B.2 adds the `## When the agent wakes` section
and the `pre_run.py` file alongside. Two parallel agents independently
editing the same SKILL.md frontmatter would pass CI in isolation and
collide silently at merge time - the worktree system prevents
filesystem races, not semantic ones. The ADR 0021 §"One-skill =
one-agent rule" critique safety constraint required consolidation.

The two streams also share the per-client time-entry payload: the pre-
run script computes utilization to decide whether to wake; when the
agent does wake, the same payload (re-pulled inside `execute_code` to
get the SOW joins the pre-run didn't need) drives the Slack report.

## What this algorithm is NOT

- **Not autonomous on SOWs.** The skill's `trust_ceiling: autonomous`
  applies only to read + internal Slack post. The agent never modifies
  a SOW, never messages clients, never edits time entries.
- **Not silently promoting critical clients.** The auto-promotion ban
  is structural enforcement - see above.
- **Not self-calibrating.** Threshold changes are owner work; the agent
  picks up new thresholds from `customer.yaml`.
- **Not skipping the Monday report.** The weekly mandatory cadence is
  a load-bearing signal - the owner reads "all clients balanced" as
  reassurance, not noise. Suppress conditions never apply to the Monday
  tick.
