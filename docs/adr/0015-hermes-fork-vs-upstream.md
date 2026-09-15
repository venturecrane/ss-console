---
title: Hermes Fork Posture — Pin-Only Fork, Plugin-Only Overlay, No Core-File Modifications
date: 2026-05-24
status: partially-superseded
captain: Scott Durgan
superseded-by: 0024-hermes-consumption-and-update-cadence.md (fork half only)
supersedes: 0015-hermes-fork-vs-upstream.md (prior version of this file; see `git log docs/adr/0015-hermes-fork-vs-upstream.md`)
related-issue: https://github.com/venturecrane/ss-console/issues/844
---

# ADR 0015 — Hermes Fork Posture

> **Partially superseded 2026-05-28 by [ADR 0024](./0024-hermes-consumption-and-update-cadence.md).** The **fork half** of this ADR (the pin-only `venturecrane/hermes-agent` fork, the `v{upstream}-smd.N` tag scheme, and the security-patch escape valve hosted on the fork) is retired: a first-principles audit found the fork to be a retrofitted relic whose immutability benefit was redundant with SHA-pinning and whose availability benefit was defeated by the provision-time `git ls-remote` to upstream. SMD now pins `NousResearch/hermes-agent` directly by `v{date}@{sha}`. The **plugin-only-overlay half** of this ADR (all SMD code in `venturecrane/hermes-smd-overlay`, no core-file modifications) stands unchanged and is the durable, verified decision. Read this ADR for the overlay posture; read ADR 0024 for how Hermes is pinned, built, and updated.

**Status:** Accepted 2026-05-24; fork half superseded by ADR 0024 on 2026-05-28.

**Source:** The locked Hermes-alignment build plan dated 2026-05-24, following Teknium's (Nous Research lead) May 2026 policy statement and PR #5295 enforcement. This rewrite replaces the prior version which proposed a "thin vendored fork with SMD overlay layer" — a posture that, on first-source review of Hermes' plugin policy, puts SMD's overlay on the wrong side of upstream rules and creates fork-divergence costs we accepted under a false assumption about how the integration would land.

## Context

Three findings reshape the fork question that prior versions of this ADR addressed:

1. **Hermes has a documented plugin policy that forbids core-file modifications.** From upstream's `AGENTS.md` (Teknium, May 2026): "plugins MUST NOT modify core files (`run_agent.py`, `cli.py`, `gateway/run.py`, `hermes_cli/main.py`). If a plugin needs a capability the framework doesn't expose, expand the generic plugin surface (new hook, new ctx method) — never hardcode plugin-specific logic into core." PR #5295 enforced this by removing 95 lines of hardcoded plugin-specific logic from `main.py`. The prior ADR's "overlay layer" posture explicitly contemplated touching upstream files. That posture is incompatible with how Hermes expects extensions to work.

2. **The plugin surface is sufficient.** Hermes exposes documented lifecycle hooks (`pre_tool_call`, `post_tool_call`, `pre_llm_call`, `post_llm_call`, `transform_tool_result`, `on_session_start`, `on_session_end`, `pre_gateway_dispatch`), a context API for registering tools and CLI subcommands (`ctx.register_tool`, `ctx.register_hook`, `ctx.register_cli_command`, `ctx.register_skill`), and as of v0.14.0 a `ctx.llm` accessor that lets plugins invoke the active LLM through the host's credentials. Working precedent for substantial plugin-based extensions exists at `eggyrooch-blip/hermes-multitenancy` (~250 KB, MIT, multi-tenant routing through `pre_gateway_dispatch`).

3. **The upstream multi-tenant work is community-authored and unmerged.** PRs #28020, #25660, #24423, #12054, #20096 — all P3, all zero core-maintainer engagement. Teknium's own profile fix (PR #31290, merged 2026-05-24) is a soft-guard, not architectural. The signal is that multi-tenant in Hermes is meant to be an application-layer plugin concern, not an upstream architectural feature. SMD's per-customer Machine isolation (ADR 0007) is a deployment-level pattern that does not require upstream changes.

## Decision

**SMD treats the Hermes fork as a pin target only. All SMD-specific code lives in a separate open-source plugin repo (`venturecrane/hermes-smd-overlay`). No SMD code lives in the Hermes fork.**

Concretely:

### The fork (`venturecrane/hermes-agent`)

- **Pin to upstream tags, byte-for-byte.** Initial pin: `v2026.5.16` (Foundation Release). Fork tag: `v2026.5.16-smd.0` — the `.0` suffix asserts "this pin carries zero overlay tags." Subsequent pins without patches keep `.0`; any overlay-tag bump increments to `.1`, `.2`, etc.
- **No source-code modifications, ever, under normal operation.** Not in the overlay sense (no `smd/` subdirectory), not in the modification sense (no edits to upstream files), not in the addition sense (no SMD-specific scripts under the fork root).
- **Fork's `README` documents the pin policy, tag scheme, and security-patch escape valve only.** No SMD product code is referenced or hosted here.
- **Fork's `CHANGELOG` is empty** until and unless we have an upstream PR to track from a genuinely-contributable SMD-developed change.

