---
name: status-report-assembler
description: Weekly client status report from PM tools + analytics.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: []
  commands: []
metadata:
  hermes:
    tags: [Marketing, Agency, ClientReporting, DraftForReview]
  smd:
    vertical: marketing-agency
    action_class: read + internal_write
    connectors:
      - asana | clickup | monday
      - google_analytics
      - meta_ads | google_ads | linkedin_ads
      - slack
      - gmail
---

# Status Report Assembler

Reads the work the agency completed for each retainer client over the past week, pulls the metrics that prove it, drafts a client-facing status note. Output lands in the agency's internal "drafts" folder + Slack thread for the owner to review. The agent never sends to clients; the owner ships.

## When to Use

Friday-night status report assembly is the canonical agency-owner bottleneck. Each client wants weekly proof of value: what shipped, what moved, what's next. Pulling that proof from 4-6 tools per client, 10-30 clients, every week, is 4-8 hours of the owner's time. Most owners do it badly under time pressure - clients notice; retention erodes.

This skill reduces it to: owner reads 30 drafts on Friday morning, edits/approves each in 1-2 minutes, ships from their own inbox. Saves the weekend.

## Prerequisites

Requires a PM tool connector (Asana / ClickUp / Monday), Google Analytics, at least one paid-media connector if the client runs paid, plus Slack and Gmail. See frontmatter.

## How to Run

Weekly cadence (Fridays at 0700 PT) via cron-skill:

```
hermes run status-report-assembler
```

Single client on demand:

```
hermes run status-report-assembler --client "Acme Co"
```

Custom window:

```
hermes run status-report-assembler --window "last 14 days"
```

## Procedure

The skill runs in two phases. The mechanical per-client × per-connector fetch loop runs inside a single `execute_code` block - intermediate per-client / per-tool results never enter the conversation context (ADR 0021 Stream A). Per-client voice matching, anomaly surfacing, and draft assembly stay in the agent's reasoning loop where they belong.

### Phase 1 - Fetch (single `execute_code` block)

Invoke `execute_code` with a Python script that iterates the agency's active retainer roster and pulls every per-client metric stream into one structured payload. The script reads connector bindings from `customer.yaml` (PM tool, analytics, paid-media, CRM, Slack, Gmail) and uses the Hermes-exposed `terminal` to call each connector's CLI:

```python
import json
import shlex

WINDOW_DAYS = 7  # override per `--window` arg

def run(cmd: str) -> str:
    """Call into the Hermes-exposed terminal tool. Strips trailing whitespace."""
    return terminal(cmd).strip()

def safe_json(raw: str, fallback_id: str) -> dict:
    """Parse a connector response; on failure, record + continue rather than abort."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "parse_failed", "fallback_id": fallback_id, "raw_excerpt": raw[:200]}

# 1. Enumerate active retainer clients from customer.yaml.
roster_raw = run('customer_yaml.py clients list --status active --has-retainer')
clients = [line.strip() for line in roster_raw.splitlines() if line.strip()]

# 2. For each client, pull every connector relevant to that client's SOW.
client_payloads = []
for slug in clients:
    cfg_raw = run(f'customer_yaml.py clients get {shlex.quote(slug)} --format json')
    cfg = safe_json(cfg_raw, fallback_id=slug)
    connectors = cfg.get("connectors", {}) if isinstance(cfg, dict) else {}

    pm = safe_json(
        run(f'pm_connector.py activity --client {shlex.quote(slug)} --window {WINDOW_DAYS}d'),
        fallback_id=f"{slug}.pm",
    ) if connectors.get("pm") else None

    ga = safe_json(
        run(f'ga4_connector.py report --client {shlex.quote(slug)} --window {WINDOW_DAYS}d'),
        fallback_id=f"{slug}.ga",
    ) if connectors.get("analytics") else None

    paid = safe_json(
        run(f'paid_media.py report --client {shlex.quote(slug)} --window {WINDOW_DAYS}d'),
        fallback_id=f"{slug}.paid",
    ) if connectors.get("paid_media") else None

    crm = safe_json(
        run(f'crm_connector.py pipeline --client {shlex.quote(slug)} --window {WINDOW_DAYS}d'),
        fallback_id=f"{slug}.crm",
    ) if connectors.get("crm") else None

    prior_reports = safe_json(
        run(f'drafts_store.py prior --client {shlex.quote(slug)} --limit 5'),
        fallback_id=f"{slug}.prior",
    )

    client_payloads.append({
        "client_slug": slug,
        "config": cfg,
        "pm": pm,
        "analytics": ga,
        "paid_media": paid,
        "crm": crm,
        "prior_reports": prior_reports,
    })

# 3. Emit ONE JSON document. This is the only thing that enters context.
print(json.dumps({
    "window_days": WINDOW_DAYS,
    "client_count": len(client_payloads),
    "clients": client_payloads,
}, ensure_ascii=False))
```

