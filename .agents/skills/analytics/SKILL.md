---
name: analytics
description: Site Traffic Report
version: 1.0.0
scope: enterprise
owner: agent-team
status: stable
---

# /analytics - Site Traffic Report

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "analytics")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Pull traffic numbers from Cloudflare Web Analytics (RUM) across all Venture Crane sites. No arguments needed for a daily summary. Optional arguments for custom ranges.

## Usage

```
/analytics              # Today + 7-day trend
/analytics 30           # Last 30 days
/analytics 2026-02-01   # Specific date
```

## Arguments

Parse the argument:

- If empty, default to **today + 7-day trend**
- If a number (e.g., `30`), show the **last N days**
- If a date (e.g., `2026-02-01`), show that **specific date**

## Constants

These do not change:

- **Account ID:** `ab6cc9362f7e51ba9a610aec1fc3a833`
- **API Token env var:** `CLOUDFLARE_API_TOKEN`
- **GraphQL endpoint:** `https://api.cloudflare.com/client/v4/graphql`
- **Analytics type:** Account-level RUM (Web Analytics), NOT zone-level httpRequests

Zone-level analytics (`httpRequests1dGroups`) will fail with a permissions error. Always use account-level `rumPageloadEventsAdaptiveGroups`.

## Pre-flight

Check that `CLOUDFLARE_API_TOKEN` is set:

```bash
[ -z "$CLOUDFLARE_API_TOKEN" ] && echo "CLOUDFLARE_API_TOKEN not set" && exit 1
```

If not set, stop: "CLOUDFLARE_API_TOKEN is not in the environment. Launch with `crane vc` to inject secrets."

## Execution

### 1. Daily Trend (broken out by site)

Query `rumPageloadEventsAdaptiveGroups` at the account level for the date range, grouped by `requestHost` and `date`:

```bash
curl -s 'https://api.cloudflare.com/client/v4/graphql' \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "query": "{ viewer { accounts(filter: { accountTag: \"ab6cc9362f7e51ba9a610aec1fc3a833\" }) { rumPageloadEventsAdaptiveGroups(limit: 500, filter: { date_geq: \"START_DATE\", date_leq: \"END_DATE\" }, orderBy: [date_ASC]) { count dimensions { date requestHost } } } } }"
  }'
```

Replace `START_DATE` and `END_DATE` based on the parsed argument.

Group the results by `requestHost`. Each unique host gets its own trend section. If there is only one host, display it without the grouping header.

### 2. Top Pages, Referrers, Countries (for the most recent date in the range)

```bash
curl -s 'https://api.cloudflare.com/client/v4/graphql' \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{
    "query": "{ viewer { accounts(filter: { accountTag: \"ab6cc9362f7e51ba9a610aec1fc3a833\" }) { topPages: rumPageloadEventsAdaptiveGroups(limit: 15, filter: { date_geq: \"TARGET_DATE\", date_leq: \"TARGET_DATE\" }, orderBy: [count_DESC]) { count dimensions { requestHost requestPath } } topReferrers: rumPageloadEventsAdaptiveGroups(limit: 10, filter: { date_geq: \"TARGET_DATE\", date_leq: \"TARGET_DATE\" }, orderBy: [count_DESC]) { count dimensions { requestHost refererHost } } topCountries: rumPageloadEventsAdaptiveGroups(limit: 10, filter: { date_geq: \"TARGET_DATE\", date_leq: \"TARGET_DATE\" }, orderBy: [count_DESC]) { count dimensions { requestHost countryName } } } } }"
  }'
```

Run both queries in parallel (two Bash tool calls in one message).

Group results by `requestHost`. If only one host exists, omit the grouping header.

### 3. Format Output

**Single site** (omit site header when only one host):

```
== venturecrane.com Traffic ==

Pageloads (7-day trend):
  Feb 10:    2
  Feb 11:    9
  Feb 12:    0
  Feb 13:    4
  Feb 14:   74
  Feb 15:  230
  Feb 16:  150  <-- today

Top Pages (today):
  39  /
  23  /articles/multi-model-code-review/
  16  /articles/agent-context-management-system/
   9  /portfolio/
   ...

Referrers (today):
  99  venturecrane.com (internal)
  50  (direct)
   1  bing.com

Countries (today):
 145  US
   2  SG
   1  PL
   ...
```

**Multiple sites** (group by host with clear separation):

```
== Venture Crane Traffic ==

--- venturecrane.com ---

Pageloads (7-day trend):
  Feb 14:   74
  Feb 15:  230
  Feb 16:  150  <-- today

Top Pages (today):
  39  /
  23  /articles/multi-model-code-review/
   ...

--- kidexpenses.com ---

Pageloads (7-day trend):
  Feb 14:   12
  Feb 15:   18
  Feb 16:    9  <-- today

Top Pages (today):
   5  /
   3  /dashboard/
   ...

--- Totals ---

  Feb 14:   86
  Feb 15:  248
  Feb 16:  159  <-- today
```

Right-align the numbers for scannability. Flag days with zero traffic (missing from API response) as `0`.

When multiple sites exist, include a **Totals** section at the bottom summing all sites.

## Notes

- Read-only query. RUM data from JavaScript beacon (no bot traffic, but includes operator browsing).
- Days missing from API response = zero pageloads. Fill as `0` in trend output.
- Data may lag a few hours for current day. If API errors, show raw error message.
- Account-level query - new sites with the beacon appear automatically.
- Fleet machines block the beacon via /etc/hosts. Fleet traffic is not collected.
