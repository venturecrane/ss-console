---
title: Hermes Consumption and Update Cadence — SHA-Pin, Golden Base Image, Track-vs-Deploy, Blessed Fleet Version
date: 2026-05-28
status: implemented (amended 2026-10-19)
captain: Scott Durgan
supersedes: 0015-hermes-fork-vs-upstream.md
amends: 0007-per-customer-machine-isolation.md, 0018-gepa-disposition.md
related-issue: https://github.com/venturecrane/ss-console/issues/1133 (migration Steps 2–5)
---

# ADR 0024 — Hermes Consumption and Update Cadence

**Status:** Accepted (Captain decision, 2026-05-28). Migration Step 1 (SHA-pin + de-fork the build path) lands with this ADR; Steps 2–5 are sequenced per the migration plan below.

**Implemented with amendments (2026-10-19).** Decisions 2, 3, 6 and 7 are built as the fleet release system (`operator/bin/fleet`), with four amendments recorded in [the amendment section](#amendment-2026-10-19-implemented-as-the-fleet-release-system) below: the Fly registry app `hermes-fleet` instead of GHCR, rings instead of a single canary, skills as their own release artifact, and a supervisor drain so a deploy never kills a running job. The Hermes pin moved from `customer.yaml.hermes_ref` to the Dockerfile's own `ARG` defaults (one image, so one pin).

**Amended 2026-10-22 by [ADR 0095](./0095-one-repository-for-the-seat-runtime.md):** the overlay is built from `operator/hermes-smd-overlay/` in this repo, so a release is one repo at one sha; see [the amendment](#amendment-2026-10-22-one-repo-one-sha).

**Source:** A first-principles audit on 2026-05-28 of two coupled questions: (1) why `venturecrane/hermes-agent` exists, and (2) whether we have a real strategy for keeping current with Hermes given upstream's weekly release train. The audit found the fork to be a retrofitted relic of the pre-2026-05-24 "code-in-fork" posture, with a supply-chain-mirror justification that does not survive scrutiny, and found the update strategy to be a quarterly-clock posture bolted onto a weekly-shipping dependency. This ADR replaces the fork posture of ADR 0015 and adds the update-cadence strategy ADR 0015 never contained.

---

## Context

### What ADR 0015 decided, and what changed under it

ADR 0015 (first landed 2026-05-21 in PR #962; rewritten 2026-05-24 in PR #1034) established a **pin-only fork** at `venturecrane/hermes-agent` plus a **plugin-only overlay** at `venturecrane/hermes-smd-overlay`. The plugin-only-overlay half of that decision is sound, verified against upstream's documented plugin policy, and is **preserved unchanged** by this ADR.

The fork half does not hold up. The fork was born under the _original_ ADR 0015, which proposed "a thin vendored fork **with SMD overlay layer**" — SMD code living inside the fork. When the 2026-05-24 realignment correctly moved all SMD code into the separate overlay repo, the fork lost its reason to exist. Instead of being retired, it was repurposed as a "pin-only mirror" and given a fresh `SMD_FORK_POLICY.md`. The justification was retrofitted onto an artifact that already existed.

### The fork's three stated justifications, tested against the code

1. **Immutability (upstream can retag / force-push).** The build already pins `HERMES_UPSTREAM_SHA=a91a57fa…` (`operator/templates/Dockerfile`) and asserts the clone against it. A commit SHA is content-addressed; upstream cannot mutate what a SHA points to. Pinning upstream by SHA delivers full immutability with no fork. The fork tag is a label that must equal a SHA we already trust. **Redundant.**

2. **Availability hedge (upstream could disappear).** This is the only property a mirror uniquely provides, and the current wiring discards it. `operator/bin/provision-customer.sh:144` runs `git ls-remote --tags https://github.com/NousResearch/hermes-agent.git` on **every provision** to resolve the upstream SHA, and `die`s if it fails. Both the fork **and** upstream must be reachable at build time. If Nous deletes the repo, provisioning fails regardless of the fork. The fork does not remove the upstream dependency; it adds a second repo that must also be up. **Defeated by our own verification step.**

3. **A place to put a security patch.** Real, but weak. A reviewable `.patch` file applied during the base-image build (diff visible in PR, documented in a changelog) is more auditable than a forked branch carrying a magic `-smd.security.N` tag. Furthermore the "AGPL §13 unmodified-deployment safe harbor" the Dockerfile cites is misapplied: **Hermes is MIT** (no copyleft, no source-offer obligation, no safe harbor needed). The component that _is_ AGPL — **Honcho** (`plastic-labs/honcho`, AGPL-3.0) — has **no** integrity assertion, only a `TODO(ci)` in the Dockerfile. The assertion guards the license that does not need it and skips the one that does. (Legal review flag, not a legal opinion.)

### The deeper smell: per-customer from-source rebuilds

`fly deploy` builds the Dockerfile **from source for every customer** (`operator/templates/fly.toml.template` `[build] dockerfile=…`). Each provision re-clones Hermes, re-runs `npm install` + `playwright install` + `uv sync` + web/TUI bundle builds + Honcho install + overlay install. The build is identical across customers (the only baked per-customer input is `CUSTOMER_SLUG`; the real per-customer data, `customer.yaml`, is correctly fetched from R2 at boot, not baked). There is **no golden base image and no registry push.** N customers means N from-scratch builds pulling from two GitHub repos each, with no guarantee two customers built a week apart get byte-identical transitive dependencies. The fork is a symptom of this build shape.

### The update-cadence problem

Our documented posture is **quarterly / 90-day** everywhere:

- ADR 0015: "Quarterly rebase becomes a tag-bump exercise."
- ADR 0018: repeated "quarterly-rebase agenda item."

Upstream ships **weekly**: `v2026.5.28` (today), `v2026.5.16`, `v2026.5.7`, `v2026.4.30`, `v2026.4.23`… roughly 13 releases in 10 weeks. A quarterly rebase against a weekly release train leaves us structurally ~12 versions behind at all times, on a runtime whose new capabilities (ADR 0021's native primitives) land weekly. This is already visible: `docs/hook-surface.md` is pinned to `v2026.5.16` and went stale the day `v2026.5.28` shipped.

Two distinct clocks are conflated under one number:

- **"Are we current with Hermes releases?"** — operational; should be weekly/continuous.
- **"Should we still be on Hermes at all?"** — strategic; quarterly is correct (this is the adapter-portability re-evaluation in ADR 0006).

The rebase is also described as a **manual** exercise (hand-authored hook-surface citations, "re-verify at every rebase"). Manual cost is _why_ the cadence was set slow. The fix is to automate the cost down so the cadence can rise.

---

## Decision

SMD consumes Hermes as a **SHA-pinned upstream dependency, packaged into a golden base image we build in CI and store in our own container registry, rolled out to the fleet on a track-vs-deploy cadence with a single blessed fleet version.** The `venturecrane/hermes-agent` fork is retired.

### 1. Pin upstream by commit SHA, not via a fork tag

`customer.yaml.hermes_ref` pins an **upstream** ref of the form `vYYYY.M.D@<40-char-sha>` (tag for human readability, SHA for immutability). The validator (`src/lib/operator/customer-yaml/helpers.ts`, `validator.ts`) is updated to this shape. There is no `-smd.N` suffix and no fork.

### 2. Build one golden base image; the registry is the mirror

A CI workflow builds a single base image — Hermes at the pinned SHA + the `hermes-smd-overlay` plugins + Honcho — and pushes it to **GHCR** (`ghcr.io/venturecrane/hermes-base:<tag>`, digest-pinned). This image is the durable artifact and the real availability hedge: it contains the source, so upstream disappearing after a build is irrelevant. It also gives reproducibility (every customer runs the same digest) and fast provisioning (compile once, not per customer). The build asserts the cloned Hermes SHA equals the pinned upstream SHA; a Honcho version/commit assertion is added at the same time (closing the existing `TODO(ci)`).

### 3. Per-customer Machines pull the base image; they do not build

`fly.toml` references `image = "ghcr.io/venturecrane/hermes-base@sha256:…"` instead of `[build] dockerfile=…`. Per-customer differentiation is configuration injected at boot (`customer.yaml` from R2, per ADR 0019), not a per-customer build. Provisioning stops re-cloning source.

### 4. Retire `venturecrane/hermes-agent`

Once provisioning consumes upstream-by-SHA via the base-image pipeline, the fork has no consumer. Archive it (read-only) with a tombstone README pointing to this ADR. Remove fork references from `Dockerfile`, `provision-customer.sh`, `fly.toml.template`, `customer.yaml` schema/validator, and the doc set. We do not need a standing org fork to submit upstream PRs — fork-on-demand covers contribution.

### 5. Track continuously, deploy deliberately

- **Track (automated, per upstream release).** A CI job watches the upstream releases endpoint. On each new tag: build a candidate base image at that SHA, run the `hermes-smd-hook-probe` plugin + the overlay test suite + the safety_substrate invariants, and emit a green/red compatibility signal plus an auto-diff of the hook surface. This replaces hand archaeology and keeps `hook-surface.md` honest automatically.
- **Deploy (deliberate).** A green candidate becomes eligible for promotion. Promotion to the fleet is a Captain decision triggered by a wanted capability, an accumulated set of fixes, or a security fix — not automatic on every green.

### 6. One blessed fleet version

At any time there is exactly one **blessed** base-image version the fleet targets. Per-customer pinning (ADR 0007's "no silent propagation") is preserved, but its role is **rollback and canary**, not permanent divergence: a customer may be held at the previous blessed version if a regression hits them, not left to drift indefinitely on a private snowflake. Standing fleet state is "everyone on blessed, minus explicitly-pinned exceptions with a reason recorded."

### 7. Staged rollout

Promotion path: blessed candidate → internal/demo Machine → one canary customer → fleet. Standard progressive delivery; the per-customer pin is the rollback lever at each gate.

### 8. Separate, fast security lane

A CVE materially affecting customer Machines jumps the cadence queue. With no fork, an emergency fix is either an accelerated bump to a patched upstream release or a reviewable `.patch` applied in the base-image build, documented in a changelog with CVE reference and a forced 30-day retirement (the escape-valve discipline from ADR 0015 is preserved, relocated from the fork to the build).

### 9. Two clocks, named

- **Operational currency with Hermes:** continuous tracking, deliberate weekly-to-monthly blessed-version promotion.
- **Strategic substrate re-evaluation** (is Hermes still the right harness; adapter-portability per ADR 0006): quarterly.

---

## Alternatives Considered

### A. Keep the fork as a pin-only mirror (ADR 0015 status quo)

**Rejected.** Its immutability benefit is redundant with SHA-pinning; its availability benefit is defeated by the provision-time `git ls-remote` to upstream; its security-patch role is weaker and less auditable than a build-applied patch; and its existence is a standing source of confusion and a re-tagging ritual on every bump. "Costs nothing to keep" is the rationalization that let a relic survive a course-correction.

### B. Per-customer from-source builds (current build shape)

**Rejected.** Slow, non-deterministic across the fleet, and multiplies the supply-chain surface by N. Per-customer variation is configuration, not compilation.

### C. Auto-propagate every green upstream release to the fleet

**Rejected.** Violates ADR 0007's no-silent-propagation safety posture. A paying customer's agent must not change behavior because upstream shipped on a Tuesday. Track ≠ auto-deploy.

### D. Manual weekly rebase (raise the cadence, keep it manual)

**Rejected.** Manual verification is the bottleneck that forced the quarterly cadence in the first place. Raising frequency without automating the compatibility check just multiplies the manual archaeology. Automate first, then the cadence is free to rise.

### E. Vendored tarball in R2 instead of a registry image

**Considered, folded in.** A tarball solves immutability and availability but not reproducibility-of-the-built-image or provisioning speed. The GHCR base image is a strict superset and is the artifact customer Machines actually run, so it subsumes the tarball.

---

## Consequences

**Positive.**

- One architectural lever closes three open threads: fork retirement, golden-base-image, and update cadence are the same pipeline.
- Genuine availability (the built image contains the source) instead of a fork that still phones upstream at provision time.
- Fleet determinism: every customer runs an identical, digest-pinned image.
- Fast provisioning: Hermes compiles once per blessed version, not once per customer.
- "Are we compatible with the latest Hermes?" becomes an automated answer within hours of each release, not a quarterly manual dig. `hook-surface.md` stops silently rotting.
- The actually-AGPL component (Honcho) gets the integrity assertion it currently lacks.
- The no-modify-core discipline (ADR 0015's durable half) is preserved and is what makes a fast cadence cheap.

**Negative / accepted.**

- Up-front cost to build the CI → GHCR pipeline (a GitHub Actions workflow; GHCR is free for the org, so no new vendor or spend). This is the deliberate investment that replaces recurring manual rebase toil.
- We give up Fly's "push code, Fly builds" convenience for `fly deploy --image`. For a single app that convenience matters; for an N-tenant fleet wanting identical images it is the wrong default, so the loss is nominal.
- A blessed-version model requires discipline to keep pinned-exception customers from becoming permanent snowflakes; the "reason recorded" requirement on each exception is the structural guardrail.
- Security patches applied as build-layer `.patch` files require the same retirement discipline ADR 0015 placed on fork security tags.

---

## Migration plan (sequenced for a pre-launch, zero-customer venture)

The binding constraint today is that the product has never booted end-to-end. The sequencing reflects that — do the cheap correct thing, then prove a boot, then harden — without gold-plating a pipeline before first boot.

1. **Now, cheap, independent:** switch `hermes_ref` to upstream SHA-pin and point the Dockerfile clone at `NousResearch/hermes-agent` directly. Removes the fork from the critical path. (Fork can be archived after this lands.)
2. **First boot:** get one Machine up end-to-end on the SHA-pinned path and learn what actually breaks. Everything below is theory until this happens once.
3. **Before first real customer:** stand up the CI → GHCR base-image pipeline (Decisions 2–3) and the tracking job (Decision 5). Switch `fly.toml` to `image =`.
4. **At customer #1:** adopt the blessed-version + staged-rollout model (Decisions 6–7) in the onboarding runbook.
5. **Doc cleanup:** retire the fork tombstone, strike "quarterly rebase" language from ADR 0018, and replace it with the two-clocks model.

---

## Verification

How we know we are following this decision:

1. `customer.yaml.hermes_ref` validates against the upstream `vYYYY.M.D@<sha>` shape; the `-smd.N` fork-tag pattern is rejected.
2. `provision-customer.sh` and `fly.toml` contain no reference to `venturecrane/hermes-agent`; the fork is archived read-only.
3. `fly.toml` deploys by `image =` digest, not `[build] dockerfile=…`; no per-customer source build occurs.
4. A CI run exists that, on a synthetic new upstream tag, produces a candidate image and a pass/fail compatibility report including a hook-surface diff.
5. The fleet view shows one blessed version with any exceptions carrying a recorded reason.
6. The base-image build asserts both the Hermes SHA and the Honcho version/commit.

## Amendment (2026-10-19): implemented as the fleet release system

Plan `zesty-gathering-axolotl`; every seat was still rebuilt from source per deploy (about 12 minutes each, serial, by hand), and every deploy killed running jobs (Fly's default stop is SIGINT plus 5 seconds). What was built, and where it departs from Decisions 2, 3, 6 and 7:

1. **Registry: the Fly app `hermes-fleet`, not GHCR.** All seat apps live in one Fly org, whose registry is shared across its apps, so an image pushed to `registry.fly.io/hermes-fleet` deploys to every `hermes-<slug>` app with `fly deploy --image`. CI holds no Fly token and GHCR would add a second registry credential to every seat's pull path for no gain. `fleet build` runs `fly deploy --build-only --push --remote-only -a hermes-fleet` once per release; the app has no Machines. The base image is the whole image (Hermes, overlay, our code), built once per release, not a base layered per seat: the Dockerfile declares `OVERLAY_REF` and `SS_CONSOLE_SHA` immediately before their first use and no longer takes `CUSTOMER_SLUG`, so the apt, npm, uv and Hermes layers are cached across releases and an overlay-only release rebuilds only the tail.
2. **The Hermes pin is the Dockerfile's.** `ARG HERMES_UPSTREAM_TAG` / `HERMES_UPSTREAM_SHA` are the one pin (still `vYYYY.M.D@<sha>` in shape, still asserted against the clone). A per-seat `customer.yaml.hermes_ref` could only disagree with the one image every seat runs; provisioning and boot smoke read the Dockerfile. Removing the field from every `customer.yaml` (and making the validator reject it) waits until every seat runs an overlay whose boot validator no longer requires it.
3. **Rings, not one canary.** `operator/fleet/rings.yaml`: 0 `smd-staging`, 1 `pilot-smokeball`, 2 `scott` `brokery` `captain-mark`, 3 `ashton-price` (always serial); a seat in no ring joins the last. Per seat: holds (D1 `fleet_holds`), preflight (required env present, no undeclared staged secret, lane-input schemas current), a live busy gate over ssh, snapshot, deploy, boot smoke, and a heartbeat read-back of the image and overlay. Only a genuine seat failure rolls the seat back (to its recorded image and fly.toml) and halts; a tooling error or timeout halts and touches nothing. One rollout at a time (D1 `fleet_lease`). "Blessed" (Decision 6) is concrete: the newest release with `completed_last_ring_at`, which provisioning deploys to a new seat.
4. **Skills are a release artifact.** A skill-only change ships without an image: CI publishes content-addressed objects and a manifest (with each skill's needs from its image) to R2, and `fleet release --skills <version>` repoints each seat ring by ring after checking the seat's image capabilities (`/app/image-capabilities.json`, baked at build). No restart. A skill rollback is a skills release of the previous version.
5. **Supervisor drain.** `kill_signal = "SIGTERM"`, `kill_timeout = 300` in the seat's fly.toml; the entrypoint supervises the gateway instead of exec-ing it, quiesces the runner lanes and lets a running job checkpoint before stopping. The busy gate quiesces (`/run/smd-medchron/quiesce`, expiring) and waits for idle before it deploys, so a release never interrupts a job in the first place.

**Considered and rejected: fetching the overlay at boot.** A seat could pull the pinned overlay from git on every boot and skip image builds for overlay-only changes. Rejected because (a) it breaks image identity: the digest would no longer say what code runs, and the heartbeat's image ref would stop being evidence; (b) it breaks overlay-pairs twin parity, which is checked against the image's baked overlay at build time; (c) it weakens the tamper boundary, since the code a seat runs would be whatever the network returned at boot rather than a reviewed, content-addressed artifact; and (d) once the heavy layers cache, an overlay-only image release costs minutes, so the gain is negligible.

Verification items 3 and 5 below are met by the fleet system (`fly deploy --image`, `fleet status`); item 4 (an automated upstream-tracking job) is still open.

## Amendment (2026-10-21): one seat never waits on the fleet

Amendment 3's "one rollout at a time" and its definition of Blessed coupled every seat to every other one. On 2026-10-20 captain-mark's rollout was refused at preflight (a staged secret the release did not declare). Because completion required one full run that halted nowhere, no release could complete after that, and because provisioning deployed only a completed release, every seat's reprovision was refused for a day. Clearing it took a six-seat rollout of software every seat already ran, and the single lease meanwhile refused an `--only` restart of the one seat that needed it. Nothing paged. Four changes:

1. **Reprovisioning keeps the seat's image.** An existing seat is redeployed on the image its Machine runs now (`fleet seat-image`, read from Fly). Only a new seat takes Blessed. Reprovisioning re-applies one seat's config and secrets; moving software forward is only ever `fleet release`. This also closes a latent downgrade: a seat moved ahead with `--only` would have been put back on an older Blessed image.
2. **Completion is read from the records.** A release is Blessed when every ringed seat's latest rollout of it is `done`, however many runs that took. A seat repaired with `--only` completes the release; a seat left `waiting_idle` or held keeps it incomplete (previously those did not block completion). A release is stamped once, so re-running an old release cannot make it the newest Blessed.
3. **Two lease scopes.** The fleet lease is held only by a full rollout. Every seat deploy, whether a rollout's, an `--only`, or a reprovision (`fleet with-seat-lease`), holds that seat's lease (`seat:<slug>` in the same `fleet_lease` table). Two deploys never land on one seat; no seat's deploy waits for another seat's.
4. **A failed rollout pages.** The fleet-alerts Worker emails team@smd.services once per new `failed`, `rolled_back` or `tooling_error` rollout row (migration 0133, condition `rollout_failed`, marker-shaped like `send_refused`).

## Amendment (2026-10-22): one repo, one sha

[ADR 0095](./0095-one-repository-for-the-seat-runtime.md) moved the overlay into this repo at `operator/hermes-smd-overlay/`. What changes here:

1. **One repo, one sha.** A release is built from one ss-console commit. The Dockerfile no longer declares `OVERLAY_REF` or `OVERLAY_REPO`; it copies the overlay tree in, path-installs it into the Hermes venv with `--no-deps` (third-party dependencies still hash-locked in `operator/requirements/hermes-overlay.txt`), and copies it again to `/app/overlay-pack`. `SMD_OVERLAY_REF` and `/app/OVERLAY_REF` carry the ss-console commit, and that is the overlay version the heartbeat reports. Amendment 2026-10-19 item 1's "declares `OVERLAY_REF` and `SS_CONSOLE_SHA` immediately before their first use" now reads: the overlay copy and `SS_CONSOLE_SHA` come after the cached apt, npm, uv and Hermes layers, so an overlay-only release still rebuilds only the tail.
2. **The label format.** `fleet build` labels images `rel-<ss-console sha8>-<yyyymmddHHMM UTC>` (was `rel-<ss8>-<overlay sha8>-<stamp>`) and release ids `img-<stamp>-<ss8>`. `fleet_releases.overlay_ref` records the ss-console commit, which the rollout's heartbeat read-back requires the seat to echo. `fleet status` drift compares image refs as before.
3. **The boot-fetch rejection stands, and is now structural.** The rejection above of fetching the overlay at boot holds for the same reasons (image identity, twin parity, the tamper boundary, negligible gain). The pack is copied into the image at build, `bootstrap.sh` refreshes the volume from the image's copy, and there is no runtime install fallback: bootstrap dies if the pack is missing. Reason (b) changes form: twin parity is now checked in the tree by `tests/operator-overlay-pairs.test.ts`, not against the image's baked overlay.
4. **Rollback and the pre-fold lane.** Rollback is `fleet release <previous img id>`, as for any release. While `ashton-price` is held on a pre-fold image, its hotfixes come from the locked standalone repo and the ss-console branch `release/pre-fold` (ADR 0095 section 8).

---

## References

- [ADR 0015 — Hermes fork posture](./0015-hermes-fork-vs-upstream.md) (superseded by this ADR; plugin-only-overlay half preserved)
- [ADR 0006 — Capability-adapter pattern](./0006-capability-adapter-pattern.md) (adapter portability = the strategic substrate hedge)
- [ADR 0007 — Per-customer Machine isolation](./0007-per-customer-machine-isolation.md) (no-silent-propagation preserved; pin role clarified to rollback/canary)
- [ADR 0018 — GEPA disposition](./0018-gepa-disposition.md) ("quarterly rebase" language to be amended to the two-clocks model)
- [ADR 0021 — Leverage Hermes native primitives](./0021-leverage-hermes-native-primitives.md) (pull-based capability adoption)
- Upstream plugin policy: `AGENTS.md` in [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
- [NousResearch/hermes-agent tags](https://github.com/NousResearch/hermes-agent/tags) (weekly date-based release cadence)
- `operator/templates/Dockerfile`, `operator/bin/provision-customer.sh`, `operator/templates/fly.toml.template` (current build wiring)
