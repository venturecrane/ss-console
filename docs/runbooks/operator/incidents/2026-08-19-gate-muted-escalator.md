# Post-incident note: a court date seven days out was escalated five times and reached nobody

**Filename convention:** `YYYY-MM-DD-short-slug.md`, dated by the day the incident began.

| Field                   | Value                                                                                                                                                                                                                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | 2026-08-19 (the refused escalation). The silence runs 2026-08-19 to 2026-08-22 and the same class recurs from 2026-08-04.                                                                                                                                     |
| Seat / surface          | `pilot-smokeball`, the `deadline-miss-escalator` routine, the `smd_send_message` outbound path                                                                                                                                                                |
| Severity                | No severity was assigned at the time. Assigned here against the ADR 0064 ladder in `docs/handbook/incident-response.md`: **SEV2**, a capability failing without client-observable impact. The routine that carries a deadline to a human could not carry one. |
| Detected by             | The Captain, noticing that the escalator had gone quiet. No instrument fired.                                                                                                                                                                                 |
| Detection lag           | About three days: the refusals are stamped 2026-08-19T14:00:45Z to 14:01:20Z and the silence was raised on 2026-08-22.                                                                                                                                        |
| Detection to resolution | `not recorded`. The remediation is in flight under [ss#2547](https://github.com/venturecrane/ss-console/issues/2547) and no runtime proof exists yet.                                                                                                         |
| Client impact           | None observed. Every recipient in the window is `scott@smd.services`, and `pilot-smokeball` carries no client matters. What a client seat would have experienced is `not recorded`, because no client seat runs this routine today.                           |
| Status                  | Open, [ss#2547](https://github.com/venturecrane/ss-console/issues/2547)                                                                                                                                                                                       |

**Sources.** The `pilot-smokeball` audit ledger, read on 2026-08-22 and recorded as `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`: `TOOL_CALL_COMPLETED`, `IDENTIFIER_UNVERIFIED`, `FABRICATION_FILTER_TRIGGERED`, `EMITTED_WAKE` and `CONFIRM_SEND_DISPATCHED` rows across 2026-08-04 to 2026-08-21. Issues [#2234](https://github.com/venturecrane/ss-console/issues/2234) and [#2348](https://github.com/venturecrane/ss-console/issues/2348) for the two prior instances. `operator/skills/deadline-miss-escalator/pre_run.py`. The `hermes-smd-overlay` `origin/main` tree, searched for any reference to pre-run script output under `plugins/` and `shared/`.

## What broke

**The routine whose entire job is to reach a human could not get past its own gates, and the refusals were audit rows nobody was reading.**

On 2026-08-19 the escalator woke with one needs-you item, a court date seven days out (`EMITTED_WAKE`, `digest_needs_you: 1`). Between 14:00:45Z and 14:01:20Z it called `smd_send_message` five times, addressed to `scott@smd.services`. All five were refused:

- once by the em-dash marker in the fabrication filter;
- four times by the identifier gate, on `date` atoms. The successive `unverified_counts` for `date` are 16, then 3, then 2, then 2, with `date_distance {1-7d: 2}` and a provenance register that was **not empty**. The turn was narrowing its message on each retry, and the last two attempts still carried two dates it could not certify.

On 2026-08-20 it woke again with five needs-you items and 33 admin confirmations (`digest_needs_you: 5`, `digest_admin_total: 33`) and made **zero send attempts** in that session. Whether the turn chose not to try, or tried through a path that leaves no row, is `not recorded`.

**Why the dates could not be certified, which is the actual mechanism.** The escalator's `pre_run.py` reads the authored dates off the firm's record through the Smokeball broker and puts them on the wake line verbatim (`authored_date`, no date arithmetic, `operator/skills/deadline-miss-escalator/pre_run.py`). Hermes injects that stdout into the woken turn's prompt as a "Script Output" block. **Nothing seeds the provenance register from that output**: the overlay's `origin/main` tree contains no reference to pre-run script output anywhere under `plugins/` or `shared/`. So the gate did its job correctly. From inside the turn, a date that arrived as prompt text is indistinguishable from a date the model composed, and the read that produced it happened in a subprocess the register never saw.

This is not an over-broad date rule. It is a read with no seam to record itself through.

**The class, and why it is the finding rather than the defect.** This is the third time a newly shipped gate silenced this same routine on this same path:

| When                     | What refused it                                                                                                      | Owner                                                             |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 2026-08-04 to 2026-08-09 | The voice-spec gate, nine refusals: "seat declares an authored voice spec for 'staff' and this turn did not read it" | [#2234](https://github.com/venturecrane/ss-console/issues/2234)   |
| 2026-08-13               | `TypeError: _smd_send_message() takes 0 positional arguments but 1 was given`, three times                           | [#2348](https://github.com/venturecrane/ss-console/issues/2348)   |
| 2026-08-19               | The em-dash marker once, the identifier gate on `date` four times                                                    | [ss#2547](https://github.com/venturecrane/ss-console/issues/2547) |

Sends to `scott@smd.services` resumed on 2026-08-11 after #2234, and the ledger records successful sends on 2026-08-14 (two) and 2026-08-18 (two). Then they stop.

Each individual gate was correct and each fix was correct. What was missing every time is the same thing: **from outside the seat, "refused", "did not try", and "nothing to report" produce the identical observation, which is silence.**

## How it was detected

By the Captain, noticing on 2026-08-22 that the escalator had stopped writing. Not by an instrument.

Every instrument the seat has was green throughout. The heartbeat was fresh, the scheduler was healthy, the crons fired on time, and the routine woke on schedule on both days. It did exactly what it was scheduled to do and produced nothing, and nothing in the fleet's health projection has a field for that.

Absence of expected output was available as evidence for three days and is not a signal anything watches, which is the same shape #2348 recorded when the escalator mails stopped on 2026-08-09 and Sentry was what eventually spoke.

## Timeline as recorded

| Time (UTC)                      | Event                                                                                                    | Source                                          |
| ------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| 2026-08-04 to 2026-08-09        | Nine sends refused by the voice-spec gate                                                                | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`; #2234 |
| 2026-08-11                      | Sends resume after the #2234 fix                                                                         | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`        |
| 2026-08-13                      | Three `smd_send_message` calls raise `TypeError`                                                         | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`; #2348 |
| 2026-08-14                      | Two sends to `scott@smd.services` succeed                                                                | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`        |
| 2026-08-18                      | Two sends to `scott@smd.services` succeed. The last successful escalation on record.                     | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`        |
| 2026-08-19 (wake)               | `EMITTED_WAKE` with `digest_needs_you: 1`, a court date seven days out                                   | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`        |
| 2026-08-19 14:00:45 to 14:01:20 | Five `smd_send_message` attempts, all refused: em-dash marker once, identifier gate on `date` four times | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`        |
| 2026-08-20 (wake)               | `EMITTED_WAKE` with `digest_needs_you: 5`, `digest_admin_total: 33`. No send attempted in that session.  | Ledger, `vfy_01M0N0NZBPQX6XADDVTZ5DEZXP`        |
| 2026-08-22                      | The Captain raises the silence                                                                           | This session                                    |

The exact wake times on 2026-08-19 and 2026-08-20 are `not recorded` in what was read.

## What changed to prevent recurrence

**Landed (repo layer, ss#2547).** Three changes, none of them a runtime claim:

1. **The read gets a seam.** Every bespoke `pre_run.py` that emits authored record dates now writes a handoff file next to its wake line, projecting only the dates and matter ids it emitted. The overlay reads it, binds it to the one session the script ran for, seeds the date atoms into the provenance register, and consumes it. The script's read becomes a source instead of prose. Best-effort by construction: a routine that cannot write a handoff still wakes.
2. **The refusal reaches a human.** The seat's heartbeat now carries `send_refusals`, `send_refusals_last_ts` and the newest few events verbatim (migration 0109), and `workers/fleet-alerts/src/send-refused.ts` pages `team@smd.services` once per new event, naming the routine and the reason. It is event-shaped rather than level-shaped on purpose: a refusal has no green state to return to, so a level would page once and then go quiet through a week of daily refusals.
3. **The class is written down.** This note, the `Class` column in `README.md`, and the rule under "The contract" that a new refusal gate on an outbound path is not done when its unit tests pass.

**Open (runtime layer).** Nothing here is wired yet. The overlay change reaches no seat until `OVERLAY_REF` is bumped and `pilot-smokeball` is reprovisioned, and the pager has never fired on a real seat. The proofs ss#2547 owes are: the field on the wire at boot; a deliberately refused send producing a page in `team@`; a second beat not re-paging; and the escalator's next needs-you day showing `CONFIRM_SEND_DISPATCHED` with no refusal row of any kind. Until those exist, this note claims a repo change and nothing more (Law 9).

## Shadow-firm scenario

`not yet written`. The scenario that replays this is the escalator woken with a needs-you item whose date came only from the pre-run read, scored on whether a message ARRIVES rather than on whether the turn ran. A scenario that scores on the turn completing would have passed on both 2026-08-19 and 2026-08-20.

## Ladder consequence

None applied at the time, and none applied retrospectively here. `deadline-miss-escalator` was already at the rung where it sends to `scott@smd.services` and to no client, and the demotion rule in `docs/runbooks/operator/enable-gate-checklist.md` has nothing lower to move it to for this seat. The rung question the incident actually raises is whether a routine can hold Rung 3 evidence recorded before a gate shipped, which is the argument #2348 already made.

## Not recorded

- Why the 2026-08-20 wake made no send attempt at all. Refused-before-dispatch and never-attempted are indistinguishable in what was read.
- The em-dash refusal's tool input. Whether the em dash came from a label the script emitted or from the turn's own prose cannot be recovered.
- Which two dates survived to the fourth attempt, and whether they were the same two.
- The exact wake times on 2026-08-19 and 2026-08-20.
- Whether anything was escalated by another route in the window.
- How many needs-you items accumulated between 2026-08-20 and 2026-08-22.
- Detection to resolution, which does not exist until the ss#2547 runtime proofs do.
