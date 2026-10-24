---
title: Customer configuration as built
date: 2026-10-22
status: accepted
captain: Scott Durgan
supersedes: 0012-customer-yaml-storage.md
related-adr: docs/adr/0044-r2-authoritative-live-reconfig.md, docs/adr/0019-customer-yaml-to-profile-config-translation.md, docs/adr/0026-config-surface-is-a-security-boundary.md, docs/adr/0083-authorship-model-output-classes.md
enforced_by:
  - tests/customer-yaml-validator.test.ts
  - tests/customer-yaml-parity-contract.test.ts
  - tests/config-publish-guards.test.ts
  - tests/config-reconcile.test.ts
  - tests/r2-config-reconcile.test.ts
  - tests/customer-config.test.ts
  - tests/authority-write.test.ts
  - scripts/validate-customer-yaml.ts
  - scripts/ci-sync-customer-configs.sh
  - scripts/ci-publish-customer-configs.sh
  - scripts/ci-reconcile-customer-configs.sh
  - scripts/ci-reconcile-r2-customer-configs.sh
  - scripts/compile-seat-spec.ts
  - scripts/lib/seat-spec.ts
  - scripts/lib/seat-spec-compile.ts
  - scripts/lib/seat-spec-inputs.sh
  - tests/compile-seat-spec.test.ts
  - tests/deploy-seat-spec-gate.test.ts
  - operator/bin/lib/stage-seat-spec.sh
  - operator/templates/seat-spec.pub
  - operator/hermes-smd-overlay/config_applier/spec_verifier.py
  - operator/hermes-smd-overlay/shared/spec_check.py
  - workers/fleet-alerts/src/spec.ts
  - .github/workflows/deploy.yml
  - .github/workflows/customer-config-reconcile.yml
  - operator/templates/entrypoint.sh
  - operator/bin/provision-customer.sh
  - operator/bin/decommission-customer.sh
---

# ADR 0094: Customer configuration as built

**Status:** Accepted 2026-10-22. Supersedes [ADR 0012](./0012-customer-yaml-storage.md), whose storage plan was never built as written. Records the partial status of [ADR 0044](./0044-r2-authoritative-live-reconfig.md), whose root config applier has run on every seat since 2026-06-15 even though 0044 was marked reverted in full.

**Why this record exists.** Agents load ADRs as ground truth, and `operator/CLAUDE.md` put 0012 on the load-first list. An architecture review on 2026-10-22 found that 0012 described a different system: a `customer-configs/<slug>.yaml` path, an R2 key under `customers/`, a merge that provisions the Machine, a Cloudflare drift cron paging Sentry, a PR-deletion offboarding, an `smd-config sync` CLI, and a D1 table keyed on `entity_id`. None of those is what runs. This ADR describes what does, with a citation for every claim, so the next agent reasons from the code and not from the plan.

Every `file:line` below is against `origin/main` at `8cb62e6d` for this repo and `3d64eda` for `venturecrane/hermes-smd-overlay`. Overlay paths are prefixed `overlay:`. The amendment that follows changes sections 2, 3 and 5; where they disagree, the amendment is what runs.

## Amendment (Wave 2 W4): one writer, a signed seat spec, shadow verification

