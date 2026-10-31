---
title: One Operator - core plus the capabilities a seat authors
date: 2026-10-31
status: accepted
captain: Scott Durgan
related-adr: docs/adr/0037-operator-thesis.md, docs/adr/0067-hosted-agent-self-serve-sku.md, docs/adr/0084-agent-native-applications-surfaces-on-a-shared-seat.md, docs/adr/0093-seat-policy-profiles.md, docs/adr/0096-release-controller-and-fleet-credentials.md
enforced_by:
  - operator/hermes-smd-overlay/tests/test_capabilities.py
  - operator/hermes-smd-overlay/tests/test_capabilities_gateway_equivalence.py
  - operator/templates/tests/test_selftest.py
  - operator/runners/medchron/tests/test_authored_lanes.py
---

# ADR 0100: One Operator - core plus the capabilities a seat authors

- **Status:** Accepted (2026-10-31)
- **Decider:** Captain (product model: "no real distinction between an operator and a hosted agent"; "think from a product perspective"); mechanism by the agent
- **Amends:** ADR 0067 and ADR 0084 (Operator and Hosted Agent are one product; the difference is an offering preset), ADR 0096 (what a release ships, and to whom)

## Context

On 2026-10-31 captain-mark, a 1 GB seat, stopped heartbeating for 24 minutes. All three 1 GB seats had 15-60 MB free. Each one ran the law firm's job runner (45 MB), fetched four law lanes' firm inputs at boot with retries (at least 60 s of sleeps), offered about twenty legal tools on every model call, and served Smokeball webhook routes, though none of them authors any of it. Separately, every merge rebuilt the one seat image and restarted every seat: #3651, a print-out fix one client uses, restarted six seats, 28 minutes for ring 2. Skills releases waited about 15 hours each behind an unrelated image (10-28..30).

The cause was not one bad gate. The product had no model of what a seat is for, so everything loaded everywhere and every change shipped everywhere. Work was built for the first client, which was right, and then pushed to every seat, which was not.

## Decision

1. **One Operator.** "Hosted Agent" and "Operator" have different origin stories and the same runtime. Nothing at runtime branches on `seat.product`, `vertical` or a customer slug. What differs is an **offering preset**: a starting template and commercial terms (ADR 0067's price stays; pricing is the Captain's).
2. **Core** is what every Operator is: the agent, the trust layer, voice, memory, the broker, the appliers, monitoring, the job-runner service and connector hosting. Core defaults are neutral. The identifier floors (ADR 0093) stay universal by design: a floor that a misconfiguration could lose is not a floor.
3. **A capability** is a unit of work a seat can be given: it owns its skills, its job lane, its connector, its config blocks, its tools and its checks. Today's capabilities happen to be the law firm's job lanes and the Smokeball connector, plus the couples, marketing and real-estate skills.
4. **Authored is derived, never a flag.** A seat has a job lane when a persona enables the lane's skill; a connector capability when it binds that connector's backend. One predicate, `operator/hermes-smd-overlay/shared/capabilities.py`, is asked by boot, the gateway plugins, translate, the webhook gate, the self-test and the release controller, so what runs, what the model is offered and what is checked cannot drift apart. An unreadable config is "cannot evaluate", never "nothing authored": boot then starts every lane and the self-test runs every check, and the webhook gate keeps serving the Smokeball routes. The one exception is what the model is offered: a gateway that cannot read its config registers no lane tool and says so in one log line, because offering a tool is an act and a seat that cannot read its config is already failing its self-test.
5. **A preset** is a named starting set of capabilities and defaults (a market: PI law firm, real estate, couples; or an offering: self-serve). Presets are for onboarding and selling. `operator/verticals/*` are presets.
6. **A bundle** is the delivery unit for capability code that is not a skill, grouped by the runtime it shares. Today there is one, `jobs`: the job runner and its lanes plus the Smokeball connector, which share a library. A bundle is published separately from the image and goes only to seats that author a capability in it.

## Release rule (one rule for anything that ships beside the image)

An artifact built at commit S (a skills release or a bundle) may go to a seat whose image commit descends from **base(S)**: the last commit at or before S that touched an image path. The artifact was written against the core as of S, and the core as of S is the core as of base(S). This replaces "the image must be built at or after S", which forced every skills merge to also build and roll an image, and it covers hotfix images by ancestry (#3657). Paths that ship as their own artifact are not image paths, so a change to them builds no image and restarts no seat.

## Contracts (for the build)

- **Bundle object:** `fleet/bundles/<name>/<version>.tar.zst` and its manifest `fleet/bundles/<name>/<version>.json` = `{name, version, sha256, size, ss_console_sha, base_image_ref, python}`. Version `YYYYMMDDHHMM-<sha8>`, the skills shape.
- **Seat pointer:** `vaults/<slug>/bundles/<name>-pointer.json` = `{version, manifest_key, manifest_sha256}`, the skills pointer shape.
- **Seat install:** root-owned under `/opt/data/.smd-root/bundles/<name>/<version>/` (the sweep prunes `.smd-root`), import-checked with the bundle's own Python, then bind-mounted over the image paths it replaces (`/opt/medchron`, `/opt/connectors/smokeball`). No mount means the image copy runs: that is the fallback, and unmounting is the rollback. The previous version stays on disk.
- **Swap:** the release controller writes the seat's pointer, then restarts the Machine behind the same busy gate an image release uses (no job is interrupted), and reads the heartbeat's `bundles` back; a seat that does not report the new version within the window is rolled back to its previous pointer. Rollout is off until the repo variable `BUNDLE_RELEASE_ENABLED` is set; one seat can take a bundle by hand (`release-tick` with `artifact: bundle` and `seat`).
- **Heartbeat:** `bundles: {<name>: <version>}`, stored as `fleet_status.bundles_json`.
- **Release record:** `fleet_releases.kind = 'bundle'` with `artifact_name` and `artifact_version`; a bundle tick walks the rings filtered to the seats that author the bundle.

## Consequences

- A seat that authors no job lane runs no runner, no lane fetch and no lane check (W1, #3656), and its model is offered no legal tool and served no Smokeball route (#3655).
- The law runner and Smokeball connector leave the core image only after a real ashton-price job has completed on the bundle; until then the image keeps them as the fallback.
- Config blocks belong to the capability that reads them; the validator refuses a block on a seat that does not author its capability.
- The four root poll loops (config, spec and skills appliers, establishment intake) run in one root process (`root_pollers`), about 170 MB back on a 1 GB seat.
- The remaining Operator vs Hosted Agent distinctions (templates, portal routes, admin labels, docs) are removed after this build.

## Rejected

- **A `packs:` or `addons:` flag as the source of truth.** A flag can disagree with what the seat actually enables; deriving from enabled skills and bound connectors cannot.
- **One image per vertical.** It multiplies images by verticals and still restarts every seat of that vertical on any change; bundles ship per capability runtime.
- **Making the identifier floors per capability.** It would let a misconfigured seat lose them (ADR 0093).
