# The Reachability Contract (Law 9)

**Run `/wired`.** The method is SMD's own skill, `.claude/skills/wired/SKILL.md`, forked from crane-console on 2026-11-09 (crane's copy is still synced into the gitignored `.claude/commands/` on every `crane ss` launch, and the project skill wins over it). This page carries only what is specific to this venture: the layer list the gate chain is built from, and the merge gate that enforces it here.

The skill produces the contract. The act as one sentence, the terminal seam, the gate chain enumerated backwards from that seam, a feasibility probe that escalates unclosable gates before the closable ones get built, and layer-tagged acceptance criteria that close against observations rather than artifacts. Run it on the ask, before the plan and before `/critique`.

Do not re-derive the method here. The skill and this page carry the same READY definition, word for word, and `tests/smd-session-skills.test.ts` fails if they drift apart. Neither one wins a disagreement: a disagreement is a bug, fixed in both in one PR.

## Why it exists

The 2026-07-28 entitlement-control incident. Four PRs, each individually honest, each defining "done" as the artifact it added. One of them wrote "Next slices, unbuilt and not implied here." Nobody lied. The artifacts summed to less than the feature, the epic closed green, and a real client could not perform the act.

A definition problem, not a diligence problem. More care would not have caught it, because each PR met its own definition.

Three terms, used precisely:

- **Built** - the code exists and its own tests pass. The weakest of the three and the easiest to mistake for done, because it produces the most visible evidence.
- **Wired** - every gate between a real client's finger and the effect is open on the deployment that client uses. Not "would work once configured." Configured. Secrets and config authoring are part of the deliverable, not prerequisites belonging to someone else.
- **READY** - all five hold, each with evidence (a probe output, a `file:line`, or a `vfy_` id): (1) built and merged, its own tests passing; (2) rehearsed on real saved inputs, through the same code path the seat runs, against an answer key, at $0; (3) running on the seat, which reports the release that contains it; (4) ready to fire, every gate between the client's email and the job (config, secrets, switches, firm files) open on that seat by a probe of the seat; (5) watched by code, not by a session: the hourly work-health triage on smd-ops (`operator/ops-machine/bin/ops-triage`, `operator/bin/work-health.py`) reads job outcomes and flags a run that failed, held or stalled. A green unit test against a fake token is none of these.

When all five hold, the session writes its handoff and closes, however long it is until the first real request. A session never stays open waiting for a real request; the first real run is watched by the hourly triage. READY replaced "Tested" (the act performed as the client on the real seat) on 2026-11-09 (CLAUDE.md, "Done means the client can do it"; doctrine Law 9).

## Where state lives in this venture

The gate chain is built by asking which of these layers the change must reach. This is the same enumeration as CLAUDE.md's "Gone means gone", which proves absence at each layer on removal. This is its positive twin.

| Layer                        | Runtime? | Typical gates                                |
| ---------------------------- | -------- | -------------------------------------------- |
| git (source, fixtures, docs) | repo     | code exists, tests pass                      |
| D1 projections               | runtime  | row written, `customer_configs` re-projected |
| R2 (skills, vaults, config)  | runtime  | object present at the key the seat reads     |
| Fly volume (`/opt/data`)     | runtime  | profile home, cron store, token on disk      |
| Running Machine              | runtime  | env loaded, config adopted, behavior changed |
| Monitoring                   | runtime  | heartbeat field, alert sink, Sentry          |
| External records             | runtime  | GitHub, mailbox, calendar, vendor dashboard  |

A layer that survives a redeploy is exactly the layer a merge cannot reach. Those are the rows that kill features, and `customer_configs` is the venture's standing example: it re-syncs only on `operator/customers/**` changes, so a field added anywhere else is live in git and absent from the projection.

## The merge gate

`.github/workflows/runtime-ac-proof.yml` blocks a PR that marks a `(runtime)`-tagged acceptance criterion `met` without a `crane_verify` ID in the Evidence column, and since 2026-11-09 one that leaves it anything but `met`, `deferred` or `n/a`. A deferral names who switches it on and by when, anywhere in the row: `owner: <who>; enforce_by: YYYY-MM-DD`, the date not already passed. "not met", "unmet", "pending" and "not yet" fail; #3852 merged with two runtime rows "not met" while the check read only a section headed "## Acceptance criteria status". It now reads every table with a Status column, under any heading (accepted form: `docs/handbook/building-the-platform.md`, "The runtime AC table"). The ID is resolved against the verify ledger (crane-context `GET /verify/lookup`, with the repo's `CRANE_RELAY_KEY`): it must exist and be a `live_state` or `fresh_process` observation, and a ledger the run cannot reach fails the check rather than passing it. The check is a required context in the main ruleset since 2026-09-25, so it blocks the merge rather than annotating it. `(repo)` ACs still take a file:line, because that is the right evidence for code. Parser: `scripts/runtime-ac-proof.mjs`, tested in `tests/runtime-ac-proof.test.ts`.

It exists because the acceptance-criteria machinery otherwise certifies the author's own definition of done. `tick-acs-on-merge` parses the merging PR's own status table to tick the linked issue, and `unmet-ac-on-close` skips PR-driven closes, so a slice that declares itself met is what closes the epic (`vfy_01KYNVJ4VG90G26SZSYPXF05KY`).

Proven in both directions on the PR that introduced it: run 30417988320 failed #2045 with two runtime ACs claimed via file:line, run 30418036850 passed after the Evidence column carried real IDs (`vfy_01KYNWNJFQTX6JRSGPVYVGSHK6`).
