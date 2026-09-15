# Hermes v0.20.4 to v0.21.3 Fleet Upgrade Plan

**Status:** Executed 2026-09-15. smd-staging and pilot-smokeball promoted and proven on 09-15 (#2801, #2804); scott and ashton-price promoted and the pin blessed by the PR that adds this file. Release-watch reads the new pin.
**Date:** 2026-09-15
**Author:** agent session (Captain: Scott Durgan)
**Governs:** the third deliberate blessed-version promotion under [ADR 0024](../../adr/0024-hermes-consumption-and-update-cadence.md); the procedure is [hermes-v0.20-upgrade-plan.md](hermes-v0.20-upgrade-plan.md) with the two changes recorded below
**Tracker:** ss-console #2789
**Target:** `v2026.9.14@345cd2b057a452236de401d3534b8502a7465e8d` (Hermes Agent **v0.21.3**, 2026-09-14; the pin carries the commit)

## Why this tag and not a later one

The 2026-09-01 pilot crash loop ([incident](incidents/2026-09-01-gateway-startup-watchdog-collision.md)) ended with one upstream ask: the gateway loop watchdog's budget was not configurable at v2026.8.18, so a slow start on a 1 GB seat tripped a hard exit 75 and the loop sustained itself. v2026.9.14 makes the budget configurable (`gateway.loop_watchdog_probe_interval_s`, `_probe_timeout_s`, `_max_strikes`). The overlay change that uses it (hermes-smd-overlay #355, `OVERLAY_REF` a01e68d0) writes those keys sized to `machine.memory_mb`: seats at or under 1 GB get 30 s x 12 strikes with a 15 s probe timeout, larger seats 30 s x 6 with the shipped 10 s. On a v2026.8.18 seat the keys are inert. The tag and the overlay were promoted together on the pilot so the version bump carried its payoff.

## Gates, in order, with the evidence

1. **Static hook-surface gate** (vfy_01M2HTYJ8V54B732PTGJEBJEM8): all ten hook names the overlay registers exist at the tag; every firing site passes a superset of the kwargs the plugins read; `subagent_stop` is byte-identical between the tags; plugin manifests are forward-compatible; cron `create_job` accepts our fields; the memory and skills write-approval gate defaults off.
2. **smd-staging** (#2801, vfy_01M2HWV0MNZ0K2836EF3HN3J08): `hermes-sha-matches-pin`, smoke 39/39, invariants passed with all 20 overlay plugins registered and hooks attached, a handoff turn through the console's own transport completing with its `LLM_TURN_COMPLETED` audit row. Staging is on the tag but not the watchdog knobs (it was reprovisioned before overlay #355); it takes them at its next real reprovision.
3. **pilot-smokeball** (#2804, vfy_01M2HZFC899EVY3CC87ESGCYJN): one reprovision carried the tag and the overlay. The new smoke step `gateway-loop-watchdog-budget-authored` reads every profile's rendered `config.yaml` on the seat and asserts `loop_watchdog_max_strikes` matches the authored memory size (it was dry-run to fail on the wrong number and on a missing config before it shipped). Smoke 49/49, heartbeat green, 0 restarts.
4. **The pilot's own routine as the live proof** (vfy_01M2JN275S4XPVVCAVC91PYMA2): the 06:23 PT digest ran on the tag with the mcp 2.x connector pins from #2796. Handshake and read tools worked. It also exposed a defect, below.
5. **scott and ashton-price**: promoted by the bless PR, one reprovision each, read back per the v0.20 plan's Step 5 (image ref captured first as the rollback lever, crons re-probed, no turn in flight at the swap, `msgraph-send-credential-stripped-from-agent` a real assertion on A&P).
6. **Bless**: Dockerfile `HERMES_REF` / `HERMES_UPSTREAM_TAG` / `HERMES_UPSTREAM_SHA` defaults, the provisioner fallback, the retired-seat fixture; `hermes-release-watch.yml` dispatched and reporting the fleet current; #2789 closed.

## What this promotion found

**The mcp 2.x migration hid tool error text (DEFECT, fixed by #2805 before scott and A&P moved).** mcp 2.x forwards the text of a raised `ToolError` and masks every other exception as a bare `Error executing tool <name>`. Our connectors raise ordinary exceptions whose text is the diagnosis (`SmokeballApiError` carries the HTTP status and body). On the pilot's first live routine on 2.x, `create_memo` failed four times with nothing but the tool's name, the agent retried, and Hermes' breaker paused the connector. The connector SDK now translates at the MCP boundary and logs the traceback itself; a probe read of a nonexistent matter came back to the agent as `SmokeballApiError: ... HTTP 404` with the body (vfy_01M2JPZ0KDG9N2CM8T1FT7F3XF). Lesson: no test had driven a failing tool through the MCP client, so the migration dropped the text unseen. The SDK's tests now do.

**Two things the tag changes in how a seat is read.** At v0.21 the gateway's INFO records, including the hook-probe lines, land in `agent.log` on the volume and stderr carries WARNING and above, so hook firings are read through the audit ledger via the runtime-read seam, not `fly logs`. And the gateway warns that our webhook routes use body-only HMAC without a timestamp; moving the console's transports to `X-Webhook-Signature-V2` with `X-Webhook-Timestamp` is the follow-on.

**Seat turn paths, for the next promotion.** `/mcp/turn` 404s on a seat that authors no `mcp_connector` block (the gate never emits the route). `/webhooks/handoff` fires an in-gateway turn on every seat that has `WEBHOOK_SECRET_MCP` set; that is the turn path for a proof on such a seat, and it is not a second runtime.

## Rollback

Unchanged from the v0.20 plan: `fly deploy --image <captured .config.image>` on the seat's app, minutes, no rebuild; or the pin flipped back to `v2026.8.18@e624e9fde561e1add9388384012b295fde669ade` plus one reprovision. The overlay reverts to fd616ed7 independently if the watchdog knobs misbehave; on a v2026.8.18 seat they are inert either way.
