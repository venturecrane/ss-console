# ADR 0080: Connector-outage alerting — generic call-site observation, deterministic paging

- **Status:** Accepted
- **Date:** 2026-07-25
- **Issue:** [#1990](https://github.com/venturecrane/ss-console/issues/1990)
- **Builds on:** ADR 0079 (work-liveness monitoring — doctrine and delivery loop), ADR 0078 (client-custody email), ADR 0020 (MCP-first connectors)

## Context

The A&P diligence response (Christa's Q9) commits: "Connection and process
failures alert us." ADR 0079 covers uptime and stuck processes; **broken
connections were its named accepted gap**. A Smokeball API outage or a dead
Graph token fails every tool call while every liveness signal stays green —
scheduler firing, heartbeats green, machine up. Before this ADR that failure
class surfaced only in the agent log.

Captain's scope ruling (recorded on #1990): the requirement is
**connector-generic** — any connector authored in `customer.yaml.connectors{}`
on any seat is covered by default, with zero per-vendor monitoring work when a
new connector lands. Smokeball and the ADR 0078 Graph mail channel are the two
kill-test instances, not the scope boundary.

## Decision

Observe connector health at the **call site**, ship it on the existing 60s
heartbeat, and evaluate it with the same deterministic, edge-triggered alerter
ADR 0079 built. No LLM anywhere in the path.

```
agent process: post_tool_call plugin ──ledger file (tmpfs)──▶ gate: connector_check
  ──60s heartbeat `connectors` map──▶ console ingest (fleet_status.connectors_json)
  ──▶ ss-fleet-alerts: connector_down:<server> / connector_check_error conditions
  ──▶ edge-triggered email to team@ / RECOVERED only on proven success
```

**Two observation seams, one ledger** (`shared/connector_ledger.py`, tmpfs,
atomic rename — the `mcp_result_store` two-process bridge shape):

1. **MCP tool path** (overlay#179): the `hermes-smd-connector-health` plugin
   observes every `post_tool_call` (status/error_type/error_message), resolves
   the server via Hermes' authoritative `tools.mcp_tool._mcp_tool_server_names`
   mapping, and records the outcome. There is deliberately **no prefix-parse
   fallback** — sanitized server names contain underscores, so parsing
   `mcp_{server}_{tool}` is ambiguous and a misparse would mint a phantom-key
   alert with no path to RECOVERED. Unmapped tools are not counted (undercount
   is the doctrinally safe failure); a broken mapping import flags the ledger
   (`mapping_ok=false`) so the dark window **pages** instead of the class dying
   silently.
2. **Channel transports that bypass the tool path** (overlay#181): the ADR 0078
   Graph mail channel (gate-side delta poller, plugin-side transports) flows
   through the single `MsGraphClient.request()` chokepoint, which records every
   outcome under `msgraph_mail` with conn-class computed **structurally from
   the real status code** — no message matching. Any future direct-transport
   channel must instrument its client the same way; this is the one per-channel
   obligation the generic design cannot remove.

**Failure semantics.** Per server: `consecutive_failures` (the only alert
input), the current run's `first_error_ts` (set on 0→1), last-ok/last-error
timestamps, conn-class evidence, truncated last message. A success resets the
count but **keeps the key** — the console resolves an alert only on a proven
success, never on key-absence.

**What pages** (Worker-side, on stored values only):

- `connector_down:<server>` opens on either path:
  - **Conn-class**: ≥3 consecutive failures (Hermes'-own-breaker parity) with
    connection-class evidence in the current run, run age ≥ 300s (a burst that
    self-heals inside the breaker's 60s cooldown never reaches an inbox).
    Conn-class evidence = pinned Hermes transport strings, anchored
    typed-client `-> HTTP <status>` formats, auth-mint markers
    (`shared/connector_signatures.py`, fixture-tested, ONE module — the
    pin-bump checklist's re-grep target). Hermes' derivative breaker string
    ("unreachable after N consecutive failures") is **excluded**: the breaker
    bumps on ANY error result, so three business errors manufacture it.
  - **Signature-free backstop**: ≥10 consecutive with zero successes and run
    age ≥ 900s pages regardless of what the errors look like — a vendor MCP
    whose auth death presents as business-text errors pages late rather than
    never. This is what makes paging genuinely connector-generic; signatures
    only make it faster.
- `connector_check_error` when the seat reports `connector_check_ok=0`: the
  seat's own check is broken (ledger unreadable, mapping gone) and nothing is
  being counted — which must page, not silently disable the class.
- Resolve **only** at `consecutive_failures == 0`. Counts 1-2 are ambiguous
  (failing again, not yet proven down) and push no state — pushing inactive
  would emit a false RECOVERED on the way INTO a new outage.

**ADR 0079 doctrine carried over, mechanically:** every age is stamped
**writer-side** on the seat (`run_age_seconds`), so the Worker never computes
against wall-clock — a frozen row from a dead seat can never self-activate a
connector page on top of its correct `heartbeat_red`. NULL map = whole-map
hold; absent server key = per-server hold (`getStaleHolds` surfaces both);
alert state marked only after the email actually sent.

**Storage:** `fleet_status.connectors_json` (TEXT) + `connector_check_ok`
(migration 0094), overwrite-including-NULL. Ingest parses three-tier:
structurally-invalid map → whole column NULL (trust nothing; everything
holds); invalid entry → dropped alone, valid siblings kept; fields inside a
kept entry never individually nulled (entries are atomic). The
`fleet_alert_state` CHECK is rebuilt to `IN (…, 'connector_check_error') OR
condition LIKE 'connector_down:%'` — the composite `(customer_slug,
condition)` PK gives one alert row per failing server.

## Amendment 2026-08-09 — auth-plane probes + credential-age horizon (ss#2148)

The idle-connector blind spot below stopped being acceptable on 2026-08-02:
the pilot's Smokeball refresh token expired unrotated at its 30-day lifetime
(vendor-confirmed in Smokeball's auth documentation) and every liveness
surface stayed green until the 14:00 UTC scheduled run failed client-facing
work. Detection-on-use means the page arrives after the damage. Two additions,
both keeping this ADR's deterministic-predicate discipline:

1. **Auth-plane probes are carved out of the synthetic-probe rejection.** The
   rejection's rationale — "a probe is a write path into vendor APIs" —
   targeted vendor _data_ APIs, and it stands for them. `auth_status` exercises
   the OAuth token mint at the vendor's _auth host_ and touches no data
   endpoint. The `connector-auth-check` skill (authored per seat in
   `customer.yaml` cron — visible, never ambient) calls it daily and retries
   twice on failure, so a dead credential crosses the 3-consecutive threshold
   and pages the same day it dies. **This probe is a DETECTOR ONLY. It is not
   a keepalive, and the earlier text here claiming otherwise was false.**

   RETRACTED 2026-09-02, after `pilot-smokeball`'s token died at its 30-day
   lifetime on 09-01 despite ~12 consecutive successful daily probes. The
   original text read: "where the vendor rotates refresh tokens on refresh
   (Smokeball does), the daily probe renews the credential and the idle-expiry
   death stops happening at all." Every clause of that is wrong, and the
   parenthetical carried no citation, no verify ID, and no observation — it
   hardened from a conditional in ADR 0054 that was justified by analogy to
   `clio-mcp`, a different vendor.

   What is actually true, verified against vendor documentation
   (vfy_01M1HBNGAQK0SGKSVKXYM7DYYB, vfy_01M1HBKA7DMVT108T6621WRG6P):
   - Smokeball auth is AWS Cognito, where a refresh token's expiry is FIXED
     from the sign-in that issued it. AWS: "By default, the refresh token
     expires 30 days after your application user signs into your user pool."
   - Rotation would not have helped either. AWS: "The new refresh token is
     valid for the remaining duration of the original refresh token." No
     refresh call, however frequent, can extend the deadline.
   - Smokeball's documented refresh response carries no `refresh_token` field,
     and AWS returns one only when rotation is enabled — so nothing rotates
     and the durable file's mtime never moves between consents.
   - The only things that reset the clock are a fresh consent, or moving to the
     Client Credentials grant, which issues no refresh token at all.

   We had recorded the disproof seven days before writing the claim:
   `vfy_01KZ1KG79CN44K2RV4W7EWJEQM` (2026-08-02) reads "token file unrotated
   since Jul 3 03:27 UTC (30-day-old)". An unrotated file across a full token
   lifetime IS the disproof; it was read as a statement about our own traffic
   and the vendor half went untested. The probe also surfaces the
   rotation-persist race:
   `auth_status` now reports `refresh_token_persisted`, and the skill treats
   `false` as a failure — a rotated token that failed its best-effort write to
   the durable file (silent today, bricks at next restart) pages the same day.

2. **Credential-age horizon, as the backstop for the probe itself.** The seat
   ships `connector_token_age` (the durable token file's mtime age; overlay
   `connector_check.token_ages()`) as a heartbeat field SEPARATE from the
   health map — synthesizing a `consecutive_failures: 0` entry would falsely
   resolve an open `connector_down` alert. The worker's
   `connector_token_expiring:<server>` condition opens at
   `lifetime − warn_days` (Smokeball: 180 − 5 for a token issued on or after
   the vendor's 2026-08-24 cutover, 30 − 5 for one issued before it) and
   resolves when the file is rewritten.

   CORRECTED 2026-09-02, same retraction as item 1 above: this paragraph used
   to say "in normal operation the keepalive rotates the file daily and this
   condition never fires; it fires when the probe infrastructure itself has
   been dead for ~25 days — the watcher's watcher". That is false for a vendor
   that does not rotate refresh tokens, which Smokeball is. Nothing rotates the
   file between consents, so this condition is not a backstop behind a working
   keepalive — **it is the ONLY warning before a silent expiry**, and it fired
   correctly on 2026-08-27 while every probe reported green. Treat a page from
   it as the last notice, not as evidence that some other watcher died.

   Lifetimes are recorded per server in the worker's vars
   (`SMOKEBALL_REFRESH_TOKEN_LIFETIME_DAYS`, now `180` — the CURRENT vendor
   lifetime); `src/token-expiry.ts` dates each token from its reported age and
   applies the older 30-day lifetime to pre-cutover tokens, because the vendor
   keyed the change to the issue date. A server with no recorded lifetime is
   never evaluated.

## Accepted gaps (named, not hidden)

- **Idle-connector blind spot.** ~~Passive call-site observation cannot see
  the outage of a connector with zero traffic~~ — closed for
  durable-credential connectors by the 2026-08-09 amendment above (probe +
  horizon). Still true for connectors with neither a probe cron nor a
  recorded lifetime; detection latency there equals natural call cadence.
  The original rejection of synthetic _data-plane_ probes stands: a probe is
  a write path into vendor APIs, the incident class this monitoring family
  exists to prevent.
- **Restart mid-outage** wipes the tmpfs ledger and re-counts from zero (the
  open alert HOLDS throughout; bounded re-detection latency). Tmpfs is chosen
  deliberately: stale pre-restart counts must not survive a boot.
- **Concurrent one-shot agent processes** can lose an increment to
  last-writer-wins on the ledger file — undercount only, never a false page.
- **While Hermes' breaker is open**, calls short-circuit without touching the
  vendor; ledger counts in that window count Hermes, not the vendor (harmless
  for the ≥3 predicate).
- **Creds-restored-but-idle** keeps the alert open until the next real call
  proves recovery — the email says so ("auto-resolves on the next successful
  call"). An unproven recovery is exactly what the no-false-RECOVERED clause
  exists to prevent.
- **A seat crash-looping before any tool call** leaves the ledger empty —
  that is heartbeat/scheduler monitoring's job (ADR 0079), not this system's.
- **AgentMail's plugin-side REST transports** (reply channel, confirm
  dispatch) bypass the tool path like Graph does and are NOT yet
  chokepoint-instrumented; agentmail coverage today is via its MCP tool calls
  only. Instrument `plugins/hermes-smd-reply`/`outbound_send` transports the
  Graph way if the reply channel becomes load-bearing for a client
  commitment.

## Pin-bump obligations

On every Hermes pin change: (1) re-grep the three transport strings named in
`shared/connector_signatures.py`; (2) the unit test asserting
`tools.mcp_tool._mcp_tool_server_names` exists fails loudly if the mapping
moves — the plugin then counts nothing and the ledger flags `mapping_ok=false`
(which pages) until fixed.

## Amends

ADR 0079's accepted-gaps list: the "job fires and fails every run" class is
now partially closed — the connector subclass (this ADR) pages; pure
business-logic job failures remain Sentry/runtime-summary territory.

## Verification

Overlay: 4 test modules (signatures fixtures, ledger semantics, check reader,
plugin handler) + heartbeat debounce additions + Graph chokepoint tests.
Console: worker unit tests (tri-state, NULL-hold, backstop, burst
suppression), real-D1 integration (`tests/connector-observability.test.ts`:
migration row-preservation + CHECK accept/reject, ingest three-tier, runOnce
open→hold→resolve lifecycle, stale-hold on key vanish), roster tests.
**Definition of done is the live kill test** (recorded on #1990): break the
Smokeball token on pilot-smokeball (hermes-uid config scramble, no root
mutation, checksum-verified restore) → ALERT email; restore + one successful
call → RECOVERED email; agentmail green throughout. Graph instance follows
the same shape on smd-staging when the channel is live on a seat.
