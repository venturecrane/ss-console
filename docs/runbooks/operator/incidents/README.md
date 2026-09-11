# Post-incident notes

The record `docs/handbook/incident-response.md` step 5 has always mandated and nobody had written. Six qualifying incidents had occurred by 2026-08-17 with zero notes on disk; the loop from incident to permanent immune-system change lived in issue bodies and one agent's memory (issue [#2391](https://github.com/venturecrane/ss-console/issues/2391)).

The index below is the register. It is guarded by `tests/incidents-register.test.ts`, added under [ss#2547](https://github.com/venturecrane/ss-console/issues/2547) after this table was found listing six of the seven notes then on disk: an index that is quietly wrong answers "has this happened before?" with a confident no.

## The contract

- **One note per incident**, filename `YYYY-MM-DD-short-slug.md`, dated by the day the incident began (not the day it was found and not the day it was filed; when those differ, the note says so).
- **Every fact cites its source** inline: an issue number, an audit section, a doctrine law, a file path, or a `vfy_` id. A fact with no source does not go in the note.
- **Where a source does not state something, the note says `not recorded`.** Detection-to-resolution time in particular is usually not recorded, because the timestamps that would establish it were never captured. Writing a plausible number instead is the fabrication class these notes exist to document.
- **Backfilled notes say they are backfilled**, and reconstruct nothing that the sources do not state.
- A note is not a status page. It closes nothing and it does not track a fix; the linked issue does that. What the note owns is the causal chain and the answer to "what changed so that this class cannot recur silently".
- **Every note carries a class**, from the fixed set below. Fixed rather than free text because the column exists to answer "has this shape happened before?", and a vocabulary invented per note answers that question differently every time.
  - `gate-regression` - a change that closed one failure muted a routine on the same path.
  - `built-not-wired` - the artifacts existed and passed their own tests; the act still could not be performed on the deployment that matters (Law 9).
  - `gone-not-gone` - a removal reported complete while a runtime layer kept the artifact.
  - `identity` - two things the system treated as the same thing, or as different things, wrongly.
  - `other` - none of the above. Used sparingly; a second `other` of the same shape is the signal to name a new class.
- **A new refusal gate on an outbound path is done when its refusal shows in `send_refusals` on the heartbeat and the pager has been proven on a seat, not when its unit tests pass.** Three times (2026-08-04, 2026-08-13, 2026-08-19) a correct new gate silenced the one routine whose job is to reach a human, and each time the refusal was an audit row nobody was watching. A gate that can refuse and a human who can learn that it refused are one deliverable, not two.

## Template

`_TEMPLATE.md`. Copy it, keep every heading, and delete no section; an empty section is filled with `not recorded` or `none`, which is information.

## How a note connects to the rest of the loop

An incident produces four things, and the note is the one that outlives the others:

1. A **paused seat and a demoted routine**, per the pre-committed demotion rule in `docs/runbooks/operator/enable-gate-checklist.md`.
2. A **root-cause change** with the evidence its own acceptance criteria demand.
3. A **shadow-firm scenario** replaying the incident, shown to fail against the unfixed state before it is trusted (`#2389`).
4. This **note**, which is what a successor reads when the same shape appears again.

## The notes

| Note                                               | Incident date                       | Class             | What it is                                                                                                                                                                                                                                                                               |
| -------------------------------------------------- | ----------------------------------- | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `2026-07-02-retired-persona-name-removal.md`       | 2026-07-02 to 2026-07-26            | `gone-not-gone`   | A retirement reported complete four times while runtime layers kept the artifact. Source: CLAUDE.md "Gone means gone".                                                                                                                                                                   |
| `2026-07-28-entitlement-control.md`                | 2026-07-28                          | `built-not-wired` | Four honest PRs summing to less than the feature; the client could not perform the act. Source: Law 9.                                                                                                                                                                                   |
| `2026-07-31-escalation-ledger-item-identity.md`    | 2026-07-31                          | `identity`        | The seven-day snooze promise fails because the item key hashes model-composed text. Sources: audit §3.11, [#2151](https://github.com/venturecrane/ss-console/issues/2151).                                                                                                               |
| `2026-08-11-unaudited-send.md`                     | 2026-08-11                          | `other`           | A message left a client seat's inbox with no audit row. Source: [#2258](https://github.com/venturecrane/ss-console/issues/2258), including its own retraction.                                                                                                                           |
| `2026-08-13-silent-filing.md`                      | 2026-08-13                          | `gate-regression` | The demand letter was filed correctly and the reply that would have said so was held. Source: [#2367](https://github.com/venturecrane/ss-console/issues/2367).                                                                                                                           |
| `2026-08-13-send-tool-dispatch-shape.md`           | 2026-08-13                          | `gate-regression` | The Operator's only send tool raised on every call. Source: [#2348](https://github.com/venturecrane/ss-console/issues/2348).                                                                                                                                                             |
| `2026-08-19-gate-muted-escalator.md`               | 2026-08-19                          | `gate-regression` | A court date seven days out was escalated five times and reached nobody. Source: [ss#2547](https://github.com/venturecrane/ss-console/issues/2547).                                                                                                                                      |
| `2026-08-20-gateway-wedge-no-restart.md`           | 2026-08-20                          | `built-not-wired` | The Operator went silent for 33 minutes; the watchdog fired, nothing restarted it, and every instrument stayed green. Source: [ss#2488](https://github.com/venturecrane/ss-console/issues/2488).                                                                                         |
| `2026-08-24-degraded-digest-and-inert-handoff.md`  | 2026-08-24 (defect from 2026-08-22) | `gate-regression` | The deadline digest arrived with every matter unnamed, the refusal text itself offered the degraded path, and the merged ss#2547 seeding had been running dead for two days. Source: vfy_01M0THACG928CA9ANNVECFBW95, [ss#2390](https://github.com/venturecrane/ss-console/issues/2390).  |
| `2026-09-01-gateway-startup-watchdog-collision.md` | 2026-09-01                          | `built-not-wired` | The pilot seat restarted itself every 15 minutes for 2.5 hours with nothing broken: a 4-minute startup crawl against a 2-minute in-process watchdog budget. Both instruments that should have paged were running, and neither did. Source: the captured thread dump; ss#2488 parts 1-2.  |
| `2026-09-01-sticky-stop-raw-sqlite-bypass.md`      | 2026-09-01                          | `other`           | The breaker tripped on real mail (a 404 burst opened the derivative unreachable circuit) and the resume was raw sqlite on the seat while the built clear surface sat unused: nothing in the responder's path named it. Source: vfy_01M1FB52Y7PEW0HSQVZB8HV2PW; ss#2680; the note itself. |
