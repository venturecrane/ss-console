# The Reachability Contract (Law 9)

**Run `/wired`.** The method is an enterprise skill, canonical in crane-console (`.agents/skills/wired/`), synced into this repo's gitignored `.claude/commands/` on every `crane ss` launch. This page carries only what is specific to this venture: the layer list the gate chain is built from, and the merge gate that enforces it here.

The skill produces the contract. The act as one sentence, the terminal seam, the gate chain enumerated backwards from that seam, a feasibility probe that escalates unclosable gates before the closable ones get built, and layer-tagged acceptance criteria that close against observations rather than artifacts. Run it on the ask, before the plan and before `/critique`.

Do not re-derive the method here. If the skill and this page disagree, the skill wins, and the disagreement is a bug to fix in one PR.

## Why it exists

The 2026-07-28 entitlement-control incident. Four PRs, each individually honest, each defining "done" as the artifact it added. One of them wrote "Next slices, unbuilt and not implied here." Nobody lied. The artifacts summed to less than the feature, the epic closed green, and a real client could not perform the act.

A definition problem, not a diligence problem. More care would not have caught it, because each PR met its own definition.

Three terms, used precisely:

- **Built** - the code exists and its own tests pass. The weakest of the three and the easiest to mistake for done, because it produces the most visible evidence.
- **Wired** - every gate between a real client's finger and the effect is open on the deployment that client uses. Not "would work once configured." Configured. Secrets and config authoring are part of the deliverable, not prerequisites belonging to someone else.
- **Tested** - someone performed the act as the client, on the real seat, and observed the far end change. A green unit test against a fake token is not this.

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

`.github/workflows/runtime-ac-proof.yml` blocks a PR that marks a `(runtime)`-tagged acceptance criterion `met` without a `crane_verify` ID in the Evidence column. The ID is resolved against the verify ledger (crane-context `GET /verify/lookup`, with the repo's `CRANE_RELAY_KEY`): it must exist and be a `live_state` or `fresh_process` observation, and a ledger the run cannot reach fails the check rather than passing it. The check is a required context in the main ruleset since 2026-09-25, so it blocks the merge rather than annotating it. `(repo)` ACs still take a file:line, because that is the right evidence for code. Parser: `scripts/runtime-ac-proof.mjs`, tested in `tests/runtime-ac-proof.test.ts`.

It exists because the acceptance-criteria machinery otherwise certifies the author's own definition of done. `tick-acs-on-merge` parses the merging PR's own status table to tick the linked issue, and `unmet-ac-on-close` skips PR-driven closes, so a slice that declares itself met is what closes the epic (`vfy_01KYNVJ4VG90G26SZSYPXF05KY`).

Proven in both directions on the PR that introduced it: run 30417988320 failed #2045 with two runtime ACs claimed via file:line, run 30418036850 passed after the Evidence column carried real IDs (`vfy_01KYNWNJFQTX6JRSGPVYVGSHK6`).
