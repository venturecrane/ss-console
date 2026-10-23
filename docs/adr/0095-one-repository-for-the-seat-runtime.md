---
title: One repository for the seat runtime
date: 2026-10-22
status: accepted
captain: Scott Durgan
supersedes: the separate-repo half of 0015-hermes-fork-vs-upstream.md (the plugin-only rule stands)
amends: 0024-hermes-consumption-and-update-cadence.md, 0053-author-built-mcp-connectors-per-customer-installed.md, 0081-repository-visibility.md
related-adr: docs/adr/0015-hermes-fork-vs-upstream.md, docs/adr/0024-hermes-consumption-and-update-cadence.md, docs/adr/0094-customer-configuration-as-built.md, docs/adr/0023-operator-per-customer-observability.md
enforced_by:
  - operator/templates/Dockerfile
  - tests/operator-dockerfile.test.ts
  - tests/operator-overlay-pairs.test.ts
  - .github/workflows/operator-substrate.yml
  - operator/requirements/compile.sh
  - operator/hermes-smd-overlay/pyproject.toml
  - operator/bin/lib/build-source-guard.sh
  - tests/doctrine-integrity.test.ts
---

# ADR 0095: One repository for the seat runtime

**Status:** Accepted 2026-10-22 (Captain). Supersedes the separate-repo half of [ADR 0015](./0015-hermes-fork-vs-upstream.md); the plugin-only rule of 0015 stands unchanged. Amends [ADR 0024](./0024-hermes-consumption-and-update-cadence.md) (one repo, one sha), [ADR 0053](./0053-author-built-mcp-connectors-per-customer-installed.md) (where the overlay lives, and the drift model it cited) and [ADR 0081](./0081-repository-visibility.md) (the overlay's visibility is no longer load-bearing for builds). Wave 1, workstream W1, of the 2026-10-22 seat-architecture review.

## Context

The Operator seat runtime was one program split across two repositories. The overlay, the Hermes plugins, bootstrap and root appliers that run on every seat, lived in `venturecrane/hermes-smd-overlay`; everything that builds, configures, releases and governs a seat lived here. The image joined them by pinning the overlay commit as `ARG OVERLAY_REF` in `operator/templates/Dockerfile` and fetching it from GitHub at build, three times: a `uv pip install` from a git URL, a `hermes plugins install` that cloned the repo, and a shallow fetch of the same commit into `/app/overlay-pack`. `bootstrap.sh` kept a runtime install fallback for a fresh volume.

The 2026-10-22 seat-architecture review measured what the split cost:

- **203 pin bumps in 90 days.** Every overlay change was two PRs, one per repo, and the second was a one-line pin bump that collided with every other session's bump on the same lines.
- **13 hand-mirrored twin files.** Contract fixtures were regenerated here and copied over the overlay's copy by hand in the same change (the seat/gate binding snapshot, the policy inputs, the lane-proof cases, the send-render table, the validator parity fixtures, the body-canon and roster-canon vectors, the skill-capture contract), and the SEC-32 gate (`operator/bin/verify-overlay-pairs.py`) existed to catch the copies drifting.
- **A `customer.yaml` validator in two languages**, held in parity by a hash pinned on both sides (ADR 0094 section 2).
- **Cross-repo parity tests that could only run on a laptop.** The audit-vocabulary, heartbeat-field and overlay-pairs tests skipped in CI because CI had no overlay checkout, so the gates that most needed CI ran least.
- **"Proven" meant "the seat booted".** A change was certified by the image building and the boot smoke passing, because no CI job ran the overlay against the ss-console code it pairs with.
- **Agents stopped at the repo boundary.** On 2026-08-25 an agent finished a seat retirement across git, D1, R2 and Fly, then stopped at the overlay's mirrored fixture to ask permission to continue. CLAUDE.md gained a section declaring the three repositories; that section treated the symptom.

Wave 0 of the review, merged the same day, shipped the instruments before any structural change: the drift alarm (#3396), the R2 image guard (#3397), ADR 0094 (#3399), the seat inventory (#3400), the decommission fix (#3401) and the history lint (#3408). This ADR is the first structural change, made under the Captain's constraints: tens of seats, not thousands; Hermes is replaceable but client data stays in our cells; build in parallel and cut over seat by seat, with `ashton-price` last.

## Decision

### 1. The overlay is a directory of this repo

The overlay source is `operator/hermes-smd-overlay/`, a pure move of the standalone tree at pin `8c6f4b865fbead8fb3a04cec8a025bcafee33b47`. The venture's runtime code now lives in one repository, `venturecrane/ss-console`; the venture spans two repositories, this one and `venturecrane/engagements` (client material, ADR 0081).

**The directory keeps the plugin's name on purpose.** pytest's package walk, which finds a test file's base directory by climbing while each parent holds an `__init__.py`, stops at a directory whose name is not a Python identifier. The standalone repo relied on that stop to make its root the import base for `shared`, `bootstrap`, `plugins` and the appliers. `hermes-smd-overlay` is not an identifier, so the walk stops there inside this tree too and the overlay's tests import exactly as they did. The overlay root carries an `__init__.py` and `operator/` does not, so a rename to an identifier would let the walk climb one level and make `operator/` the import base, breaking every top-level import the overlay's tests use.

### 2. The image is built from the local tree

The Dockerfile copies the overlay in and installs it; nothing is fetched from git at build or at boot.

- `COPY operator/hermes-smd-overlay/ /app/overlay-src/`, then a `uv pip install --no-deps` path install into the Hermes venv. The overlay's third-party dependencies are still hash-locked in `operator/requirements/hermes-overlay.txt`, derived by `operator/requirements/compile.sh` and constrained to Hermes' own lock.
- A second `COPY operator/hermes-smd-overlay/ /app/overlay-pack/`, which `bootstrap.sh` refreshes onto the volume on every boot exactly as before (the volume shadows anything the build put under `${HERMES_HOME}`).
- Removed: `ARG OVERLAY_REF`, `ARG OVERLAY_REPO`, the build-time `hermes plugins install`, every git clone of the overlay in the image, and the runtime install fallback. `bootstrap.sh` dies if the pack is missing.

### 3. The overlay version is the ss-console commit

`ENV SMD_OVERLAY_REF=${SS_CONSOLE_SHA}`, and a sentinel file `/app/OVERLAY_REF` carries the same value. The heartbeat's `version` field, which a seat reports as its overlay version, is now the ss-console commit its image was built from.

- `fleet build` labels images `rel-<ss-console sha8>-<yyyymmddHHMM UTC>` (was `rel-<ss8>-<overlay sha8>-<stamp>`) and release ids `img-<stamp>-<ss8>`.
- The `fleet_releases.overlay_ref` column records the ss-console commit, and the rollout's heartbeat read-back requires the seat to echo it.
- `fleet status` drift compares image refs as before.

### 4. What the fold removes

- `operator/bin/verify-overlay-pairs.py`, the SEC-32 cross-repo hash gate. Both twins of each pair are now hashed in place by `tests/operator-overlay-pairs.test.ts`.
- `operator/bin/overlay-ref-drift.py`, redundant with `fleet status` drift.
- `.claude/hooks/sync-overlay-checkout.sh`, the SessionStart hook that kept a second checkout current. There is no second checkout.
- The `overlayRepo`, `overlayRef` and `overlayRefNote` keys of `operator/contracts/overlay-pairs.json`, the `overlayRef` of `operator/contracts/audit-action-vocabulary.json`, and the `overlay_ref` of `operator/observability/heartbeat-fields.json`. The parity tests read the overlay from disk, in CI too; the "skipped in CI, runs only on a laptop with the overlay checkout" caveat is gone.
- The 8 hand-copied fixture files inside the overlay: `tests/contract/seat_gate_binding_snapshot.json`, `tests/contract/policy_inputs.json`, `tests/contract/lane_proofs_cases.json`, `tests/contract/send-render.yaml`, `tests/contract/validator_parity_fixtures.json`, `tests/fixtures/body-canon-vectors.json`, `tests/fixtures/roster-canon-cases.json` and `contracts/skill_capture_v1.json`. The overlay's tests read the ss-console originals in place. "Regenerate here, then copy over the overlay's fixture" is no longer a step anywhere.

### 5. CI runs the overlay as a required check

`.github/workflows/operator-substrate.yml` gains a separate job, `overlay`: ruff check and ruff format check over the overlay tree; the overlay pytest suite on Python 3.13 against the pinned Hermes, fetched shallow from `NousResearch/hermes-agent` at the Dockerfile's `ARG HERMES_UPSTREAM_SHA`; and a no-diff run of `operator/requirements/compile.sh`, so a dependency change that skips the lockfile fails. `overlay` is a required check of the main ruleset (id 15555219) since the fold merged (added by the merging session with `gh api`, read back, vfy_01M5KA91K2A1ZWBK2J7KBQHY1K), making the required list: `Typecheck, Lint, Format, Test`, `Security Summary`, `substrate`, `overlay`, and `Runtime ACs carry a verification ID`. A ruleset change is execution, not a Captain act. A later job, `overlay-e2e` (Wave 1, W3: a real Hermes dispatch harness), is planned and not part of this decision.

### 6. A runtime change is one PR

A change to the seat runtime is one ordinary PR, whether it touches the overlay, the rest of `operator/`, or both, followed by `fleet build` and `fleet release` as today (ADR 0024, amended). Pin bumps are gone.

### 7. Cutover, seat by seat, `ashton-price` last

The folded image is built in parallel with the last pre-fold release, and seats move in ring order: `smd-staging`, `pilot-smokeball`, then `scott`, `brokery` and `captain-mark`, and `ashton-price` last. Each move is an ordinary `fleet release <id> --only <seat>` or ring rollout, with the usual busy gate, snapshot, boot smoke and heartbeat read-back. Rollback for any seat is `fleet release <previous img id>`: the pre-fold image stays in the registry and its release row in D1.

### 8. Hotfix lanes

- **A seat on a folded image:** an ordinary PR, then `fleet release`.
- **`ashton-price` while it is held on a pre-fold image:** an overlay hotfix branch off `8c6f4b86` in the locked standalone repo (unlock, land, relock), plus a pin bump on the ss-console branch `release/pre-fold`, built with `SS_ALLOW_DIVERGENT_SOURCE=1 fleet build` (the build-source guard otherwise refuses a tree that is not origin/main) and released with `fleet release --only ashton-price`. The lane is recorded in the release notes, and it closes when `ashton-price` runs a folded image.

### 9. The standalone repo

`venturecrane/hermes-smd-overlay`'s `main` is locked by a GitHub ruleset (lock branch) at the fold, and its full history is pushed into this repo as `refs/heads/overlay-history`. The repo is archived only after `ashton-price` runs a folded image, with one last commit replacing its README with a pointer here. Its public visibility (ADR 0081 section 2) was load-bearing only because the Dockerfile fetched it unauthenticated at build; that reason is gone.

## What stands

ADR 0015's plugin-only rule is untouched: the overlay MUST NOT modify Hermes core files, extends Hermes only through its documented plugin surface, and Hermes is pinned by tag and SHA and run byte-for-byte (ADR 0024). The fold changed where the overlay's code lives, not what it may touch. ADR 0024's rejection of fetching the overlay at boot also stands, and is now structural: the pack is copied into the image, so the code a seat runs is the reviewed, content-addressed tree its digest names.

## Consequences

- One PR, one CI run and one commit identify a runtime change from source to seat. The heartbeat's version, the release row and the image label all carry the same ss-console commit.
- The parity tests that skipped in CI now run there, and the twins they compare are both in the tree, so a one-sided edit fails the PR that makes it.
- The overlay suite is a merge gate rather than a laptop habit.
- `CLAUDE.md` declares two repositories, and the 2026-08-25 failure has no boundary left to stop at.
- Version strings changed shape. Anything that parsed `rel-<ss8>-<ov8>-<stamp>` or compared an overlay commit against the standalone repo reads the new form; heartbeats from pre-fold images still report standalone overlay commits until each seat moves.
- For the cutover window the fleet runs two kinds of image, and `ashton-price` keeps a pre-fold hotfix lane until it moves.

## Enforcement

`tests/adr-integrity.test.ts` checks that every path under `enforced_by:` above exists. `tests/operator-dockerfile.test.ts` pins the Dockerfile's shape; `tests/operator-overlay-pairs.test.ts` hashes each twin pair in place; the `overlay` job of `.github/workflows/operator-substrate.yml` runs the overlay suite and the lockfile check as a required check; `operator/bin/lib/build-source-guard.sh` refuses to build a release from anything but a clean origin/main unless `SS_ALLOW_DIVERGENT_SOURCE=1` says otherwise; and `tests/doctrine-integrity.test.ts` fails if `CLAUDE.md` stops declaring where the overlay lives.
