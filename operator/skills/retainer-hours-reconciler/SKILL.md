---
name: retainer-hours-reconciler
description: Reconciles tracked hours vs SOW retainer caps for owner.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Marketing, Agency, RetainerOps, Slack]
  smd:
    vertical: marketing-agency
    action_class: read + internal_write
    connectors:
      - harvest
      - toggl
      - float
      - slack
---

# Retainer Hours Reconciler

Reads time entries from the agency's time-tracking tool, maps them against the SOW retainer caps for each client, and posts a weekly utilization report to a designated Slack channel. The agent is autonomous on the read + reporting flow; it does NOT message clients, change time entries, or modify SOWs.

## When to Use

Agency owners lose more money to retainer mismanagement than to bad sales. The two recurring failure modes:

- **Over-utilization** - a client is at 110% of their hours by week 3 of the month; the agency eats the difference. Often discovered only at month-end, after the work is already done at a loss.
- **Under-utilization** - a client is at 30% of their hours with one week left; the agency is providing less than the relationship is paid for; client churns at renewal "because we weren't getting value." The owner could have proactively scheduled work, but didn't see the gap.

This skill catches both before they cost money. Weekly Slack post, every Monday morning.

## Prerequisites

See frontmatter.

## How to Run

Weekly cadence (Mondays at 0800 PT) via Hermes' cron-skill mechanism:

```
hermes run retainer-hours-reconciler
```

One-off look at a specific client:

```
hermes run retainer-hours-reconciler --client "Acme Co"
```

Pull a different time window (default: month-to-date):

```
hermes run retainer-hours-reconciler --window "last 7 days"
```

## When the agent wakes

This skill is wired to a Hermes cron-skill schedule with a `pre_run.py` gate (ADR 0021 Stream B). The pre-run script polls each active retainer client's time-tracking connector and decides whether the agent needs to wake:

