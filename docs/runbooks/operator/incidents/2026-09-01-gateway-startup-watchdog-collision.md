# Post-incident note: the pilot seat restarted itself every 15 minutes for two and a half hours, and nothing on it was broken

| Field                   | Value                                                                                                                                                                       |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | `2026-09-01`, roughly 00:12 to 02:45 UTC                                                                                                                                    |
| Seat / surface          | `pilot-smokeball`, Machine `2862965a4e7218` (lax)                                                                                                                           |
| Severity                | SEV2 against the ADR 0064 ladder in `docs/handbook/incident-response.md`. No severity was assigned at the time; this note makes the assignment.                             |
| Detected by             | A human watching `fly logs` during the outbound-email-quality rehearsal. No instrument fired - see "How it was detected".                                                   |
| Detection lag           | Not separable. The loop began under active human observation, so there is no honest "occurred at X, noticed at Y" to record.                                                |
| Detection to resolution | Roughly 2h30m, most of it spent establishing that the seat was not wedged on the code shipped that evening.                                                                 |
| Client impact           | None observed. The seat is the pilot, not a paying client seat, and no client-visible send was due in the window. The Operator was unreachable on every channel throughout. |
| Status                  | Closed by this note plus the hardening PR that carries it. The upstream ask (below) is open.                                                                                |

**Sources.** The captured thread dump and surrounding log (`scratchpad/deathwatch.log`,
2026-09-01, 80KB of `fly logs`); `operator/templates/entrypoint.sh`;
`operator/templates/bootstrap.sh`; `operator/customers/pilot-smokeball/customer.yaml`;
Hermes upstream at the seat's pin `v2026.8.18@e624e9fd`
(`gateway/shutdown_watchdog.py`, `gateway/run.py`, read from
`NousResearch/hermes-agent` at that SHA); the memory note
`project_outbound_email_quality_program`.

## What broke

Nothing was broken. Two correct mechanisms, each doing its job, had incompatible
budgets, and the collision is structural at 1GB.

**The startup crawl.** The gateway builds its channel directory as the event
loop's first task. That work resolves every platform plugin, and resolution is a
synchronous `importlib` module load performed **on the loop**. The captured dump
shows the whole chain in one stack, with the loop's current frame inside the
import machinery:

```
File "/opt/hermes/gateway/platform_registry.py", line 441 in _resolve
File "/opt/hermes/gateway/platform_registry.py", line 499 in _resolve_all
File "/opt/hermes/gateway/platform_registry.py", line 580 in plugin_entries
File "/opt/hermes/gateway/channel_directory.py", line 190 in build_channel_directory
File "/opt/hermes/gateway/run.py", line 12934 in start
File "/opt/hermes/gateway/run.py", line 30339 in start_gateway
File "/usr/lib/python3.13/asyncio/base_events.py", line 683 in run_forever
```

On a 1 vCPU / 1GB Machine with cold page cache this took roughly four minutes.

**The watchdog budget.** Hermes arms an out-of-loop liveness watchdog at
`gateway/run.py:12186`, which is **before** the crawl at 12934. The watchdog is a
plain thread that posts `loop.call_soon_threadsafe` probes and counts failures
(`gateway/shutdown_watchdog.py:110-209`). At the pinned defaults -
`probe_interval` 30s, `probe_timeout` 10s, `max_strikes` 3 - the budget is about
**120 seconds**, and the loop cannot answer a probe while it is inside a
synchronous import. Three strikes, `os._exit(75)`.

So the loop was not wedged by a bug. It was wedged **by its own startup**, and
the instrument watching it was armed before the thing it would fail on.

**Why it sustained.** `tini` reports the exit, Fly replaces the Machine, and the
new container boots with colder caches and a journal to recover - so the next
startup is _slower_ than the one that just failed. There is no damping term
anywhere in that circuit. The loop ran at roughly 15-minute intervals with **no
external load at all**; the rehearsal one-shots only tipped it in.

Two facts worth keeping, because both were checked and both rule out the obvious
wrong answers:

- `oom_killed=false` throughout. This is not an OOM.
- The code shipped that evening was not implicated. The wedge is in upstream
  startup, and no envelope ever existed for it to wedge on.

## How it was detected

By a human reading `fly logs`. That is the finding.