**Accepted 2026-10-23** (the architect's call; the Captain ruled engineering placement an agent decision the same day). Workstream W4 of the 2026-10-22 seat-architecture review.

**Why.** Section 3 below records four writers of `vaults/<slug>/customer.yaml`, three of them carrying a copy of the image guard (#3397), and nothing in the path was signed: every check was a sha256 over a channel we trusted. A guard on one writer of a key is a guard on none of them, which is how the nightly reconciler undid the publisher's refusals (Known gap 1).

**What changed.**

1. **One writer.** `scripts/ci-publish-customer-configs.sh`, the deploy job `publish-customer-configs`, is the only automated writer of `vaults/<slug>/customer.yaml`, and the only writer of the two new keys below. On every push to main it publishes the slugs the push changed and converges every other seat whose R2 state differs from what that commit compiles, so a push range that was never processed is healed by the next push. A refusal fails the job for a changed slug and is a warning for any other seat.
2. **The compiled, signed seat spec.** `scripts/compile-seat-spec.ts` (logic in `scripts/lib/seat-spec.ts` and `scripts/lib/seat-spec-compile.ts`) compiles one canonical JSON spec per seat: `schema_version`, `slug`, `compiled_from` (the ss-console commit), the `customer.yaml` key and sha256, the lane-proofs seed reference, each staged lane's firm-input manifest by vault key and sha256 (digests only: firm inputs live in the private engagements repo and in the vault, never in this repo), the skills release reference, and `requires` (`skills`, the image requirements `skill:`/`pin:`/`pre_run:`, the enabled skills' capability `needs`, and per lane the schema module path and digest). It is stored content-addressed at `fleet/specs/<slug>/<sha256>.json`; the pointer `vaults/<slug>/spec-pointer.json` names it and carries an Ed25519 signature over its exact bytes. The private key is a secret of the protected GitHub environment `seat-spec` only, and the publish job names that environment and reads the key only when the repository variable `SEAT_SPEC_ENABLED` is `true` (GitHub would otherwise auto-create the environment unprotected; `tests/deploy-seat-spec-gate.test.ts`); the public key is committed at `operator/templates/seat-spec.pub` and baked root-owned into the image at `/app/seat-spec.pub`; `key_id` is the first 16 hex of sha256 over the raw 32-byte public key. Without the key the publisher still publishes `customer.yaml` and skips the spec with a warning.
3. **The image guard is the compiler.** It refuses a config naming a skill, pin, pre_run or capability need the seat's running image (plus its skills release) lacks, and the spec alone when a staged lane's schema module differs from the one the image's `lane-inputs.json` records. `scripts/lib/seat-image-guard.sh` and `scripts/check-config-against-image.mjs` are gone; `scripts/lib/seat-spec-inputs.sh` resolves what the seat runs now (read-only) for every caller.
4. **The reconciler is read-only.** `scripts/ci-reconcile-r2-customer-configs.sh` never writes R2. It compares each seat's `customer.yaml` bytes and signed spec (present, verifying against the committed key, equal to what HEAD compiles but for `compiled_from`) and pages through its existing `prio:P1` issue path: DRIFT and SPEC on a dated issue, REFUSED on the standing one. `tests/r2-config-reconcile.test.ts` asserts no aws call a run makes writes. Section 5's "self-heals" now describes the publisher's convergence on the next push, not this job.
5. **`operator/bin/sync-customer-yaml.sh` is deleted**, with its test and every reference.
6. **Provisioning uploads through the compiler** (`operator/bin/provision-customer.sh` step 2, `operator/bin/lib/stage-seat-spec.sh`) with `--provisioning`: the requirements are recorded, not checked, because the image that carries them ships in the same run. It signs and publishes the spec only when `SEAT_SPEC_SIGNING_KEY` is present.
7. **Shadow verification on the seat.** The root `config_applier` loop runs `overlay:config_applier/spec_verifier.py`: it fetches the pointer and spec, verifies the digest and the signature with `cryptography` (the Hermes venv's pin) against `/app/seat-spec.pub`, checks `requires` against `/app/image-manifest.json`, the skills release the spec names, the image's real tree and `/app/image-capabilities.json`, and the lane schema modules under `/app/runners/`, and writes `/run/smd-spec/state.json` (0644, by rename). The heartbeat ships it as `spec` (`overlay:shared/spec_check.py`) into `fleet_status.spec_json` (migration 0139), and the fleet-alerts worker pages `spec_unverified` (migration 0140, `workers/fleet-alerts/src/spec.ts`) on a bad signature or digest, an unknown key, a spec for another seat, a missing spec, an unmet requirement, a verifier that stopped reporting, or a seat still holding an older spec than the published pointer 15 minutes after it was written. **`config_applier` still applies `customer.yaml` exactly as section 6 describes**, and `config_drift` is unchanged.

**Not yet.** Applying only verified spec bytes, and `config_drift` comparing spec digests, wait until every seat has reported a verified spec equal to the published one for a full release cycle. Machine size and region, secret values, the image and the section 8 portal overrides stay outside the spec.

## Decision

### 1. Git is the only authoring source

Each seat's configuration is one file, `operator/customers/<slug>/customer.yaml`, edited by pull request. Every automated writer reads that path: the publisher diffs `operator/customers/*/customer.yaml` (`scripts/ci-publish-customer-configs.sh:74-75`), the D1 sync watches the same glob plus the sibling `routine-grid.yaml` (`scripts/ci-sync-customer-configs.sh:40-41`), and provisioning reads `${CUSTOMER_DIR}/customer.yaml` (`operator/bin/provision-customer.sh:123`).

No portal or admin path writes the file or its replicas. The admin authority flip records intent and never mutates `customer_configs` (`tests/authority-write.test.ts:6-9`); the only `INSERT INTO customer_configs` in `src/` and `scripts/` is the projection's generated SQL (`src/lib/portal/customer-config-projection.ts:226`).

Client material does not live here. Dossiers and correspondence are in the private `venturecrane/engagements` repo; `operator/customers/<slug>/` in this repo holds only `customer.yaml` and `routine-grid.yaml` (ADR 0081). The one per-seat config that lives there is the chronology firm config, uploaded by provisioning from `${SS_ENGAGEMENTS_DIR}/operator/customers/<slug>/medchron/firm.yaml` (`operator/bin/provision-customer.sh:246`, `:293-300`).

### 2. Validation: two validators held in parity

- **TypeScript, pre-merge and pre-publish.** `src/lib/operator/customer-yaml/` (`validate()` at `validator.ts:212`), invoked by `scripts/validate-customer-yaml.ts`. Every writer of the R2 object runs it before writing: the publisher (`scripts/ci-publish-customer-configs.sh:243-247`), the R2 reconciler (`scripts/ci-reconcile-r2-customer-configs.sh:283-292`), provisioning (`operator/bin/provision-customer.sh:181-184`) and the manual sync (`operator/bin/sync-customer-yaml.sh:48-51`).
- **Python, on the seat.** `overlay:bootstrap/validate.py` (`validate_customer_yaml` at line 202). The boot fetch runs it on the candidate before adopting it (`operator/templates/entrypoint.sh:66-82`, `:95-104`) and the live applier runs it before every apply (`overlay:config_applier/applier.py:503-510`).
- **Parity.** `tests/customer-yaml-parity-contract.test.ts` classifies a shared fixture manifest with the TS validator and pins a content hash that must equal the overlay's `test_validator_parity_contract.py` (`tests/customer-yaml-parity-contract.test.ts:1-19`, `:38-40`). A one-sided fixture change fails one repo or the other.

### 3. The R2 object a seat boots from, and its four writers (superseded by the amendment: one writer)

The seat reads `s3://${R2_BUCKET_CONFIG}/vaults/<slug>/customer.yaml` (bucket default `smd-customer-config`). Four things write it, all from git, none from a portal:

| Writer                                                                                                                   | When                                                                                                         | Never creates                                                                    | Image guard                                                                  | Proof of write                                    |
| ------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------- |
| `scripts/ci-publish-customer-configs.sh` (deploy job `publish-customer-configs`, `.github/workflows/deploy.yml:164-208`) | every push to main that changes a `customer.yaml` (`deploy.yml:3-5`; `ci-publish-customer-configs.sh:74-75`) | yes: only a 404 skips; any other HEAD failure is an error (`:23-29`, `:249-263`) | **yes** (`:265-288`, `scripts/check-config-against-image.mjs`)               | byte read-back (`:296-314`)                       |
| `scripts/ci-reconcile-r2-customer-configs.sh` (`customer-config-reconcile.yml` job `reconcile-r2`, `:164-253`)           | daily, `41 9 * * *` (`.github/workflows/customer-config-reconcile.yml:37`)                                   | yes (`:40-45`)                                                                   | **yes**, since #3397 (`scripts/lib/seat-image-guard.sh`)                     | byte read-back (`:304`)                           |
| `operator/bin/provision-customer.sh` step 2, via `operator/bin/reprovision.sh`                                           | by hand, per seat (`reprovision.sh:24`, `:53`)                                                               | n/a: this is the creator                                                         | no; it deploys the image in the same run (`provision-customer.sh:1158-1163`) | none beyond the upload's exit status (`:231-238`) |
| `operator/bin/sync-customer-yaml.sh`                                                                                     | by hand, after a merge (`:5-14`)                                                                             | yes, since #3397 (before it, uploaded unconditionally, `:54-60`)                 | **yes**, since #3397 (`scripts/lib/seat-image-guard.sh`)                     | byte read-back, since #3397                       |

Every scripted writer is confined to that one key. The publisher and reconciler re-check the assembled key against a whole-string pattern before writing (`scripts/ci-publish-customer-configs.sh:123-129`, `:234-235`), so they can never touch the portal-owned `output-classes.json` beside it (`:30-37`).

### 4. The D1 projection the portal reads

`customer_configs` (created by migration 0039) is keyed on `customer_slug` since migration 0090, which moved the primary key off `entity_id` so one client can own several seats (`migrations/0090_customer_configs_slug_pk.sql:1-11`, `:29-31`). Instance-addressed reads use `getCustomerConfigBySlug` (`src/lib/portal/customer-config.ts:354-365`); the entity-keyed `getCustomerConfig` is documented as legacy and returns an arbitrary row for a multi-seat client (`:325-342`).

- **Writer.** `scripts/project-customer-config.ts` renders idempotent SQL from the canonical mapper (`src/lib/portal/customer-config-projection.ts:182-247`). The upsert never updates `entity_id`, so a routine sync cannot move a seat to another client (`:211-219`). It also appends a `customer_config_history` row stamped with the git SHA (`:231-241`).
- **On merge.** The deploy job `sync-customer-configs` (`.github/workflows/deploy.yml:121-156`) re-projects every changed slug that already has a row and proves the write by reading back the stamped `git_sha` (`scripts/ci-sync-customer-configs.sh:110-129`). A slug with no row is skipped with a warning: the first projection binds a config to a client and is run by hand (`:13-21`, `:100-102`).
- **Nightly.** `scripts/ci-reconcile-customer-configs.sh` compares every row's stamped SHA against the commit main names for that file, re-projects drift, and alerts on a row whose stamped SHA is no longer an ancestor of main (`:24-35`). It never creates or drops a row (`:37-44`). Its known blind spot is a grid-only change, which leaves the stamped SHA unchanged (`:46-53`).

### 5. Drift is reconciled by a GitHub Action that self-heals and opens an issue

There is no Cloudflare drift cron and no Sentry event. `.github/workflows/customer-config-reconcile.yml` runs daily at `41 9 * * *` (`:37`) with two jobs, `reconcile` (D1) and `reconcile-r2` (R2) (`:65`, `:164`). Each repairs what it finds and then opens a `prio:P1` issue so the duration of the drift is on record (`:127-159`, `:224-253`). Exit 1 means the control could not evaluate and fails the run; exit 2 means findings (`scripts/ci-reconcile-r2-customer-configs.sh:75-79`).

### 6. How a seat adopts config

- **At boot.** The root entrypoint fetches the R2 object on every boot into the root-owned `/var/lib/smd-config/customer.yaml`, not the volume (`operator/templates/entrypoint.sh:28-29`, `:31-37`). The candidate is adopted only if the on-box validator passes; a refused or unfetchable object leaves the previous config in place, and a seat with no previous config refuses to boot (`:84-127`). The file is `root:root 0644`, so the agent can read but never rewrite its own ceiling (`:128-132`). Agent-side readers resolve it through `SMD_CUSTOMER_YAML_PATH` (`:610-617`). Rebuild-class fields such as a persona's `tone` reach the seat this way, at the next restart, because `overlay:bootstrap/translate.py` regenerates the profile at boot (`overlay:config_applier/safety.py:315-328`).
- **Live, within about 15 seconds.** The overlay's root `config_applier` is launched as a uid-0 background child before the gateway drops to `hermes` (`operator/templates/entrypoint.sh:677-705`). It HEADs the object every 15 seconds by default (`overlay:config_applier/__main__.py:62`; its write target is set to the root-owned path by `operator/templates/entrypoint.sh:616`) and on an ETag change pulls, validates, diffs against the running file, and writes atomically (`overlay:config_applier/applier.py:463-555`). It applies a diff only when every changed path is on the live-writable allow-list (`overlay:config_applier/safety.py:242-291`: persona exposure, skill `initiation`/`enabled`/`settings`, `scope.inbound_allow_from`, `scope.admins`, `escalation`, `webhook_triggers`, `seat`, `output_classes`) and none is on the rebuild-class never-list (`:297-339`: `vertical`, `model`, `memory`, `hermes_ref`, `customer_id`, `fly_region`, `connectors`, persona OAuth/slug/status/tone, `seat.role`, `people`, `spaces`). One held path holds the whole diff (`overlay:config_applier/applier.py:433-452`); the live loop logs it as DEFERRED and waits for the next boot or reprovision (`overlay:config_applier/__main__.py:200-205`, `:224-233`). An apply writes a best-effort `CONFIG_WRITE` audit row (`overlay:config_applier/applier.py:536-544`).

### 7. What only reprovision or a release applies

- **Machine size and region.** Read from `customer.yaml` `machine.size`, `machine.memory_mb` and `fly_region` (`operator/bin/provision-customer.sh:186-218`) and rendered into `fly.toml` by the one renderer that reprovision and `fleet release` share (`:374-379`, `operator/bin/lib/render-fly-toml.sh`). Neither the boot fetch nor the applier can resize a Machine.
- **Firm inputs.** The chronology `firm.yaml` and its controls are uploaded to `vaults/<slug>/` by provisioning only (`operator/bin/provision-customer.sh:240-300`, `:311-321`).
- **Secrets.** Fly secrets are staged by provisioning; nothing in the config path carries them.
- **Code.** The overlay and image arrive by release, never by config (ADR 0024).

### 8. Portal runtime overrides that bypass git

Three client-owned controls change seat behavior without touching `customer.yaml`. They are deliberate and are not drift; the reconcilers do not see them.

- **Entitlement level** under the authored ceiling: the console posts to the Machine's `/entitlement/set` override store, which clamps to the authored ceiling and survives restart and reprovision (`src/lib/portal/operator/entitlement-change.ts:7-26`, `:124`).
- **Pause**: the console posts to `/sticky-stop/set` and records the governance row control-plane-side (`src/lib/portal/operator/pause-control.ts:5-19`, `:72`).
- **Output-class specs**: the console writes `vaults/<slug>/output-classes.json`, a key structurally separate from `customer.yaml`, which the overlay's `spec_applier` installs root-owned (`src/lib/operator/output-class-specs.ts:5-21`, `:78`; ADR 0083).

### 9. Retirement is `decommission-customer.sh`, never a merge

No reconciler or publisher deletes anything. A missing `customer.yaml` is warned about and skipped by the publisher (`scripts/ci-publish-customer-configs.sh:216-222`) and the D1 sync (`scripts/ci-sync-customer-configs.sh:71-77`); the R2 reconciler has no delete call (`scripts/ci-reconcile-r2-customer-configs.sh:46-48`). Retirement is `operator/bin/decommission-customer.sh <slug> --live`, dry-run by default (`:9-17`), whose pipeline ends by renaming `operator/customers/<slug>/` to `<slug>.decommissioned.<date>` (`operator/bin/lib/decommission.py:70-89`). The publisher skips tombstoned directories by name (`scripts/ci-publish-customer-configs.sh:207-214`). Which runtime layers decommission clears is governed by "gone means gone" in `CLAUDE.md`, not restated here, because that pipeline is being reworked in parallel.

## Known gaps (recorded, not decided here)

1. **Closed by #3397: the nightly R2 reconciler could undo the publisher's image guard.** At `8cb62e6d`, when the publisher refused a config the seat's image cannot run (`scripts/ci-publish-customer-configs.sh:265-288`), the `reconcile-r2` job saw byte drift and republished HEAD with validation only (`scripts/ci-reconcile-r2-customer-configs.sh:283-294`), and the seat would adopt it at its next boot, the 2026-10-13 outage class. #3397 moves the check into one shared implementation, `scripts/lib/seat-image-guard.sh`, which the publisher, the R2 reconciler and `operator/bin/sync-customer-yaml.sh` all source, so the reconciler no longer republishes a refused config. `operator/bin/provision-customer.sh` deliberately does not run it: provisioning ships the config together with the image that carries it (`:1158-1163`).
2. **Closed by #3397: `operator/bin/sync-customer-yaml.sh` carried none of the publisher's guards.** At `8cb62e6d` it had no never-create check, no image check, no read-back, and a looser slug pattern (`^[a-z0-9][a-z0-9-]{0,31}$`, `:33`) than the canonical one (`scripts/ci-publish-customer-configs.sh:228`). #3397 gives it all four, the image check through `scripts/lib/seat-image-guard.sh`.
3. **The applier records a config epoch but nothing reads it,** and it does not distinguish a widening from a tightening (section 7 of the [0044 amendment](./0044-r2-authoritative-live-reconfig.md)).
4. **Provisioning's missing-projection hint is wrong.** `operator/bin/provision-customer.sh:693-694` tells the operator to "let ci-sync-customer-configs project it", but the sync never creates a first row (`scripts/ci-sync-customer-configs.sh:100-102`); the first projection is `scripts/project-customer-config.ts`, run by hand.

## Consequences

- An agent asking "how does a config change reach a seat" gets one answer: merge, publish to `vaults/<slug>/customer.yaml`, then allow-listed fields within about 15 seconds and everything else at the next boot, with machine shape and firm inputs only at reprovision.
- ADR 0012 stays in the tree as history. Its git-first principle survives here; its paths, triggers, drift mechanism, offboarding and CLI do not.
- The `enforced_by` list above is checked by `tests/adr-integrity.test.ts`: a path that is renamed or removed fails the build, so this record cannot silently lose its enforcement the way 0012 did.
