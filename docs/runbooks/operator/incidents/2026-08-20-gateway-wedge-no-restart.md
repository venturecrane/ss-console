# Post-incident note: the Operator went silent for 33 minutes and only a human brought it back

| Field                   | Value                                                                                                                                                                                                                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | `2026-08-20` (evening MST; `2026-08-21` UTC)                                                                                                                                                                                                                                                 |
| Seat / surface          | `hermes-ashton-price` -- the paying client's seat, mid go-live                                                                                                                                                                                                                               |
| Severity                | No severity was assigned at the time. Assigned here against the ADR 0064 ladder in `docs/handbook/incident-response.md`: **SEV2** -- total loss of the Operator on a paying seat, with no client-observable impact because the firm-address fence was still up. The issue carries `prio:P0`. |
| Detected by             | A human. Every automated instrument on the seat reported healthy throughout.                                                                                                                                                                                                                 |
| Detection lag           | ~23 minutes from the last successful turn (03:02:01Z) to the watchdog's CRITICAL line (03:24:57Z), and ~38 minutes to the human restart (03:40:37Z). Nothing detected it in between.                                                                                                         |
| Detection to resolution | ~16 minutes from the CRITICAL line to the manual restart. The seat was unreachable for 33 minutes in total.                                                                                                                                                                                  |
| Client impact           | None observed. The firm-address fence (`domain_blocks`) was still up, so nobody at Ashton & Price could reach the Operator to notice. **That fence comes down in the next step of the go-live.**                                                                                             |
| Status                  | Open -- owned by [ss#2488](https://github.com/venturecrane/ss-console/issues/2488)                                                                                                                                                                                                           |

**Sources.** ss#2488 (the filed issue, including the `gateway.log` and `gateway-exit-diag.log` excerpts, which this note does not re-derive); `vfy_01M0H7A2AZGGHZR3EM2FSZVB82` (the seat's Fly log buffer across the window, plus `fly status`); `vfy_01M0H7AFYWQ9EBZNGNFFMZNZKJ` (the Hermes source read at the pin the seat runs); `vfy_01M0H9BKDCTFKSC5WSS9Z9DYVG` (the live heartbeat and process tree on `hermes-smd`); `operator/customers/ashton-price/customer.yaml:102`; `operator/templates/entrypoint.sh`; `operator/templates/fly.toml.template`; Law 12 (a check that cannot fail has measured nothing).

## What broke

The gateway's asyncio event loop stopped. Hermes has a backstop for exactly this: an out-of-loop OS thread that probes the loop every 30s and, after three missed probes, dumps all thread stacks and hard-exits so a service manager can restart the process. It fired. It logged, verbatim:

> Gateway event loop missed 3 consecutive liveness probes; dumping all thread stacks and exiting with code 75 so the service supervisor can restart it.

It did not exit. The gateway pid stayed 655 until a human restarted it.

**Three things the issue asserted, which the source at the pinned SHA contradicts.** They are recorded here because each one would have sent the next reader down a wrong path.

1. **The exit was already unconditional.** At `v2026.8.18@e624e9fd` -- the exact SHA in `customer.yaml:102` -- `gateway/shutdown_watchdog.py:196` calls `os._exit(exit_code)` directly. There is no graceful asyncio shutdown on that path, so "a clean asyncio shutdown needs the very event loop that was wedged" cannot be the mechanism. The thread reached its `logger.critical` at line 173 (the line is in `gateway.log`) and never reached the `os._exit` two statements later. Between them sit exactly two calls: `faulthandler.dump_traceback(all_threads=True)` (line 183) and `mark_exited` (line 193). **Which of the two stalled was never established, and the evidence to establish it is gone.**

2. **The 0-byte `gateway_faulthandler.log` was correct behaviour, not a missing diagnostic.** That file is written only by `faulthandler.register(SIGUSR2, file=…)` at `gateway/run.py:12169`. Nobody had ever sent SIGUSR2, so the file had never been written. The watchdog's own dump call passes no `file=` argument and therefore goes to **stderr**. It never landed there either: the seat's Fly log buffer covers 03:02:00Z to 03:49:01Z with unbroken 30-second coverage and contains zero traceback lines.

3. **`gateway-exit-diag.log` cannot distinguish a watchdog that exited from one that hung.** `os._exit` bypasses `atexit` and never returns from `asyncio.run`, so a _successful_ watchdog exit writes nothing to that file either. The evidence that the exit did not happen is the pid, not the diag log.

**And the defect the issue did not name, which is why nobody knew.** The seat's only Fly health check is `GET /health` on our own webhook gate, and that handler is a literal constant -- `self._json(200, {"status": "ok", "platform": "webhook-gate"})` (overlay `webhook_gate.py:985` at pinned `OVERLAY_REF 7a8ba42`). It observes nothing about the gateway, and the gate runs in a **separate process** that was never wedged. It answered 200 every 30 seconds throughout the outage; `fly machine status` still prints that exact constant as the check output. The ADR 0023 control-plane heartbeat and the healthchecks.io dead-man ping are emitted from that same gate process, so they stayed green too. `fleet-alerts` does carry a work-liveness condition that would have caught this (`scheduler_max_overdue_seconds > 900s`, `workers/fleet-alerts/src/index.ts:384`) -- except A&P's crons were turned off for go-live (ss#2332). Every liveness instrument on that seat was disarmed, three of them by construction and one by configuration.

**Why nothing restarted it.** `entrypoint.sh` `exec`s the gateway as the container's main process under tini. The root respawn supervisor in that file covers the **broker**, not the gateway. `under_systemd=no`. Fly restarts a Machine when its process exits (`restart: [{policy: "on-failure"}]`), but not when a health check fails -- and the health check was passing anyway.

## How it was detected

A human noticed the seat was unreachable and restarted the gateway by hand. The instrument that should have caught it reported healthy for the entire 33 minutes, and is still doing so.

## Timeline as recorded

| Time (UTC)  | Event                                                                                               | Source                                    |
| ----------- | --------------------------------------------------------------------------------------------------- | ----------------------------------------- |
| 03:02:01    | Establishment survey completes cleanly (203.3s, 11 API calls, response sent). Last successful work. | ss#2488, `gateway.log`                    |
| 03:02:00    | Webhook gate answers `GET /health` 200. It continues every 30s, without a gap, to 03:49:01.         | `vfy_01M0H7A2AZGGHZR3EM2FSZVB82`          |
| 03:02–03:24 | No poller tick, no housekeeping, no gateway log line of any kind.                                   | ss#2488                                   |
| 03:24:57    | `CRITICAL gateway.shutdown_watchdog`: missed 3 liveness probes, "exiting with code 75".             | ss#2488, `gateway.log`                    |
| 03:24:57    | No stack dump reaches stderr. No `gateway.exit_*` or `atexit.hook` record is written.               | `vfy_01M0H7A2AZGGHZR3EM2FSZVB82`          |
| 03:24–03:40 | Machine `started`, gateway pid still 655, health check passing.                                     | ss#2488, `fly status`                     |
| 03:40:02    | SIGINT received as a planned gateway stop -- a human.                                               | ss#2488                                   |
| 03:40:37    | `gateway.start` pid 657. The webhook gate process is **not** interrupted across this.               | ss#2488, `vfy_01M0H7A2AZGGHZR3EM2FSZVB82` |

The 22-minute gap between the last turn and the watchdog's CRITICAL line is unexplained: three missed 30-second probes should trip it in roughly 90 seconds.

## What changed to prevent recurrence

- **Landed and proven on a seat (part 1, #2502, `vfy_01M0HEM5VM26XFVD8W4XW19AQF`):** a root-side gateway liveness supervisor in `operator/templates/entrypoint.sh`, forked before the exec-drop in the same shape as the broker respawner. It watches the loop heartbeat Hermes already writes (an asyncio task on the very loop that wedges), escalates SIGUSR2 → SIGTERM → SIGKILL against the container main process, and bounds itself with a kill ledger on the volume so a flapping seat stops and pages instead of restarting forever. On `hermes-scott` (same Hermes pin as A&P): wedge at 05:54:12Z, container `exit_code=137` at 05:59:15Z, new gateway pid by 05:59:17Z, nobody involved. The baseline on the old image (`vfy_01M0HD766MTCE7KE7T7FTZYMXR`) reproduced the incident exactly: 340s frozen, no restart, `/health` green throughout. The negative control held: a 383s-stale previous-boot beat did not trigger a second kill. `SIGSTOP` freezes every thread, so SIGUSR2 could not be serviced and the stack dump stayed 0 bytes; best-effort, recorded, not waived.
- **Landed (repo layer, part 2 -- the false-green surface; runtime proof pending):** the webhook gate, which survives a wedge, now reads the gateway's pulse age and the supervisor's state and puts four fields on the control-plane heartbeat (`gateway_loop_ok`, `gateway_loop_age_seconds`, `gateway_supervisor_state`, `gateway_restarts_last_hour`; overlay PR #293, seat side #2505). Migration `0107` lands them in `fleet_status`; `fleet-alerts` pages on five conditions (`gateway_loop_wedged`, `gateway_restarted`, `gateway_supervisor_refusing`, `gateway_supervisor_inert`, `gateway_loop_unprovable`); the admin roster paints a wedged loop or a refusing supervisor red. Two signals because one would be racable -- the part-1 restart is also what refreshes the heartbeat, and on `hermes-scott` it landed inside the window where an age-only alert could have been overwritten before the 2-minute cron sampled it. The kill ledger is the event record a restart cannot race. Fly's `/health` stays a constant by decision: a failing Fly check stops routing to the Machine, trading a bounded outage for an unbounded one on a wrong reading.
- **Open:** the part-2 runtime proof (wedge `hermes-scott` at the new overlay ref; observe the ALERT email land before the restart and the `gateway_restarted` page after it); the ledger-refusal proof (three kills in an hour); and the reprovision that puts both parts on `hermes-ashton-price`, which is sequenced with the go-live session that owns that seat.

## Shadow-firm scenario

`not yet written` (#2389). A scenario for this would need to hold the gateway loop without killing the process, and it is only trusted once observed to FAIL against the unfixed state.

## Ladder consequence

None. No routine was demoted: the failure was in the runtime substrate, not in any routine's behaviour, and A&P's crons were already off for go-live (ss#2332).

## Not recorded

- **Which of the two calls stalled the watchdog thread** -- `faulthandler.dump_traceback` or `mark_exited`. The stack dump that would have said went to stderr and never arrived, so the answer for this occurrence is unrecoverable.
- **Why the watchdog took 22 minutes** to notice a loop it should trip in ~90 seconds.
- **What wedged the loop in the first place.** Contemporaneous conditions only: `loadavg_1m=15.25` on 1 vCPU / 1024 MB, "system memory pressure is elevated" still logging every 60s after the restart, one zombie worker reaped on boot. No causal link is established by any source.
- **How the gateway pid moved 655 → 657 without the container restarting.** The webhook gate process ran unbroken across 03:40, so tini's child cannot simply have exited and been replaced. The supervisor is deliberately built not to depend on the answer: it kills `SMD_GATEWAY_PID`, verifies the process actually went away, and falls back to the pid the heartbeat itself names.
- **Whether the Fly health check was ever `critical`.** ss#2488 reports it as critical throughout; the log buffer shows the gate answering 200 to Fly's prober every 30s across the same window, and `fly status` reports "1 total, 1 passing". The discrepancy is not resolved here, and Fly retains no check history to resolve it with.