Only the final `print()` output enters the conversation context - typically ~15-25k tokens per client for ~20 clients, instead of ~120 separate tool-call result blocks. Per-client connector parses and prior-report reads happen in the child process and stay there. A single client's connector failure is recorded as a `parse_failed` row inside the payload - the batch does not abort.

### Phase 2 - Reason (agent, in-context)

The agent reads the JSON returned by `execute_code` and, per the rules in `references/algorithm.md`, processes each client in turn:

1. **Categorize PM activity** into "What shipped" per the inclusion rule (PM status transitioned to a done state inside the window AND client-visible AND not internal-only). Group by epic/category. See `references/categorization-rubric.md`.
2. **Format metrics** for the "Results" section. Only SOW-tracked metrics with healthy data and a comparable prior window. WoW deltas where reasonable; degraded data flagged inline.
3. **Surface blockers** matching the rubric (waiting-on-client state with a specific next-step ask, open > 2 business days). Internal blockers do NOT enter the client-facing draft.
4. **Compile next-week priorities** from the agency's plan. Items get one of three provenance markers: confirmed, contingent-on-named-dependency, or `[TBD]`. Never invented.
5. **Pick the "one thing" ask** by source priority (pending decision > info request > relationship maintenance).
6. **Voice-match** against the client's prior shipped reports in the payload. ≥ 3 prior reports → match formality / paragraph density / salutation. 1-2 priors → match but flag in Slack alert. 0 priors → use agency default voice + flag for calibration.
7. **Detect anomalies** per the rubric (result drop ≥ 25% WoW, blocker ≥ 7 business days, shipped count 0, next-week count 0). Anomalies become `> NOTE:` lines above the affected section AND surface in the Slack alert.
8. **Write the per-client draft** to `customer_notes/drafts/{client_slug}/status-YYYY-MM-DD.md` using the client's preferred template if present, otherwise the default in `references/output-format.md`.
9. **Post one summary Slack thread** to the agency's `client-status-drafts` channel listing all drafts written and all flagged-for-review entries, per `references/output-format.md`.

Detailed per-section inclusion rules, anomaly thresholds, and voice-calibration logic live in `references/algorithm.md`. The reference is the source of truth for what "good status assembly" looks like; this procedure is the dispatch shape.

### Trust Ceiling

**draft_for_review** for all clients. No exceptions. Even if a client has explicitly said "the agent can send weekly," the SOW provision and the substrate enforce draft-only for external sends.

The agent MAY:

- Read PM, analytics, paid, CRM tools per the client's connector binding
- Write the draft to the internal drafts folder
- Post a Slack thread message in the internal channel
- Read prior shipped reports (for voice matching)

The agent MUST NOT:

- Send the draft to the client (gmail.send refused per invariant 2)
- Modify any client-facing data in the source tools
- Promise specific results in next-week-priorities (the owner authors goals)
- Hallucinate metrics - every number must be sourceable to a tool call

## Pitfalls

Common failures: hallucinated metrics (every number must trace to a tool call), invented next-week-priorities (use placeholders when owner hasn't authored goals), voice drift from prior shipped reports.

## Verification

A successful weekly run satisfies:

1. Every active client has a draft in `drafts/{client}/` within 30 min of run start.
2. Every metric in every draft is sourceable to a specific tool call (audit-trail in the run log).
3. Voice match: a sample human-graded against prior shipped reports - passes if the owner edits < 25% of the words.
4. Flagged anomalies (campaign disapprovals, traffic drops, blocker tickets that should be in next-week-priorities) are surfaced - the value-add over a dumb template.
5. No invented promises. If the agent doesn't have an evidence base for next-week-priorities, it leaves placeholders for the owner to fill, not made-up goals.

## References

- `references/algorithm.md` - detailed per-client / per-section reasoning rules preserved for graders (post-`execute_code` rewrite)
- `references/voice.md` - agency-to-client voice + client-specific tonal matching
- `references/output-format.md` - exact draft structure + metric inclusion rules
- `references/categorization-rubric.md` - what counts as a "blocker" vs "noise"; anomaly thresholds
- `references/test-cases.md` - synthetic client datasets (10 clients, varied verticals + tone)

## Cost estimate (filled by grading)

Post-`execute_code` rewrite (ADR 0021 Stream A). Per-connector intermediate
results no longer enter the conversation context; only the single Phase-1
JSON payload does.

- Typical tokens-in per run (20 clients): ~300K - one JSON document covering all clients' PM activity, GA4, paid-media, CRM, and prior reports.
- Typical tokens-out per run: ~60K (one ~3K draft × 20 clients + one summary Slack post).
- Hermes tool calls per run: 2 (one `execute_code` + one `Email.create_draft`-equivalent batch / file-write batch). The per-connector calls happen inside `execute_code` and don't count toward conversation context.
- Typical cadence: weekly × N clients.

Pre-rewrite the parent agent saw ~120 separate tool-call result blocks per
run (one `execute_code` block replaces those). Token reduction expected
≥ 50% vs. baseline; grading harness confirms.

For a 20-client agency: ~$5-8/month in tokens, ~$1/month in tool calls.
