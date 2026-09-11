# Post-incident note: the seat's breaker tripped on real mail, and the resume bypassed its own governance surface

| Field                   | Value                                                                                                                                                           |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | 2026-09-01                                                                                                                                                      |
| Seat / surface          | `ashton-price` (production), plus the admin console's clear surface (unused)                                                                                    |
| Severity                | SEV2 (client-facing capability muted ~5h on a production seat; no data loss, no wrong output). Assigned in this note; none was assigned at the time.            |
| Detected by             | A rehearsal job submission holding instantly with "seat paused (sticky stop HARD_STOP)" - found incidentally, ~2h after the trip, while proving ss#2616 slice 7 |
| Detection lag           | ~2h04m (trip 18:32:01Z, noticed ~20:36Z)                                                                                                                        |
| Detection to resolution | ~2m to clear once authorized (20:38:26Z); root cause of the trip fixed same day (ss#2680)                                                                       |
| Client impact           | The Operator took no turns 18:32Z-20:38Z: inbound mail queued unanswered. Two replies had gone out normally minutes before the trip.                            |
| Status                  | Closed - trip cause fixed (ss#2680), governance backfilled, this note + `../sticky-stop-clear.md` landed in the same PR                                         |

**Sources.** ss#2616 session 210a1345; `vfy_01M1FB52Y7PEW0HSQVZB8HV2PW` (the
clear), `vfy_01M1CTSQNQJQS8YGRJKKZVJTMM` / `vfy_01M1D2XJHTQ06PPTYPAD9E7869`
(surrounding rehearsals); PR ss#2680; the seat audit ledger windows quoted
below; `migrations/0085_operator_stop_clears.sql`; overlay
`tests/test_connector_signatures.py`.

## What broke

Two distinct defects, one mechanical and one operational:

1. **The trip (mechanical).** Real inbound client mail drove multi-matter
   turns. The agent passed matter **numbers** (10006, 202248, 200285) to
   `/matters/{id}` paths; Smokeball 404'd three times in one burst; Hermes'
   MCP-client circuit - which is **derivative**, bumping on any error result
   (`mcp_tool.py:3446`, pinned by the overlay's `test_connector_signatures.py`)
   - declared the server "unreachable"; every subsequent Smokeball call then
     failed instantly, and the sticky ladder counted 5 (WARN->SOFT_STOP) then 8
     (HARD_STOP) consecutive `list_matters` failures inside its 600s window.
     The breaker behaved exactly as designed.
2. **The resume (operational).** The responder concluded "no wired clear
   surface exists" and cleared the stop by raw sqlite UPDATE on the seat's
   `sticky_stop.db`. The surface existed in full: gate `POST
/sticky-stop/clear`, console lib + admin route, a visible "Clear cost stop"
   form on the admin operator page, and the D1 governance table
   `operator_stop_clears`. The false conclusion came from two instrument
   artifacts: a `grep ... | head -5` that cut the listing above
   `sticky-stop-clear.ts`, and a `grep clear-stop | grep -v api/admin` that
   excluded the one `.astro` form whose action URL contains `api/admin`. The
   raw clear worked but left `operator_stop_clears` silent - a resume the
   governance trail cannot see, against a written "all changes logged"
   commitment.

## How it was detected

Not by the pager. fleet-alerts computes a `hard_stop` condition from the
heartbeat's `sticky_stop_level`, but nothing in the responder's path surfaced
it; the stop was found when a slice-7 wake-rehearsal job pause-held instantly.
The instrument that should have handed the responder the clear surface - the
alert text - named only the level.

## Timeline as recorded

| Time (UTC)  | Event                                                                                        | Source                           |
| ----------- | -------------------------------------------------------------------------------------------- | -------------------------------- |
| 18:08-18:30 | msgraph `message.received` webhooks drive multi-matter turns; two replies confirm-dispatched | seat audit ledger window         |
| 18:30:44    | Three `get_files_on_matter` 404s (matter numbers in id paths)                                | audit ledger `error_type` rows   |
| 18:30:51    | `INVARIANT_VIOLATION` consecutive_tool_failures=5 (WARN->SOFT_STOP)                          | audit ledger                     |
| 18:30:56    | `AGENT_STOPPED` consecutive_tool_failures=8 -> HARD_STOP                                     | audit ledger                     |
| 18:32:01    | State row `updated_at` at HARD_STOP                                                          | `sticky_stop_state` row          |
| ~20:36      | Stop discovered via pause-held rehearsal job                                                 | session 210a1345                 |
| 20:38:26    | Cleared by raw sqlite under Captain direction                                                | `vfy_01M1FB52Y7PEW0HSQVZB8HV2PW` |
| 2026-09-02  | Governance row backfilled into `operator_stop_clears`, BACKFILL-marked                       | the backfill row itself          |

## What changed to prevent recurrence

- **Landed:** ss#2680 - the connector refuses a matter-number-shaped id
  segment before any HTTP leaves the process (one instructive refusal instead
  of a 404 burst that opens the derivative circuit). This PR: the `hard_stop`
  alert text now carries the clear surface + runbook pointer (push-model);
  `../sticky-stop-clear.md` documents the blessed path, triage-before-clear,
  and the break-glass mode; the 09-01 clear is backfilled into
  `operator_stop_clears` as a self-marked reconstruction (`gate_level` NULL,
  `BACKFILL`-prefixed reason).
- **Open:** none owned by this incident. (The derivative-circuit behavior is
  Hermes core, plugin-rule out of reach; the connector guard removes its
  trigger class.)

## Shadow-firm scenario

Not yet written.

## Ladder consequence

None - the affected surface was the whole seat (breaker), not a routine; no
routine was above Rung 1 on this seat for the affected window.

## Not recorded

Which specific inbound message triggered the multi-matter turn that produced
the bad tool calls (digests only, by design); whether the agent would have
self-corrected absent the derivative circuit (the circuit masked the
instructive 404 after three calls).