- **WAKE** if any client is in `OVER_CRITICAL`, `OVER_WARNING`, or `UNDER_CRITICAL` band - these are the buckets that need owner attention before month-end.
- **WAKE** unconditionally on the mandatory weekly cadence boundary (Monday morning) - the weekly Slack report ships even when all clients are `BALANCED`, because the absence-of-noise itself is a signal the owner has come to rely on.
- **SUPPRESS** otherwise. Before printing `{"wakeAgent": false}` the pre-run writes a `SUPPRESSED_WAKE` audit row capturing the polling inputs (hashed), the decision basis, and the next scheduled tick. A WAKE writes the sibling `EMITTED_WAKE` row on the same fields, best-effort - it can never suppress or delay the wake (#2253). The dashboard's watcher-health view greps the audit log for these rows: a scheduled tick with **neither** row is the alarm signal (mirror-don't-gate per ADR 0016).
- **FALLBACK** to wake on any audit-write failure. A silent suppress without a trail is structurally indistinguishable from a silently-broken pre-run script.

See `pre_run.py` alongside this SKILL.md for the wake decision logic; see `references/algorithm.md` for the detailed bucket thresholds and period-boundary policy.

**On wake - the wake line in the Script Output block is this turn's item list (#2253).** Hermes injects the pre-run's stdout verbatim into the woken prompt, so that line is not a flag, it is the handoff. Three shapes reach you, and the `decision_basis` tells them apart:

- **`client_in_critical_band`** - carries `plans`, one per firing client: `client_slug`, `kind: critical_band`, the `bucket`, the `projected_eom_pct` the gate computed, and `low_confidence`. Those clients are the finding set. State the figures the payload carries; do not re-derive your own, and carry `low_confidence: true` into the post as the "projected (linear extrapolation), few days elapsed" caveat.
- **`previously_critical_pending_ack`** - carries `plans` with `kind: pending_ack`: the slug only, with `bucket` / `projected_eom_pct` / `low_confidence` null. The gate returns on this branch before assigning buckets, so null means "no bucket computed this tick", never "this client is fine" - read the utilization yourself for those clients.
- **`weekly_mandatory_boundary`** - carries **no plans, BY DESIGN.** This is the Monday cadence wake: no individual client triggered it, so there is no per-item fact to hand over, and enumerating the full roster IS the job. Absent plans here are not blindness. The distinction is in the basis itself - every blind basis ends in `_fail_open`; this one does not.

When the line carries a **`*_fail_open` basis** (`no_audit_writer_fail_open`, `suppress_heartbeat_failed_fail_open`, `customer_slug_unset_fail_open`, `connectors_not_wired_fail_open`), or carries `plans_truncated: true`, the gate woke blind or partial: enumerate through the time tracker yourself and never treat a partial list as the complete one. Anything neither the payload nor a tool call this run produced surfaces as `"{client}: data unavailable - owner check {connector}"`, never as a plausible utilization figure (Phase 2 step 7).

## Procedure

The skill runs in two phases. The mechanical per-client × per-connector fetch loop runs inside a single `execute_code` block - intermediate time-entry and SOW reads never enter the conversation context (ADR 0021 Stream A). Utilization-bucket classification, service-line threading detection, and Slack-report assembly stay in the agent's reasoning loop where they belong.

### Phase 1 - Fetch (single `execute_code` block)

Invoke `execute_code` with a Python script that iterates the agency's active retainer roster and pulls every client's time entries + SOW caps into one structured payload. The script reads the time-tracker binding from `customer.yaml` (Harvest / Toggl / Float) and the SOW pointer (Notion / Drive / inline):

```python
import json
import shlex
from datetime import date

WINDOW = "mtd"  # override per `--window` arg (e.g., "last 7 days")

def run(cmd: str) -> str:
    """Call into the Hermes-exposed terminal tool. Strips trailing whitespace."""
    return terminal(cmd).strip()

def safe_json(raw: str, fallback_id: str) -> dict:
    """Parse a connector response; on failure, record + continue rather than abort."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "parse_failed", "fallback_id": fallback_id, "raw_excerpt": raw[:200]}

# 1. Enumerate active retainer clients.
roster_raw = run('customer_yaml.py clients list --status active --has-retainer')
clients = [line.strip() for line in roster_raw.splitlines() if line.strip()]

# 2. For each client, pull time entries + SOW caps.
client_payloads = []
for slug in clients:
    cfg_raw = run(f'customer_yaml.py clients get {shlex.quote(slug)} --format json')
    cfg = safe_json(cfg_raw, fallback_id=slug)
    tracker = cfg.get("time_tracker", "harvest") if isinstance(cfg, dict) else "harvest"

    entries = safe_json(
        run(
            f'time_tracker.py entries --tracker {shlex.quote(tracker)} '
            f'--client {shlex.quote(slug)} --window {shlex.quote(WINDOW)} --format json'
        ),
        fallback_id=f"{slug}.entries",
    )

    sow = safe_json(
        run(f'sow_store.py current --client {shlex.quote(slug)} --format json'),
        fallback_id=f"{slug}.sow",
    )

    client_payloads.append({
        "client_slug": slug,
        "config": cfg,
        "time_entries": entries,
        "sow": sow,
        "as_of": date.today().isoformat(),
    })

# 3. Emit ONE JSON document. This is the only thing that enters context.
print(json.dumps({
    "window": WINDOW,
    "client_count": len(client_payloads),
    "clients": client_payloads,
}, ensure_ascii=False))
```

Only the final `print()` output enters the conversation context - one JSON document covering every active retainer client. The per-client time-entry pulls and SOW reads happen in the child process and stay there. A single connector failure becomes a `parse_failed` row inside the payload - the batch does not abort.

### Phase 2 - Reason (agent, in-context)

The agent reads the JSON returned by `execute_code` and, per the rules in `references/algorithm.md`, processes each client:

1. **Sum time entries** by client and by service line (account-management, strategy, production, etc.) from the `time_entries[]` payload.
2. **Extract retainer caps** from `sow`: monthly retainer hours + per-service caps if specified.
3. **Compute utilization** as month-to-date `actual_hours / contracted_hours` percentage AND projected end-of-month percentage (linear extrapolation from current pace; label clearly as "projected" in the Slack post).
4. **Classify each client** into one of five buckets: `OVER_CRITICAL` (≥ 110%), `OVER_WARNING` (95-110%), `BALANCED` (65-95%), `UNDER_WARNING` (40-65%), `UNDER_CRITICAL` (< 40%). See `references/categorization-rubric.md` for bucket definitions; see `references/algorithm.md` for the bucket-decision algorithm including the "previously-critical did not auto-promote" rule.
5. **Surface service-line threading patterns.** If a single service line (e.g., strategy) is at 200% while production is at 30%, flag scope-misalignment - it suggests SOW restructuring even when the aggregate utilization looks fine.
6. **Post one Slack report** to the configured `retainer-ops` channel per `references/output-format.md`. Tag `@<owner>` if any client is `OVER_CRITICAL` or `UNDER_CRITICAL`.
7. **Honor `parse_failed` rows.** Any client with a parse failure on time-entries or SOW surfaces in the Slack report as `"{client}: data unavailable - owner check {connector}"` rather than a fabricated utilization figure.

Detailed bucket thresholds, projected-EOM extrapolation math, service-line threading heuristics, and the rubric calibration policy live in `references/algorithm.md`. The reference is the source of truth for what "good utilization reporting" looks like; this procedure is the dispatch shape.

### Trust Ceiling

Customer-zero and paying-customer ceiling: **autonomous** for read + internal Slack post.

The agent MAY:

- Read time entries from Harvest / Toggl / Float (read scope)
- Read SOW documents from the SOW store (read scope)
- Post the weekly report to the designated internal Slack channel
- Tag the owner (`@<owner>`) in the post if any client is `OVER_CRITICAL` or `UNDER_CRITICAL`

The agent MUST NOT:

- Modify time entries (edit, delete, reclassify)
- Modify the SOW
- Message clients directly (or even hint at messaging them)
- Post to channels other than the configured `retainer-ops` channel
- Promote any client's status to "OK" autonomously after a previous critical flag

If the owner replies in the Slack thread "Yes, increase Acme's cap to 80 hours," the agent does NOT modify the SOW - it logs the operator instruction and surfaces it in next week's report as a "pending SOW update." SOW changes are human work.

## Pitfalls

Common failure modes: mistaking projected EOM for actual hours, posting to a non-`retainer-ops` channel, repeating noisy alarms three weeks in a row without rubric calibration, and silently promoting a previously critical client to "OK."

## Verification

A successful weekly run satisfies:

1. Every active retainer client gets a utilization entry.
2. Service-line breakdown is present where the SOW specifies service caps.
3. EOM projection is clearly labeled "projected (linear extrapolation)" so the owner doesn't mistake it for actual.
4. Critical entries (over/under) have a one-line suggested action - specific, not generic.
5. Junk-quality alarms are absent: if the agent surfaces something that's "noise" three weeks in a row without the owner acting, the rubric (rubric.md) needs a calibration pass.

## References

- `references/algorithm.md` - detailed bucket thresholds, projected-EOM math, service-line threading detection, rubric calibration policy, and wake-decision policy (preserved for graders post-rewrite)
- `references/voice.md` - voice for the Slack post (tight, scannable, action-oriented)
- `references/output-format.md` - exact Slack message structure
- `references/categorization-rubric.md` - bucket definitions + thresholds
- `references/test-cases.md` - synthetic time-entry datasets for regression testing

## Cost estimate (filled by grading)

Post-`execute_code` + `pre_run.py` rewrite (ADR 0021 Streams A and B combined). The pre-run gate skips agent inference entirely on quiet weeks; when the agent does wake, the per-connector intermediate results no longer enter the conversation context.

- **Quiet-week cost (pre-run suppresses):** $0 in tokens, $0 in tool calls. One `SUPPRESSED_WAKE` audit row written.
- **Wake-week cost** (typical agency, ~10 clients): ~30K tokens in (one JSON document covering every client's time entries + SOW), ~2K tokens out (one Slack post). Hermes tool calls per run: 2 (one `execute_code` + one Slack post). Per-connector time-tracker and SOW reads happen inside `execute_code` and don't count toward conversation context.
- **Typical cadence:** weekly mandatory boundary fires every Monday morning; mid-week anomaly wakes fire only when a client crosses a critical band.

Pre-rewrite the agent ran every Monday + every cron tick regardless of state, with ~25 per-client tool calls each run. Post-rewrite the cost is concentrated on the weekly mandatory tick plus genuine anomalies. Token reduction expected ≥ 70% across a 30-day window vs. baseline; grading harness confirms.

Total marginal cost per month per customer at typical usage: <$0.50 in tokens, <$0.10 in tool calls.
