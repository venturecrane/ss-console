---
title: Seats declare their purpose; a client's twin gates its releases
date: 2026-11-10
status: accepted
captain: Scott Durgan
supersedes: the ring ordering of docs/adr/0024-hermes-consumption-and-update-cadence.md (amendment 2026-10-19, item 3)
amends: docs/adr/0104-logic-in-code-six-mechanisms.md, docs/adr/0096-release-controller-and-fleet-credentials.md
related-adr: docs/adr/0093-seat-policy-profiles.md, docs/adr/0053-author-built-mcp-connectors-per-customer-installed.md, docs/adr/0024-hermes-consumption-and-update-cadence.md
enforced_by:
  - operator/bin/tests/test_fleet_twin_gate.py
  - tests/seat-inventory.test.ts
  - operator/bin/tests/test_reprovision_staging.py
  - tests/plan-premise-gate.test.ts
---

# ADR 0107: Seats declare their purpose; a client's twin gates its releases

- **Status:** Accepted (2026-11-10)
- **Decider:** Captain (the winning line below); mechanism by the agent
- **Supersedes:** the ring order written into ADR 0024's 2026-10-19 amendment, item 3 (`0 smd-staging, 1 pilot-smokeball, 2 scott brokery captain-mark, 3 ashton-price`). The order has changed twice since; ring 0 is now derived from purpose (below), and no ring order is written into an ADR again.
- **Amends:** ADR 0104's proof ladder (the pilot no longer gates anything; drafting is not proven on the pilot) and ADR 0096's controller (a client seat now waits for its twin on each release, not only for the ring before it).

## The winning line

"Every change is rehearsed on an exact twin of a client's seat before that client sees it, and seats are renamed or added by editing one declaration, not code."

## Context

Three things were keyed by seat slug or by ring arithmetic instead of by what a seat is for.

1. **The release order trusted ring numbers, not proof.** Ring 0 was "staging" because `smd-staging` authored `ring: 0`. The controller (ADR 0096) moves a ring once the ring before it baked, and it counts a seat done on a NEWER release as done on an older one (`tick.overlay_newer`), so a client seat could take a release its staging seat never ran. On 2026-10-30 releases `9bd7caa3` and `f835d1d9` reached `ashton-price` with no `smd-staging` rollout row for either. A laptop `fleet release <id> --only ashton-price` had no check at all, and a hold on staging let a full run go straight on to the client.
2. **"Which seats are clients" was ring arithmetic.** The admin Merge-to-seat card took every ring strictly between the lowest and the highest as a client ring (`src/lib/admin/merge-to-seat.ts`), so `brokery` and `captain-mark`, real clients in the last ring, were never measured, and any reordering would have silently changed the answer.
3. **Slugs were written into behavior.** The plan premise gate named `ashton-price`, `pilot-smokeball` and `smd-staging` in two regexes; the boot harness derived its fixture from `"smd-staging"` and its large-store case from `"pilot-smokeball"`; `reprovision-staging.sh` hardcoded its slug; work-health had `no_reply_minutes_by_seat: {smd-staging: 5}`; the connector materializer contract exempted `"smd-staging"` by name; `fleet selftest --plant` refused every seat but `"smd-staging"`. Renaming `smd-staging` to `ap-staging`, the next step, would have needed every one of those edited, and missing one would fail open.

## Decision

### 1. Every seat declares its purpose

Each `operator/customers/<slug>/seat.yaml` (console-side only, never read on the seat) authors a required `purpose`:

| purpose        | meaning                                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------------------- |
| `client`       | holds a client's work and records (`ashton-price`, `ashton-price-marketing`, `brokery`, `captain-mark`) |
| `release-twin` | an exact copy of ONE client seat that takes every release first; names that client in `twin_of`         |
| `demo`         | shows the product on SMD's own test data (`pilot-smokeball`); gates nothing                             |
| `internal`     | SMD's own work (`scott`, `smd-guide`)                                                                   |

It is named `purpose`, not `role`: `customer.yaml` `seat.role` already selects a role package (ADR 0093). The loader (`scripts/lib/seat-inventory.ts`) refuses a missing or unknown purpose, a `twin_of` on any purpose but `release-twin`, a twin whose `twin_of` is not a `client` seat, and a second twin for one client. A release twin is ring 0, derived: it authors no other ring, and no other seat may author ring 0. Today the one twin is `smd-staging`, `twin_of: ashton-price`.