### Security-patch escape valve

The "no patches, ever" CI rule is the rule for normal operation, not for security incidents. If upstream has not shipped a fix for a CVE that materially affects customer Machines, Captain may authorize a vendored emergency patch under a tag pattern `v{upstream}-smd.security.{n}` (e.g., `v2026.5.16-smd.security.0`). Each emergency patch documents in the fork's `CHANGELOG`:

- The CVE reference and severity rationale.
- The upstream issue/PR (if filed; we file upstream as part of the emergency-patch workflow).
- A forced removal date — emergency patches must be upstream-merged or have a clear retirement plan within 30 days of the security tag.

Nothing else bypasses the CI rule. The `customer.yaml` validator regex is updated to accept the `security.N` suffix: `^v\d{4}\.\d{1,2}\.\d{1,2}-smd\.(security\.)?\d+$`.

### Upstream-monitoring posture

Before any customers are in production, Captain notices Hermes releases through normal ecosystem engagement (Discord, X, the release notes for tag-bump pulls). We do **not** pre-build automated CVE polling tooling. When we have a customer in production, we revisit this; at that point a GitHub Actions workflow polling upstream's releases endpoint daily becomes a small, justified cost. The escape valve above is what we need beforehand.

### The overlay (`venturecrane/hermes-smd-overlay`)

All SMD-specific extension code lives in a separate open-source repo, MIT-licensed, hosted at `github.com/venturecrane/hermes-smd-overlay`. Layout per the locked build plan §4:

- **Four narrow plugins** — `hermes-smd-audit`, `hermes-smd-trust`, `hermes-smd-voice`, `hermes-smd-memory-mirror`. Each ships as a Hermes plugin (drop-in or pip-installable) that registers against the documented hook surface.
  > **Count note (2026-07-13).** The overlay plugin set has since grown from these four to **twelve** (adding `hermes-smd-inbound`, `-jobs`, `-reply`, `-peer-memory`, `-webhook-router`, `-workspace`, `-hook-probe`, `-mcp-result-sink`, and others as ADRs 0021/0028/0045/etc. landed). The durable invariant is the **posture** — narrow, single-responsibility hook plugins with no core-file modifications — **not the count of four**; treat "four" here as the founding set, not a cap. Downstream ADRs that quote "one of the four" / "the four overlay plugins" (e.g. 0016, 0019) inherit this correction.
- **`shared/` package** — common code (D1 client, customer-config loader, env-var credential access) imported by the plugins; not registered as a plugin itself.
- **`bootstrap/` package** — ships a `hermes-smd bootstrap` CLI that translates `customer.yaml.personas[]` into N per-profile `config.yaml` + `SOUL.md` at Machine startup. Invoked from the Machine container entrypoint before Hermes launches.
- **`docs/hook-surface.md`** — first-source citations (file:line) for every Hermes hook the plugins register against, refreshed at every Hermes rebase. The §0 prerequisite of the locked build plan produces this file.

Distribution: `hermes plugins install venturecrane/hermes-smd-overlay --enable`. The clone places the four plugins under `~/.hermes/plugins/`; the `bootstrap/` CLI is invoked from the Machine's container entrypoint per ADR 0019 (forthcoming).

### Customer.yaml `hermes_ref` field

Continues to pin per-customer (matching the per-customer Machine pin pattern in ADR 0007). The validator enforces the fork-tag pattern. The fork tag's upstream provenance is derivable from the suffix scheme (e.g., `v2026.5.16-smd.0` → upstream `v2026.5.16`).

## Alternatives Considered

### Pattern 1: Upstream-only, no fork

Track upstream `NousResearch/hermes-agent` at a pinned ref directly, no SMD fork.

**Rejected.** We need a stable reference point that we can guarantee against. Upstream may force-push, retag, or remove tags. Having a fork we control as the pin source insulates customer Machines from upstream metadata changes. The fork costs nothing operationally as long as we hold the no-patches discipline.

### Pattern 2: Thin vendored fork with overlay layer (prior ADR version)

The prior ADR specified an SMD overlay layer (`smd/` subpackage in the fork) hosting safety_substrate hooks, with the option to modify upstream files where a hook didn't exist.

**Rejected.** Teknium's May 2026 plugin policy forbids exactly this pattern in upstream-acceptable extensions. PR #5295 demonstrates the enforcement. Building the overlay this way means our quarterly rebases compound conflicts, and our proposed upstream PRs for "expand the plugin surface" arguments get rejected because we've already done the work as a fork patch. The dependency direction goes the wrong way over time.

