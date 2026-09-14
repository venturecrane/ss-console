---
title: Hermes Consumption and Update Cadence — SHA-Pin, Golden Base Image, Track-vs-Deploy, Blessed Fleet Version
date: 2026-05-28
status: accepted
captain: Scott Durgan
supersedes: 0015-hermes-fork-vs-upstream.md
amends: 0007-per-customer-machine-isolation.md, 0018-gepa-disposition.md
related-issue: https://github.com/venturecrane/ss-console/issues/1133 (migration Steps 2–5)
---

# ADR 0024 — Hermes Consumption and Update Cadence

**Status:** Accepted (Captain decision, 2026-05-28). Migration Step 1 (SHA-pin + de-fork the build path) lands with this ADR; Steps 2–5 are sequenced per the migration plan below.

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