`npm run gen:seat-registries` renders the declaration for every reader: `src/lib/operator/seat-purposes.generated.ts` (TypeScript), `scripts/lib/seat-purposes.generated.mjs` (the hooks), `operator/fleet/seats.yaml` (Python, through `operator/bin/lib/seat_purpose.py`), and a `twins:` block in `operator/fleet/rings.yaml` (the release tool). `tests/seat-inventory.test.ts` fails on any stale byte.

### 2. A client's twin gates its releases

A `client` seat with a declared twin takes a release only after its twin's OWN rollout of that release is `done`:

- **The controller** (`operator/bin/lib/fleet/tick.py`, `twin_deferrals`): such a client seat is not pending, named as waiting on its twin, until the twin's row for this exact release is done. It reads the release's own rows, never the newer-release overlay, so a twin done only on a newer release does not count. A twin with no row, held, stalled or failed keeps its client waiting; a twin in no ring keeps it waiting for good. Fail closed.
- **A laptop run** (`operator/bin/lib/fleet/waves.py`, `_twin_waiting`): `--only <client>` is refused, naming the twin, until the twin is done on that release; a full run records the client `waiting_twin` (nothing written, nothing halted, the release left incomplete). A hotfix for a client seat goes to its twin first: `--only <twin>`, then `--only <client>`.
- **Jobs bundles** keep the gate: `bundles.authoring_rings` carries each ring's twins.

`operator/bin/tests/test_fleet_twin_gate.py` pins every path, including the 2026-10-30 shape, and its kill tests remove the gate and assert the same scenarios then reach the client seat.

### 3. Slug-keyed behavior becomes a purpose lookup

| Was                                                           | Now                                                                                                                                                      |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| premise gate `SEAT_RE` / `REHEARSAL_RE` slug lists            | built from the inventory: any seat lands a plan on a seat; a twin or demo seat is a rehearsal                                                            |
| boot harness `SOURCE_SLUG = "smd-staging"`                    | the release twin, de-slugged with its own slug                                                                                                           |
| boot harness large-store case `"pilot-smokeball"`             | the demo seat (derived: it is the one non-client seat with a real-sized store)                                                                           |
| `reprovision-staging.sh` hardcoded slug                       | takes the twin's slug (default: the one twin declared); refuses any other purpose; isolates both the twin's and its client's customer-owned secret names |
| work-health `no_reply_minutes_by_seat: {smd-staging: 5}`      | `no_reply_minutes_by_purpose: {release-twin: 5}`                                                                                                         |
| materializer contract `exemptCustomers: [..., "smd-staging"]` | `exemptPurposes: [release-twin, demo]`; `exemptCustomers` holds template presets only                                                                    |
| `fleet selftest --plant` refuses all but `"smd-staging"`      | refuses all but a release twin                                                                                                                           |
| Merge-to-seat client seats by ring arithmetic                 | provisioned seats with `purpose: client`                                                                                                                 |

`workers/fleet-alerts` `SMOKEBALL_STAGING_SEATS` stays as data with its own test.

## The stated gap

The twin is not yet an exact copy. It is still named `smd-staging`, and it binds no Smokeball connector, so a change to a Smokeball read or write path is not exercised on it before `ashton-price` takes it. A later change renames it `ap-staging` (one seat.yaml rename and `npm run gen:seat-registries`, the point of this record) and binds the connector against a test firm; `pilot-smokeball` becomes a demo seat in name as well as purpose. Until then, a Smokeball-path change is rehearsed at $0 in the client's ops workspace against an answer key (Law 18), and the twin gate still guarantees the image, skills and bundle booted green on the twin before the client.

**Closed 2026-11-10.** `ap-staging` is the release twin of `ashton-price`: its `customer.yaml` equals the client's except the paths its `twin.yaml` declares (held by `tests/twin-drift.test.ts`), it binds SMD's Smokeball staging tenant, and its self-test runs the client's 101 checks (vfy_01M73GY7D0R92GM5J0VBCA0VE6). `smd-staging` is `internal` in the last ring until it is decommissioned. The rename needed no code change, which is what this record set out to make true.

## Consequences

- Renaming a seat or adding one is an edit to its `seat.yaml` and a regeneration; no Python, TypeScript, hook or contract names it.
- No release reaches a client seat its twin has not taken. An urgent client fix costs one twin deploy first (minutes); there is no override flag.
- `brokery` and `captain-mark` now count in Merge-to-seat, which measures to the last provisioned client seat.
- A client without a twin (every client but `ashton-price` today) is gated by ring order alone, as before. Adding its twin is one new seat with `purpose: release-twin`, `twin_of: <client>`.