The seat carries a liveness supervisor in the Machine entrypoint (ss#2488 part 1)
and a fleet-alerts path that pages off the state word it writes (part 2). Both
were running. Neither produced a page, for two separate reasons, and both are
fixed by the PR that carries this note:

1. **The supervisor never arms on a boot that was never healthy.** `/opt/data`
   persists, so a stale heartbeat from the previous boot is on disk at every cold
   start, and arming on it would SIGKILL every boot forever. The arming latch is
   correct. But not-arming had no deadline of its own: a gateway that wedges
   _during_ startup never writes a first beat, so it sat in that branch
   permanently, nagging into `fly logs` on a 5-minute floor while the state word
   stayed `not-armed` - which fleet-alerts does not page on, and must not, since
   `not-armed` is also every healthy seat's first thirty seconds.

2. **The one alarming line it did emit was false.** At 02:30:37 the supervisor
   logged "cannot resolve the gateway profile from /proc/652/cmdline; supervisor
   is INERT and this seat has no automatic recovery". The argv parse was fine.
   The entrypoint forks the supervisor while still root and then execs
   `bootstrap.sh`, which stages skills, syncs the voice vault and runs the
   appliers before its own exec of the gateway at `bootstrap.sh:916` - so for the
   first minutes of _every_ boot `/proc/<container-main>/cmdline` legitimately
   reads `bash /app/bootstrap.sh`. The bootstrap log two seconds earlier and five
   seconds later shows it seeding the skill catalog. The same boot resolved the
   profile normally once the exec landed. `inert` is a paging state, so the seat
   was calling a healthy boot an unrecoverable one, on every boot - the cheapest
   possible way to teach everyone to ignore the signal.

## Timeline as recorded

| Time (UTC)   | Event                                                                                                          | Source                            |
| ------------ | -------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| ~00:12       | Loop begins, tipped in by rehearsal one-shots running a second full hermes runtime on the box                  | memory note; not in the log dump  |
| 02:21:54     | "stale but has never been seen fresh this boot; NOT arming" - supervisor sitting in the never-armed branch     | `deathwatch.log:69`               |
| 02:27:01     | Same line, next boot, 8070s stale                                                                              | `deathwatch.log:101`              |
| 02:28:51     | Gateway still starting: tool-registry and provider warnings                                                    | `deathwatch.log:118-119`          |
| 02:29:03     | `CRITICAL gateway.shutdown_watchdog: Gateway event loop missed 3 consecutive liveness probes`; all-thread dump | `deathwatch.log:122`              |
| 02:29:03     | Dump shows the loop inside `build_channel_directory` → `platform_registry._resolve` → `importlib`              | `deathwatch.log:233-255`          |
| 02:29:05     | `Main child exited normally with code: 75`; Fly begins cleanup and replaces the Machine                        | `deathwatch.log:263`              |
| 02:30:12-13  | New boot: bootstrap env checks                                                                                 | `deathwatch.log:330-344`          |
| 02:30:35-42  | Bootstrap seeding the skill catalog (76 skill dirs)                                                            | `deathwatch.log:352,354`          |
| **02:30:37** | **"supervisor is INERT and this seat has no automatic recovery" - fired mid-bootstrap, on a healthy boot**     | `deathwatch.log:353`              |
| 02:35:44     | Back in the never-armed branch, 8592s stale                                                                    | `deathwatch.log:418`              |
| ~02:45       | `fly scale memory 2048`; loop breaks immediately, beat age 3s                                                  | memory note; probe not in the log |

## What changed to prevent recurrence

- **Landed** (the PR carrying this note):
  - `operator/customers/pilot-smokeball/customer.yaml` authors `memory_mb: 2048`.
    The live Machine was scaled by hand during the incident; the authored file
    still said 1024, so the next reprovision would have shrunk the seat straight
    back into the loop. **This is authored-vs-live drift, not a fix in itself** -
    the runtime claim was already true, the repo claim was not.
  - The supervisor distinguishes "the gateway has not started yet" from "the
    gateway will never be found", by the clock, and reports `starting` instead of
    `inert` inside a bounded startup grace
    (`SMD_GATEWAY_LIVENESS_STARTUP_GRACE_SECONDS`, default 900s).
  - A boot that produces no fresh beat within that grace now reports
    `never-healthy` and **pages**, and deliberately does **not** kill - killing a
    slow-starting gateway is the mechanism that sustained this loop, and hermes'
    own in-process watchdog already owns the kill on its own budget.
  - The argv parse accepts `--profile` and the attached forms as well as `-p`, so
    a future change to the gateway invocation fails at review rather than
    silently un-watching the seat.
  - `operator/CLAUDE.md` carries the one-shot ban.
- **Open:**
  - **Upstream ask, not yet filed.** The watchdog budget is not configurable. The
    only knob is `gateway.loop_watchdog: false` in `config.yaml`, a boolean, and
    `gateway/run.py:12063-64` says so in as many words: "no env override -
    config-only knob, #69089". `probe_interval`, `probe_timeout` and
    `max_strikes` are function defaults that
    `GatewayRunner._start_loop_liveness_guards` never overrides - it calls
    `start_loop_liveness_watchdog(loop)` with no arguments. So there is nothing to
    raise for seats, and disabling the watchdog outright is the wrong trade: it is
    the only thing that recovers a genuinely wedged loop. The ask upstream is
    either a startup grace before the watchdog arms, or config for the budget, or
    a lazy platform-plugin import so the crawl is off the loop.
  - The other 1024 seats. `smd`, `smd-staging`, `scott`, and both provisioning
    templates (`_template`, `_hosted-template`) still author 1024 and carry the
    same collision. Raising them costs real money per seat per month, so it is a
    Captain call, not an agent's. `ashton-price` authors 4096 and is already above
    the floor.

## Shadow-firm scenario

Not yet written. The behavioural half is covered instead by
`operator/templates/tests/test_gateway_liveness_supervisor.py`, which drives the
real supervisor text against a fake process tree - including the literal
NUL-separated cmdline bytes captured from this Machine - and which was observed
to FAIL against the unfixed entrypoint before the fix landed.

## Ladder consequence

None. No routine was demoted: the seat was unreachable rather than wrong, and no
routine produced an incorrect action during the window.

## Not recorded

- The exact start of the loop. `~00:12` comes from the memory note, not from the
  captured log, which begins later.
- How many restarts occurred in total. The captured window holds three boots; the
  kill ledger would not show them either, since these were hermes' own exits, not
  supervisor kills.
- Whether 2048 is sufficient with a heavier connector set than this seat runs. It
  is the floor that was proven here, not a ceiling that was measured anywhere.
- Why startup took four minutes rather than one. The dump names the frame, not
  the cost distribution across plugins.