### Pattern 3: Hard fork

Fork the project, accept maintenance ownership of the entire codebase, do whatever we need.

**Rejected.** Hermes ships 459+ PRs per week and 13 minor releases in 10 weeks. The maintenance cost on a single-Captain venture is prohibitive. The "Hermes-leaning" Phase 1 posture (ADR 0004) was chosen specifically to inherit upstream's roadmap; hard-forking forfeits that inheritance.

### Pattern 4: Pin-only fork + separate open-source plugin overlay (this decision)

Selected. The fork is a pin target; the overlay is a plugin repo built against Hermes' documented extension surface. We work _with_ Hermes' contribution model rather than against it. Open-sourcing the overlay signals craft, builds maintainer goodwill in the natural course (not as performance), and costs us nothing — the proprietary value is in the SMD backend (audit DB, voice training, admin console, customer onboarding), not in the hook code.

## Consequences

**Positive.**

- Fork rebase cost approaches zero. No SMD code lives in the fork. Upstream tags can be pulled and re-pinned without merge conflicts.
- Quarterly rebase becomes a tag-bump exercise (re-pin `v2026.5.16-smd.0` → `v2026.5.17-smd.0`), not a code-merge exercise.
- The overlay is testable in isolation. The `tests/` directory in `venturecrane/hermes-smd-overlay` exercises each plugin against a stock Hermes install via fixtures.
- Upstream contribution becomes natural rather than performative. If during plugin development we identify a hook the plugin surface should expose, we file an upstream issue with concrete justification and our use case. Acceptance is independent of any pre-existing fork relationship.
- AGPL § 13 unmodified-deployment safe harbor extends cleanly to Honcho (sibling decision in ADR 0016) because the overlay does not modify Honcho either.

**Negative / accepted.**

- We give up the ability to ship a custom seam in the fork ahead of upstream. If Hermes' plugin surface lacks a hook we need, we either propose it upstream (and wait), redesign the feature against available hooks, or accept a degraded version. The locked plan's §0 hook surface verification catches this early.
- The security escape valve exists as an explicit exception to the no-patches discipline. Discipline depends on Captain not over-using it; the 30-day retirement requirement and the CHANGELOG documentation are the structural guardrails.
- A future upstream relicensing event (Hermes moves from MIT to a copyleft) would force a fresh decision. The fork insulates us short-term; the migration path is the Phase 2 question.

## Verification

How we know we are following this decision:

1. **The fork tree matches upstream byte-for-byte at the pinned tag.** CI on customer Machine image builds compares the installed Hermes commit SHA to the recorded upstream commit SHA; mismatch fails the build.
2. **The fork repo contains no SMD-specific source.** A directory listing of `venturecrane/hermes-agent` shows only the upstream tree plus optional `README.md` and `CHANGELOG.md` SMD documentation files.
3. **All SMD plugin code lives in `venturecrane/hermes-smd-overlay`.** No `smd/`, `aie/`, or similar subdirectory inside the Hermes fork.
4. **No core-file edits on any pinned tag.** Verification via `git diff v2026.5.16 venturecrane/hermes-agent:v2026.5.16-smd.0` returns empty for all pinned tags (unless a `security.N` tag is in play, in which case the diff is constrained to the patched files and documented in CHANGELOG).
5. **The `hermes_ref` validator enforces the tag pattern.** A `customer.yaml` with `hermes_ref: v2026.5.16` (raw upstream) fails validation; `v2026.5.16-smd.0` passes; `v2026.5.16-smd.security.0` passes.

## References

- Hermes upstream plugin policy: `AGENTS.md` in [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) (Teknium, May 2026)
- [Hermes PR #5295](https://github.com/NousResearch/hermes-agent/pull/5295) — enforcement of the no-core-modification rule
- [Hermes PR #31290](https://github.com/NousResearch/hermes-agent/pull/31290) — Teknium's profile soft-guard, Captain-merged 2026-05-24, signals architectural-vs-application boundary
- [`eggyrooch-blip/hermes-multitenancy`](https://github.com/eggyrooch-blip/hermes-multitenancy) — community precedent for substantial plugin-based extension
- [ADR 0004](./0004-productized-operator-offering.md) — the SKU this fork posture supports
- [ADR 0007](./0007-per-customer-machine-isolation.md) — per-customer Machine isolation; the per-customer pin attaches via `hermes_ref`
- [ADR 0016 (rewrite)](./0016-honcho-disposition.md) — the symmetric "no patches to upstream code" discipline applied to Honcho
- [ADR 0017 (rewrite)](./0017-skill-curator-disposition.md) — the symmetric "trust native, mirror, don't gate" posture applied to skill creation
- AGPL § 13 analysis for the symmetric Honcho posture, locked plan §5
- Locked Hermes-alignment build plan dated 2026-05-24
