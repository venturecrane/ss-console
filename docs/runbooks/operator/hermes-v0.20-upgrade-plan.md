# Hermes v0.18.0 → v0.20.4 Fleet Upgrade Plan

**Status:** 🟡 PR-1 executed 2026-08-20 — pilot-smokeball, scott and smd/Crane on v0.20.4 (PR #2453, main `6281d080`). **Step 1 (staging) NEVER RAN**: smd-staging dies at the msgraph two-app gate before any build (ss#2467), so the rollback rehearsal it carried never happened either — see the step for what replaced it. A&P is PR-2, on the Captain's explicit go; assigned 2026-08-20 to the ap-golive-christa session, where the promotion is the first move of a larger arc (promotion, then the Christa mail flip).
**Date:** 2026-08-19
**Author:** agent session (Captain: Scott Durgan)
**Governs:** the second deliberate blessed-version promotion under [ADR 0024](../../adr/0024-hermes-consumption-and-update-cadence.md); supersedes the procedure in [hermes-v0.18-upgrade-plan.md](hermes-v0.18-upgrade-plan.md) where the two differ
**Tracker:** ss-console #2444 (supersedes #2225)
**Target:** `v2026.8.18@e624e9fde561e1add9388384012b295fde669ade` (Hermes Agent **v0.20.4**, 2026-08-18; the tag is annotated — the pin carries the **commit**, not the tag object)

---

## TL;DR

- **Where we are:** all five live seats (hermes-smd-staging, hermes-pilot-smokeball, hermes-scott, hermes-smd/Crane, **hermes-ashton-price = production client seat**) run Hermes 0.18.0 pinned to `v2026.7.1` (`vfy_01M0DP0R1XTXG1SNMK4J5X2H0R`). Upstream is v0.20.4: 7 weeks and 8 releases (0.18.2, 0.19.0 "Quicksilver", 0.19.1, 0.20.0 "Herald", 0.20.1–0.20.4) of CVE pins, credential-scoping fixes, and state-integrity fixes we are not running.
- **The hook surface is compatible.** All 10 hook names the overlay binds are in `VALID_HOOKS` at v2026.8.18; `register_hook` still warns-but-stores; all 14 Hermes internals the overlay imports exist; tool-hook kwargs are a superset (`status`/`error_type` present); `transform_tool_result` still sees the raw result (`vfy_01M0DP33GT9KHSQ947P8DN29Y0`).
- **Two build breakers**, both fixed in this PR: (1) upstream now ships `.npmrc engine-strict=true` with `engines.node >=22.22.0` while the image used Debian 13 apt Node 20.19.2 (`vfy_01M0DP257XDYJ0Z04V1ZF57G2X`) → the image now copies Node 26 + npm from `node:26-bookworm-slim` exactly as upstream's own Dockerfile does at that tag; (2) `hermes plugins install` now security-scans the cloned plugin and our overlay's own security **test fixtures** trip a non-overridable `dangerous` verdict → the scan is disabled for that build step only (`plugins.scan_on_install: false` in the build-time root config), and the install is now pinned with `--ref`.
- **Four upstream defaults flip seat behaviour; all four are pinned to today's values** in the overlay's `translate.py` (hermes-smd-overlay#281, `OVERLAY_REF` = `eeeac283`): `approvals.mode` manual→smart, `agent.max_turns` 90→500, `tools.tool_search` "auto" (now activates on ANY MCP tool and hides them behind bridge tools the trust plugin would refuse), `delegation.max_concurrent_children` 3→10. `display.show_reasoning` is written as explicit posture (the gateway already resolves it False). Two things first read as flips are **not**: `approvals.timeout` (gateway wait is 300s at both tags) and `display.show_reasoning` on any gateway platform.
- **One ceiling could not be preserved:** `delegation.max_iterations` 50→250. Hermes' config migration 36 rewrites an explicit 50 to 250 at every boot, so 250 is accepted and recorded here. `delegate_task` is not on the webhook (email) lane's toolsets, so the A&P lane is unaffected.
- **Procedure:** overlay pins first (merged) → this PR builds the image and promotes the four SMD-own seats from the PR branch (staging → pilot → scott → Crane), each with boot-smoke plus a new `hermes-sha-matches-pin` check → Captain checkpoint → PR-2 promotes A&P, blesses the pin, closes #2444/#2225.

---

## 1. Current state

| Fact                            | Value                                                                                     | Source                                                                                         |
| ------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| Fleet pin (all 6 customer.yaml) | `v2026.7.1@7c1a029553d87c43ecff8a3821336bc95872213b` = v0.18.0                            | `operator/customers/*/customer.yaml`; Dockerfile ARGs `:40/43/49`; `provision-customer.sh:269` |
| Running on all 5 Machines       | Hermes 0.18.0 (`/opt/hermes/pyproject.toml`)                                              | `vfy_01M0DP0R1XTXG1SNMK4J5X2H0R`                                                               |
| Latest upstream stable          | `v2026.8.18` = v0.20.4 (2026-08-18), commit `e624e9fd…`                                   | `gh api repos/NousResearch/hermes-agent/releases`, `.../commits/v2026.8.18`                    |
| Overlay pin                     | `0088352a` → `eeeac283` (overlay#281, this PR)                                            | `operator/templates/Dockerfile` `ARG OVERLAY_REF`, `operator/contracts/overlay-pairs.json`     |
| Seat Node                       | v20.19.2 / npm 9.2.0 (Debian 13 apt)                                                      | `fly ssh console -a hermes-smd-staging`                                                        |
| Release-watch                   | paging weekly since 2026-07-13 (#1837, #2021, #2193 closed superseded; #2225, #2444 open) | `.github/workflows/hermes-release-watch.yml`                                                   |

---

## 2. What the gap buys us (only what bears on SMD)

1. **Security train.** CVE dependency pins refreshed (cryptography, starlette, python-multipart; anthropic SDK pin for CVE-2026-34450/34452; aiohttp 3.14.3 floor), DNS-pinned SSRF-safe fetches, strict redaction at compaction boundaries, webhook body-size caps on every aiohttp server, timestamp-bound webhook signatures, Telegram token redaction, tier-3 credential reads scoped, the Anthropic request-local client fix (a watchdog could corrupt SQLite), four session-state integrity fixes.
2. **Always-on reliability.** Durable delivery-obligation ledger, session activity heartbeats + stall watchdog, cron scheduler self-heal and stale-claim reconciliation, MCP schema cache, prompt caching for tool schemas on native Anthropic (our July forensics measured tool schemas at 70% of a first call's 40k tokens).
3. **Opportunities, filed not built:** signed outbound webhooks (a push path from seat to console; today audit rows are pulled), `pre_verify` hook, subagent lifecycle API, `approvals.deny` globs, and Hermes' tiered tool disclosure once the bridge tools are mapped in the trust plugin.

---

## 3. The defaults-diff step (new at this promotion; repeat at every pin bump)

The v0.18 runbook checked the hook surface. This promotion adds a second mandatory check: **diff `hermes_cli/config_defaults.py` (and `hermes_cli/config.py` DEFAULT_CONFIG at the old tag) and ask, for every changed key, "do we author it?"** The seats author almost nothing (`translate.py` writes model, delegation, mcp_servers, web, platforms, telegram, agent.disabled_toolsets), so every upstream default flip lands on every seat unmodified.

| Key                                  | v2026.7.1                                                                                              | v2026.8.18                                                                                       | Reaches the gateway?                                                                                                                                                   | Decision                                                                                                                                                |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `approvals.mode`                     | `manual`                                                                                               | `smart`                                                                                          | yes (`tools/approval.py:3181` reads merged config)                                                                                                                     | **pinned `manual`** — a dangerous command on an unattended turn stays denied instead of LLM-approved                                                    |
| `agent.max_turns`                    | 90                                                                                                     | 500                                                                                              | yes (`gateway/run.py:1979-2022` bridge)                                                                                                                                | **pinned 90** — cost ceiling                                                                                                                            |
| `tools.tool_search.enabled`          | "auto" = fires only when MCP schemas > 10% of context (never fired on our seats: 0 activations on A&P) | "auto" = fires on ANY MCP tool; hides MCP tools behind `tool_search`/`tool_describe`/`tool_call` | yes (`tools/tool_search.py:12-17`, `config_defaults.py:2639-2651`)                                                                                                     | **pinned `off`** — the trust plugin has no class for the bridge names and refuses unknown tools; the eager-schema state is what the seats are proven on |
| `delegation.max_concurrent_children` | 3                                                                                                      | 10                                                                                               | yes                                                                                                                                                                    | **pinned 3** — fan-out cost ceiling                                                                                                                     |
| `delegation.max_iterations`          | 50                                                                                                     | 250                                                                                              | yes                                                                                                                                                                    | **accepted 250** — migration 36 rewrites an explicit 50 to 250 at every boot (`config_migrations.py:757-786`)                                           |
| `approvals.timeout`                  | CLI wait 60; gateway wait `approvals.gateway_timeout` default 300                                      | `approvals.timeout` 300 is the gateway wait                                                      | n/a                                                                                                                                                                    | **not pinned** — same 300s gateway wait at both tags; pinning 60 would tighten it                                                                       |
| `display.show_reasoning`             | False                                                                                                  | True                                                                                             | **no** — gateway reads raw profile config with `default=False` (`gateway/run.py:9335-9341`); `display_config.py` defaults False per platform; the flip is CLI/TUI-only | written `False` as explicit posture only                                                                                                                |
| `plugins.scan_on_install`            | (absent)                                                                                               | True                                                                                             | build step                                                                                                                                                             | **off for the build-time install only** (see §4)                                                                                                        |

**Correction on the record:** the session's first evaluation told the Captain `show_reasoning` would prepend a Reasoning block to Telegram replies. The source says otherwise (above). No client-visible change was ever in play from that key.

---

## 4. Build changes (this PR)

- **Node 26 source stage** (`FROM node:26-bookworm-slim@sha256:9e6f9357…`), `nodejs npm` dropped from apt, node + npm copied into the final stage with npm/npx symlinks — the same shape and digest upstream's Dockerfile uses at v2026.8.18. `engines.npm <11.10.0 || >=11.17.0` is satisfied by construction (upstream builds its published image from this digest under engine-strict). Node stays in the runtime image because `@oktopeak/clio-mcp` is a Node stdio MCP server.
- **clio-mcp install** runs from a neutral cwd and the build asserts the bin is on PATH and the package landed under the Node 26 global prefix (presence checks only — it is a stdio server and would block on stdin).
- **Plugin install**: `--ref "${OVERLAY_REF}"` (pinned at last) and `plugins.scan_on_install: false` written into the build-time root `$HERMES_HOME/config.yaml` before the install (the install merges its `plugins.enabled` record into the same file, as before). At runtime `/opt/data` is the volume mount and shadows that file on every persisted seat; on a fresh volume it rides along and also covers `bootstrap.sh`'s rare runtime-install fallback, which would otherwise hit the same scan. Any future overlay change that adds injection-shaped test fixtures keeps tripping the scan; the opt-out is deliberate and documented in the Dockerfile.
- **`OVERLAY_REF` → `eeeac283`** in the Dockerfile ARG, `operator/contracts/overlay-pairs.json` (translate.py is not a tracked twin; every `overlaySha256` unchanged), and `tests/operator-dockerfile.test.ts`.
- **Global Hermes ARGs (`Dockerfile:40/43/49`) and `provision-customer.sh:269` stay at v2026.7.1 in this PR.** Bless happens in PR-2 after A&P; until then the release-watch keeps reading v2026.7.1 by design.

## 5. Boot-smoke hardening: `hermes-sha-matches-pin`

Every existing boot-smoke check passes identically on 0.18 and 0.20, so boot-smoke could not tell a promoted seat from one a peer quietly rebuilt at the old pin from `origin/main`. The new step reads `hermes_ref` from the invoking checkout's `operator/customers/<slug>/customer.yaml` (the same file the provisioner rendered the build from) and asserts it equals `/opt/hermes/HERMES_SHA` on the Machine. It is the one check whose failure means "the wrong Hermes is running".

## 6. Execution plan — staged rollout with gates

Reachability contract (the `/wired` output for #2444): **A&P's principal emails the Operator mailbox and receives the same verified reply she gets today; the Captain reads Hermes 0.20.4 on all five seats, the policy defaults that upstream flipped still behave as they do today, and the release-watch stops paging.** Terminal seam: the running process on hermes-ashton-price plus the blessed Dockerfile tag the watcher reads.

Ordering constraints that shape the steps: the provisioner builds from the invoking checkout and refuses uncommitted changes, a HEAD behind `origin/main`, or an `OVERLAY_REF` that disagrees with `overlay-pairs.json` — so the four SMD-own seats are reprovisioned **from this PR's branch** (ahead of main is allowed), and the PR merges the same day the fourth seat is green. Until it merges, nobody reprovisions staging/pilot/scott/smd from `origin/main` (that would silently rebuild a seat at 0.18 with no error).

**Step 0 — baseline.** Capture hook-probe `kwargs_seen` per hook on pilot-smokeball (v0.18) from `/opt/data/profiles/<slug>/logs/agent.log` + `gateway.log`; if rotated, the 07-07 run's recorded set is the baseline.

**Step 1 — staging. NOT RUN — the seat cannot be built.** `operator/bin/reprovision-staging.sh` exits before the build: `provision-customer.sh`'s two-app msgraph gate refuses smd-staging because `MSGRAPH_SEND_CLIENT_ID__SMD_STAGING` / `_SECRET__SMD_STAGING` are absent from the vault (ss#2467, `vfy_01M0DWJMC0RNS9ZYR3Y907AJVY`). **pilot-smokeball served as the proving seat instead**, at higher fidelity: real connector credentials rather than a sandbox. Every observation below was made there (Step 2). Kept as written so the intended staging procedure survives for whoever unblocks ss#2467.

The original text: `operator/bin/reprovision-staging.sh` (isolated creds; boot-smoke automatic, including the new sha check). Then on the seat, as the hermes uid with the profile:
`setpriv --reuid=hermes --regid=hermes --init-groups /opt/hermes/.venv/bin/hermes -p <slug> config get <key>` for `approvals.mode`, `agent.max_turns`, `tools.tool_search.enabled`, `delegation.max_concurrent_children`, `display.show_reasoning` (without `-p` it reads the bare default profile; without setpriv it touches root-owned files in the profile home); the gateway log line `Agent budget: max_iterations=90 (agent.max_turns from config.yaml, …)`; no `tool_search activated` line after the boot self-check turn; `node --version` ≥ 22.22 and `ldd /usr/local/bin/node` clean; `command -v clio-mcp` → `/usr/local/bin/clio-mcp`; `hermes plugins list` shows the overlay and the heartbeat `version` = `eeeac283`; the gateway log `MCP: N tool(s) from M server(s)` (the mcp 2.0 client ↔ 1.x `_reference` connector handshake); hook-probe `kwargs_seen` ⊇ Step 0.
**Rollback rehearsal — NEVER PERFORMED.** It was planned here and could not run: staging never built (above). Nobody has proven that `v2026.7.1` still builds on the Node-26 Dockerfile, so the pin-flip rollback is UNTESTED and must not be quoted as a measured lever.

What replaced it, unplanned: the **image-ref rollback was exercised for real** on pilot-smokeball at 2026-08-20T00:2x UTC, when the `.python-version` defect crash-looped the workspace broker and the seat went down. `fly machine update <id> --image <previous .config.image> -a hermes-pilot-smokeball -y` followed by `fly machine start` restored it (`broker-up`, `gateway:200`). It was not stopwatched: minutes, not tens of minutes, and no rebuild. **Capture `.config.image` before every reprovision** — that is the lever that has actually been used.

Measured reprovision windows (start → boot-smoke green), all from the same branch, three seats:

| Seat            | Start     | Green     | Elapsed |
| --------------- | --------- | --------- | ------- |
| pilot-smokeball | 01:24:08Z | 01:36:42Z | 12m34s  |
| scott           | 02:38:49Z | 02:51:10Z | 12m21s  |
| smd/Crane       | 02:52:19Z | 03:05:15Z | 12m56s  |

Inside that window the Machine reaches `started` ≈11.5 min in (the remote build dominates) and boot-smoke takes ≈63 s. The old Machine serves throughout the build, so the seat is down only for the tail. The "~18 minute" figure in `provision-customer.sh` describes the 2026-06-11 secrets-ordering incident, not a reprovision.

**Step 2 — pilot-smokeball (the seat this promotion was actually proven on).** `seat-readiness.py` first; then `for _ in $(seq 200); do echo s; done | operator/bin/reprovision.sh pilot-smokeball` — **never `yes s |`**: an unexpected prompt ate an unbounded feed on 2026-08-20 and the log reached 50 GB before anyone noticed; boot-smoke green; then `rehearse-card.py pilot-smokeball --as <authored admin> --only N` for a card command that calls Smokeball, and assert post-upgrade audit rows: `TOOL_CALL_COMPLETED` for an `mcp_smokeball_*` tool (mcp 2.0 ↔ 1.x with real creds), the inbound/`WEBHOOK_ROUTED` row for the same turn (`pre_gateway_dispatch` fired), a usage row (`post_api_request`), the voice plugin's line for the reply (`transform_llm_output`). `subagent_stop` is not exercised and is reported as source-compat only.

**A boot-smoke check that passes vacuously.** `msgraph-send-credential-stripped-from-agent` asserts the send credential is absent from the agent env. On pilot, scott and smd — none of which author msgraph — that is trivially true, so its PASS on those seats measured nothing. **hermes-ashton-price is the first seat where it is a real assertion on v0.20.4.** A failure there is a finding, not flakiness. Same shape as Law 12: a check that cannot fail has measured nothing.

**Step 3 — scott.** Reprovision; boot-smoke; the Captain sends one Telegram message and gets a reply (liveness of a Telegram seat on 0.20.4).

**Step 4 — smd/Crane.** Reprovision; boot-smoke; one real turn on its channel.

Immediately before the checkpoint report: re-probe `/opt/hermes/HERMES_SHA` on all four seats. Merge this PR.

**— CHECKPOINT —** Report the four seats, the hook evidence per hook, the MCP proof, the pins observed live, and the measured rollback. **Stop. Wait for the Captain's explicit go for A&P.**

**Step 5 — A&P (PR-2, on go).** At a quiet hour the Captain names: re-probe that crons are still off; capture the running image ref (`fly machines list -a hermes-ashton-price --json` → `.config.image`) as the minutes-not-rebuild rollback; note the msgraph cursor file mtime (`/opt/data/profiles/<slug>/msgraph/delta-state.json`). The swap is an in-place Machine replace after the remote build finishes (no `[deploy] strategy`, Fly default 5s grace); the old 0.18 Machine keeps polling every 45s during the build, and a turn in flight at the swap is killed — because the poller marks a message seen on the adapter's 2xx accept, that reply would be lost with no retry. So confirm no turn is in flight right before the swap; this is a standing property of every A&P reprovision, recorded here for the first time. A build failure leaves A&P untouched on 0.18; a post-deploy boot failure leaves it down on 0.20 and the rollback is the captured image ref. **After** boot-smoke + sha green, the Captain sends the same round-trip email proven on 2026-08-19 (#2436/#2437) from the permitted sender: exactly one inbound row, one verified reply in Sent Items, no duplicate. Cursor resume is proven by the cursor file's post-boot mtime plus zero duplicate inbound rows across the quiet period.

**Step 6 — bless (PR-2).** A&P + pilot-law pins, fixture, `provision-customer.sh` default, Dockerfile ARGs (`:43` is what the watcher reads), `first-boot.md:89`, placeholder examples. `gh workflow run hermes-release-watch.yml` → "Fleet is current with upstream (v2026.8.18). No action." Close #2444 and #2225.

## 7. Rollback

Two levers, both exercised before A&P: (1) **image ref** — `fly deploy --image <previous .config.image>` on that app, minutes, no rebuild (captured per seat before each reprovision); (2) **pin flip** back to `v2026.7.1@7c1a0295…` + reprovision (`uv.lock` + `uv sync --frozen` reproducible; proven to build on the Node-26 Dockerfile by the staging rehearsal). Machine secrets + volume persist under both. If the overlay pins misbehave, `OVERLAY_REF` reverts to `0088352a` independently.

## 8. Verification / done criteria (each runtime row = a `crane_verify` id in the PR's AC table)

1. Image builds at v2026.8.18 on Fly's remote builder; `node --version` ≥ 22.22; `clio-mcp` on PATH.
2. Boot-smoke green incl. `hermes-sha-matches-pin` on every promoted seat; `/opt/hermes/pyproject.toml` reads 0.20.4; re-probed before the checkpoint.
3. Hook evidence: hook-probe `kwargs_seen` ⊇ baseline (6 hooks); audit rows for `pre_gateway_dispatch`, `post_api_request`, `transform_llm_output` on the pilot rehearsal turn; `subagent_stop` source-compat only.
4. MCP: `MCP: N tool(s) from M server(s)` at boot + a real `mcp_smokeball_*` `TOOL_CALL_COMPLETED` on pilot.
5. Pins observed live (`hermes -p <slug> config get` ×5 as hermes uid; `max_iterations=90` in the gateway log; no `tool_search activated`).
6. Rollback rehearsed on staging, both directions timed; A&P image ref captured before its reprovision.
7. A&P round-trip after boot-smoke green: one inbound row, one verified reply, no duplicate.
8. Release-watch `workflow_dispatch` reports current; #2444 and #2225 closed.

## 9. Follow-ons (filed, not built)

Operator approvals posture on v0.20 (`smart`, `max_turns`, `approvals.deny`, `delegation.max_iterations` — Captain decisions); tiered tool disclosure once the bridge tools are mapped; the plugin-install scan vs our test fixtures; outbound webhooks / `pre_verify` / subagent lifecycle API; the 11 overlay plugin docstrings still pinned `v2026.5.16`; boot-smoke hardening beyond the sha check (hook-registration count, overlay SHA == `OVERLAY_REF`); upstream replaces Debian 13's libsqlite3 3.46.1 (WAL-reset corruption bug) and we do not yet.

---

## References

- [ADR 0024 — Hermes Consumption and Update Cadence](../../adr/0024-hermes-consumption-and-update-cadence.md)
- [hermes-v0.18-upgrade-plan.md](hermes-v0.18-upgrade-plan.md) (the first promotion; its §3 hook-surface method is reused)
- hermes-smd-overlay#281 (the pins), ss-console #2444 / #2225 (the trackers)
