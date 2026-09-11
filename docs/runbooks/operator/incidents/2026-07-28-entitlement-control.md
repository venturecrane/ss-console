# Post-incident note: the epic closed green and a Named Administrator could not change a routine's level

**Backfilled 2026-08-17 under #2391.** No note was written at the time. Every fact below is attributed to a source named in the Sources block; nothing is reconstructed.

| Field                   | Value                                                                                                                                                                                                                                                             |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | 2026-07-28                                                                                                                                                                                                                                                        |
| Seat / surface          | The entitlement-control feature: a Named Administrator changing a routine's autonomy level from the surface they use                                                                                                                                              |
| Severity                | Not a SEV1 under ADR 0064: the Operator was neither down nor acting outside its entitlements. It is recorded here because it is a **delivery** failure class the ladder depends on, and because Law 9 exists because of it. No severity was assigned at the time. |
| Detected by             | `not recorded` in the source. What the source records is that the epic closed green and the act was found to be impossible afterwards.                                                                                                                            |
| Detection lag           | `not recorded`                                                                                                                                                                                                                                                    |
| Detection to resolution | `not recorded`                                                                                                                                                                                                                                                    |
| Client impact           | The named act could not be performed. Whether any client attempted it is `not recorded`.                                                                                                                                                                          |
| Status                  | The failure class is closed by a merge gate (below). The feature's own completion state is owned elsewhere and is not asserted here.                                                                                                                              |

**Sources.** Law 9 (`deliverable-is-the-act`) in `docs/doctrine/agent-operating-doctrine.md:197-225`, including its incident block and the `vfy_01KYNVJ4VG90G26SZSYPXF05KY` citation; the "Done means the client can do it" section of `CLAUDE.md`, which narrates the same incident; `.github/workflows/runtime-ac-proof.yml`; `scripts/runtime-ac-proof.mjs`; `docs/doctrine/wired-contract.md`.

## What broke

Four PRs shipped against one epic. **Each was individually honest.** One of them wrote, in its own body, "Next slices, unbuilt and not implied here." Nobody lied and nobody was careless. The artifacts summed to less than the feature, the epic closed green, and **a Named Administrator could not change a routine's level** (Law 9 incident block, `docs/doctrine/agent-operating-doctrine.md:212-213`).

The doctrine names the mechanism rather than the mistake, which is the part that matters for the ladder: a PR that defines done as the artifact it added can be entirely truthful and still leave the feature dead, so asking for more diligence does not reach it. Built, wired and tested are three different claims. **Built** means the code exists and its own tests pass, which is the weakest of the three and the easiest to mistake for done because it produces the most visible evidence. **Wired** means every gate between a real client's finger and the effect is open on the deployment that client uses, configured rather than configurable, with secrets and config authoring part of the deliverable rather than someone else's prerequisite. **Tested** means someone performed the act as the client, on the real seat, and observed the far end change.

There is a second, structural half. The acceptance-criteria machinery certified the author's own definition of done: `tick-acs-on-merge` parses the merging PR's own status table to tick the linked issue, and `unmet-ac-on-close` skips PR-driven closes, so **a slice that declares itself met is what closes the epic** (`vfy_01KYNVJ4VG90G26SZSYPXF05KY`, cited at `docs/doctrine/agent-operating-doctrine.md:225`).

## How it was detected

`not recorded` in either source. Both narrate the state discovered (the epic green, the act impossible) without recording the route by which it was discovered.

Two prior escalations are recorded in the same law's incident block and did not stop it:

- 2026-07-25, three days earlier: `feedback_built_not_wired_into_behavior`, escalated that day, requiring handoffs to lead with mission-level readiness and banning "end-to-end" unless the end is customer-visible. Law 9 records that this escalation "did not stop the recurrence three days later".
- 2026-06-30: `feedback_verify_operator_runtime_not_config`, a runbook claiming a cron fires that had never run, which the law names as the same gap between config and adoption.

## Timeline as recorded

| Time (UTC) | Event                                                                              | Source                                                         |
| ---------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| 2026-06-30 | Prior incident of the same class: a runbook claims a cron fires that has never run | `docs/doctrine/agent-operating-doctrine.md:216-217`            |
| 2026-07-25 | Escalation `feedback_built_not_wired_into_behavior`, raised that day               | `docs/doctrine/agent-operating-doctrine.md:214-215`            |
| 2026-07-28 | Four PRs merged, epic closes green, the Named Administrator's act is not possible  | `docs/doctrine/agent-operating-doctrine.md:212-213`, CLAUDE.md |

Ordering within 2026-07-28 is `not recorded`. The four PR numbers are not enumerated in either source and are deliberately not guessed here.

## What changed to prevent recurrence

**Landed.** Law 9 was written into the doctrine registry at gate tier, with a deliberately narrow merge gate: `.github/workflows/runtime-ac-proof.yml` plus `scripts/runtime-ac-proof.mjs` block a PR that marks a `(runtime)` acceptance criterion met without a `crane_verify` id in its Evidence column. Repo-layer criteria still take a `file:line`, because that is the right evidence for code. The gate exists precisely because the AC machinery would otherwise certify the author's own definition of done.

**Landed, process side.** The `/wired` contract (`docs/doctrine/wired-contract.md`, invoked before planning any work whose effect is observable outside the repo) produces the act as a sentence, the terminal seam, the gate chain enumerated **backwards** from that seam, and a feasibility probe that escalates unclosable gates before the closable ones get built. Backwards enumeration is the load-bearing detail: forward enumeration only ever produces the artifacts already planned, and the gates that kill features (adoption, roles, secrets, transport) are the ones that are not code.

**Landed, plan-time.** `.claude/hooks/plan-premise-gate.mjs` extends the same idea to plan time, blocking exit from plan mode without an evidenced premises table.

**Open.** Nothing in either source claims this class is finished. Law 9's escalation field reads `none pending`, which means no escalation is outstanding, not that the class is closed.

## Shadow-firm scenario

Not applicable as a seat-level adversarial scenario: the failure is in the delivery process, not in the running Operator, so the shadow firm cannot replay it. Its analogue is the merge gate, which is the continuous instrument for this class.

## Ladder consequence

None at the time. Retrospectively, this incident is the reason the ladder in `docs/runbooks/operator/enable-gate-checklist.md` requires an **artifact** per rung rather than a report: "we shipped the four PRs" is exactly the claim that was true here and still left the client unable to act.

## Not recorded

- The four PR numbers, and which one carried the "Next slices" sentence.
- Who discovered the gap and by what route.
- Whether any client or Named Administrator attempted the act before it was found.
- Any detection-to-resolution interval. Neither source states one and none is computable from what they do state.
