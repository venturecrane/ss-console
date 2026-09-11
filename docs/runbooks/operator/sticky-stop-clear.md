# Sticky-stop clear: seeing, investigating, and resuming a stopped seat

**Status:** standing runbook. **Owner:** Operator platform.
**Source incident:** `incidents/2026-09-01-sticky-stop-raw-sqlite-bypass.md` -
a HARD_STOP was cleared by raw sqlite on a production seat while the built,
authenticated clear surface sat unused, because nothing in the ops path named
it. This runbook is that pointer.

## What the sticky stop is

The seat's automated circuit breaker (ADR 0062; `operator/safety-substrate/sticky_stop.py`,
vendored into the overlay). Two states, `OK -> HARD_STOP`, driven by recorded
conditions - cost spend, consecutive tool failures, refusal cascades, time
budget (`operator/contracts/runtime-controls.yaml`, the
`sticky_stop_*` rows). State is one row per `(customer, persona)` in
`/opt/data/smd/sticky_stop.db` on the seat's volume; it survives reprovision by
design. At HARD_STOP the trust plugin blocks every tool call, the gate 503s
inbound routes, and the medchron daemon pause-holds its jobs. **Transitions are
forward-only: nothing auto-resumes a system stop.** The only path back to OK is
a human clear.

The ladder had four rungs until 2026-09-02. `WARN` and `SOFT_STOP` were removed
because neither ever restricted anything, so a seat sitting at one of them was
working normally while the console said a brake was engaged. Two things follow
for you at 2am: **a seat is either running or stopped, nothing in between**, and
**the stop counts did not move** - whatever tripped a HARD_STOP before trips it
at the same count now. A seat still running a pre-collapse overlay can report
`WARN` or `SOFT_STOP` until it is reprovisioned; read either as running.

## How you see it

- **The page.** The alert for `hard_stop` (fleet-alerts) names the clear
  surface directly. Follow it.
- **Admin console.** `https://admin.smd.services/admin/operator/<slug>` shows
  the level (from the heartbeat's `sticky_stop_level`) and, at HARD_STOP,
  renders the **"Clear cost stop"** form. It still renders at a legacy
  SOFT_STOP too, so a pre-collapse seat is clearable from the same place.
- **On the seat** (when the heartbeat is in doubt): the medchron daemon's
  heartbeat json carries the level's consequences, and the state row itself is
  readable via a read-only query of `sticky_stop_state` (the shape
  `operator/runners/medchron/medchron/daemon.py` `sticky_level()` uses).
- **Why it tripped:** the row's `reason`/`condition` columns, and the Machine
  audit ledger around the trip (`AGENT_STOPPED` for HARD_STOP;
  `INVARIANT_VIOLATION` now marks an observation that changed no level, and on
  a pre-collapse seat also the old WARN/SOFT_STOP steps, plus the `error_type` strings on
  the surrounding `TOOL_CALL_COMPLETED` rows - the 09-01 investigation method).

## Investigate BEFORE you clear

A stop is the product working. Clearing without knowing why it tripped re-arms
a seat against the same fault:

1. Read the state row's `reason` and `condition`.
2. Query the audit ledger for the window around `updated_at`: the errored tool
   calls (`json_extract(metadata,'$.error_type')`) and the
   `INVARIANT_VIOLATION`/`AGENT_STOPPED` transition rows tell the story.
3. Fix or verify the underlying cause first. A cost trip needs a spend
   decision, not a reflexive clear - and note that `clear()` deliberately
   preserves `cost_cents_today`, so a cleared cost trip re-trips on the next
   recorded cent until the day rolls.

## The one blessed clear path

Admin console -> `https://admin.smd.services/admin/operator/<slug>` ->
**"Clear cost stop"** -> type the reason (required) -> submit.

End to end, that is: admin session authenticates you ->
`POST /api/admin/operator/<slug>/clear-stop`
(`src/pages/api/admin/operator/[customer]/clear-stop.ts`) ->
`clearStopOnMachine` POSTs the seat gate's `POST /sticky-stop/clear`
(Bearer-authed; overlay `webhook_gate.py`) -> the real
`StickyStopMachine.clear()` resets the ladder to OK -> the governance row
lands in D1 `operator_stop_clears` (who, when, why, what cleared - migration
0085). The Machine ledger deliberately carries no resume row: the stop is a
runtime event on the Machine ledger, the resume is a governance action audited
control-plane-side where you were authenticated (the 0085 header states this).

Clears any level, including a legacy WARN/SOFT pin on a pre-collapse seat. (The
form renders at HARD_STOP and at a legacy SOFT_STOP; for a WARN-only pin use the
break-glass path below.)

## Break-glass (console or heartbeat degraded)

The form renders only when the heartbeat has reported the level; a degraded
seat can be stopped while the console shows nothing. The sanctioned degraded
mode is the same gate endpoint, called directly:

1. `POST https://hermes-<slug>.fly.dev/sticky-stop/clear` with
   `Authorization: Bearer <slug-derived key>` (the derivation lives in
   `src/lib/operator/runtime-read-transport.ts`; never paste the master secret)
   and JSON body `{"captain_id": "<your email>", "reason": "<why>"}`.
2. Then write the governance row yourself: a one-shot parameterized
   `wrangler d1 execute --command` INSERT into `operator_stop_clears`, with the
   `reason` prefixed `BACKFILL <date>:` and `gate_level` from the gate's
   response. **Never a migration file** - a backfill in a migration fabricates
   the row in every future database.

**Raw sqlite on the seat is banned.** It works mechanically, it resets the
wrong things or misses counters, it races the runtime's writer, and it leaves
the governance table silent - the 09-01 incident, exactly.

## After the clear

- The trust wall re-reads within ~2 seconds; gate routes and daemon jobs
  resume on their next tick (a medchron pause-held job self-resumes).
- Confirm the seat is doing work again (heartbeat, or the surface the client
  uses), and that `operator_stop_clears` carries your row.
