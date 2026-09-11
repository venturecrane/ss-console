# The shadow firm

A standing adversarial rehearsal suite (ss#2389). It plays the client hostile
against a rig seat, replays every incident class this venture has actually had,
scores each scenario mechanically, and emits a run id that stands as the proof of
an `OVERLAY_REF` bump's runtime AC. The run happens after the rig has been
reprovisioned onto the candidate ref, and the runner refuses to drive unless the
rig is already running it.

Operating instructions, the exit-code table, and the release-gate procedure live
in `docs/runbooks/operator/shadow-firm.md`. This file is the map of the code.

## The hard line

No scenario may target a client seat, a client-visible address, or a production
tenant. Enforced in `scope.py` at load time, not in a comment. See the runbook.

## Layout

| File          | What it owns                                                                                                                                      |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scope.py`    | The hard line: the drivable-address allowlist and the rig seat-kind gate. Refuses, never warns.                                                   |
| `registry.py` | Loads and validates `scenarios/*.yaml`. Refuses a leg with no expectation, an action type outside the audit vocabulary, or anything out of scope. |
| `scoring.py`  | Pure predicates over an observation bundle. No I/O, no judgement of prose. PASS / FAIL / SKIPPED.                                                 |
| `drivers.py`  | The I/O half: AgentMail probe sends, the ADR 0043 audit read seam, mailbox reads, and the ss#2258 send reconciliation.                            |
| `report.py`   | The run artifact and the run id, which is a digest over seat, candidate ref, and outcomes.                                                        |
| `run.py`      | The CLI, and the gate that refuses to drive unless the rig's running overlay ref equals the candidate this run would certify.                     |
| `scenarios/`  | One YAML file per incident class.                                                                                                                 |
| `tests/`      | The Law 12 falsifier, the scope-guard tests, and the running-ref gate's own refusals.                                                             |

## Why the split between `scoring.py` and `drivers.py`

So the failure path can be exercised without a seat. `tests/test_falsifier.py`
feeds hand-built observation bundles and deliberately broken scenarios to the
real scorer and asserts FAIL. A suite whose FAIL path has never run is a suite
that has only ever been observed agreeing with us.

## Why this grades when `rehearse-card.py` refuses to

`rehearse-card.py` replays a script whose replies are prose, and prose graded by
the same agent that wrote the prompt is how a mis-designed test once read as a
product failure. Nothing here reads an answer for meaning: every predicate is a
named audit row appearing or not appearing, a message arriving or not arriving in
a mailbox we own, or a regex over a reply.
