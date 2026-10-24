---
title: customer.yaml → Per-Profile Hermes Config Translation — Bootstrap CLI, Deterministic, Idempotent
date: 2026-05-24
status: accepted
captain: Scott Durgan
supersedes: none
related-spec: docs/specs/operator/customer-yaml-schema.md
related-issue: TBD (filed as follow-on to the locked Hermes-alignment plan dated 2026-05-24)
---

# ADR 0019 — customer.yaml → Per-Profile Hermes Config Translation

**Status:** Accepted (Captain decision, 2026-05-24).

**Source:** The locked Hermes-alignment build plan dated 2026-05-24, §6 (Machine architecture rework). The locked plan introduced a customer-Machine startup step that translates the customer-authored `customer.yaml` into Hermes-native per-profile `config.yaml` + `SOUL.md` files at boot. That translation deserves its own ADR because it is the seam between the SMD product surface (which speaks "customer," "persona," "connector," "trust ceiling") and the Hermes runtime surface (which speaks "profile," "model provider," "MCP server config," "personality file"). Getting this seam right is what makes the rest of the plugin overlay tractable.

## Context

The product surface SMD authors and customers interact with is described in `customer.yaml` (`docs/specs/operator/customer-yaml-schema.md`). Its vocabulary: `customer_id`, `personas[]`, `connectors{}` keyed by capability, `scope`, `escalation`, `voice_library`, `memory`. This vocabulary maps onto SMD-product concepts: a customer's identity, the AI persona(s) representing them, the vendor systems they use, the safety envelope, the voice substrate, the per-customer storage namespacing.

The runtime surface Hermes actually consumes is described in its own per-profile `~/.hermes/profiles/<slug>/config.yaml` plus `SOUL.md`, plus the upstream `~/.hermes/.env` for secrets, plus optional MCP server configs. Hermes' vocabulary: `profile`, `model`, `mcp_servers`, `personalities`, `memory.honcho.*`, `agent`, `terminal`, `compression`. These are concepts the Hermes maintainers chose; they predate the SMD product.

Two distinct customer.yaml change classes have different operational implications:

1. **Structural changes** — adding/removing a persona, swapping an adapter backend, changing a `connectors{}.backend:` prefix, adding a new capability binding. These change the profile directory layout or the loaded toolset; they require a Machine restart and (typically) a Captain re-provision because OAuth tokens on the Fly volume per ADR 0010 need to remain undisturbed.
2. **Non-structural changes** — escalation contact updates, voice-sample additions, scope rule tweaks, trust-ceiling adjustments on existing skills. These change config values inside the existing profile layout; they can be applied via SIGHUP/reload without a restart.

The translation step must (a) be deterministic so the same customer.yaml produces byte-identical profile configs each run, (b) be idempotent so re-running it (after an R2 sync) doesn't duplicate or corrupt state, (c) classify changes correctly, and (d) be testable in isolation against fixtures.

## Decision

**A `hermes-smd bootstrap` CLI, shipped in `venturecrane/hermes-smd-overlay/bootstrap/`, performs the translation. The Machine container entrypoint invokes it before launching Hermes. A `customer-sync` sidecar polls R2 for non-structural changes and triggers reloads without restart; structural changes are logged for Captain re-provision.**

### Translation steps (one-shot at boot, idempotent on re-run)

For each persona in `customer.yaml.personas[]`:

1. **Profile directory.** Create or ensure `~/.hermes/profiles/<persona.slug>/` exists with subdirectories `skills/`, `memories/`, `logs/`.
2. **`SOUL.md`.** Write the persona identity:

   ```markdown
   # <persona.name>

   <persona.title>. <one-sentence purpose statement derived from persona.tone, skills, and customer.vertical>.

   Voice characteristics:
   <each item from persona.tone[]>

   Voice sample anchors at: r2://vaults/<customer.customer_id>/voice/samples/

   <if customer.escalation.red_flag_recipients exists>
   When the conversation surfaces a red-flag pattern, escalate to:
   <each recipient>
   ```

   Idempotent: regenerated from inputs each run; the same inputs produce byte-identical output.

3. **`config.yaml`.** Translate to Hermes-native shape:
   - `model:` ← `customer.model`
   - `memory:` ← Honcho-tuned defaults from ADR 0016 rewrite (`recallMode: hybrid`, `dialecticCadence: 3-5`, `dialecticDepth: 1`, `injectionFrequency: every-turn`, `writeFrequency: session`, observation_gates per persona); `memory.honcho.peer_id` ← `<customer.customer_id>:<persona.slug>`; `memory.honcho.endpoint` ← `http://localhost:8000` (local Honcho sidecar)
   - `mcp_servers:` ← for each `customer.connectors{}` entry with `backend: mcp:<name>`, an `mcp_servers.<name>` entry with stdio command + env from the connector's adapter config; the bootstrap also writes any required OAuth token file references from `connectors{}.token_ref` after env-injection
   - `plugins:` ← enable the four overlay plugins (`hermes-smd-audit`, `hermes-smd-trust`, `hermes-smd-voice`, `hermes-smd-memory-mirror`); pass per-plugin config (D1 namespace from `customer.memory.d1_namespace`, R2 voice path from `customer.voice_library.samples_path`, trust ceilings from `persona.skills[].trust_ceiling`)
   - `skills:` ← `external_dirs:` pointing at the symlinked per-persona skills directory (next step)
   - `personalities:` ← single-entry `default:` whose content is a brief reference to `SOUL.md`; we do not use Hermes' `/personality` command for product persona switching (that's `/handoff` to another profile per ADR 0011)
4. **Skill symlinks.** For each entry in `persona.skills[]` with `enabled: true`, symlink the canonical skill catalog at `/app/skills/<skill.name>/` into `~/.hermes/profiles/<persona.slug>/skills/<skill.name>/`. Skills with `enabled: false` are not symlinked.
5. **Per-profile resolved manifest.** Write `~/.hermes/profiles/<persona.slug>/.smd-manifest.json` with the source `customer.yaml` SHA, the per-plugin config snapshot, the active skill name+version pins, and the bootstrap timestamp. Used by `customer-sync` for non-structural change detection.

### customer-sync sidecar (non-structural change detection and reload)

> **Deferred — not launched in Phase 1 (note added 2026-07-13).** The `customer-sync` R2-polling sidecar described in this section was **not launched** (`operator/templates/bootstrap.sh:16` marks it "NOT launched in Phase 1"; the manual push-to-R2-then-restart stand-in that followed was deleted by the [ADR 0094](./0094-customer-configuration-as-built.md) amendment, which leaves one writer). Its live-reconfiguration role was taken over by [ADR 0044](./0044-r2-authoritative-live-reconfig.md) (R2-authoritative live reconfiguration via a root-owned `config_applier` poll loop). Phase-1 reloads happen via full Machine restart, which preserves the volume and its OAuth tokens (per ADR 0010). Read the rest of this section as the original design intent, superseded by 0044.

A separate process (`hermes-smd customer-sync`) polls R2 for the customer.yaml file at a configurable cadence (default 5 minutes). On detected change:

1. Fetch the new customer.yaml.
2. Validate it against the schema; on failure, log to admin portal and **do not apply** — keep the previous version in effect.
3. Diff against the current resolved manifest. Classify each diff entry:
   - **Non-structural** (escalation contacts, voice samples references, trust-ceiling values on existing skills, scope keyword/folder edits, `dismissed_*` policies, archival cadences) → write the new file to the volume, regenerate per-profile `config.yaml` (the symlinks and `SOUL.md` are unchanged for non-structural edits), SIGHUP each Hermes profile process.
   - **Structural** (persona slug added/removed/renamed, `personas[].skills[]` set changed in membership, `connectors{}` adapter or backend prefix changed, `memory.d1_namespace` changed, `hermes_ref` changed) → log a warning, post a "Captain re-provision required" event to the admin portal, do **not** rewrite anything on disk.
4. Emit an `hermes-smd-audit` row of type `CUSTOMER_YAML_SYNCED` or `CUSTOMER_YAML_STRUCTURAL_CHANGE_DEFERRED` depending on classification.

The structural-vs-non-structural cut is the safety boundary: OAuth tokens on the volume per ADR 0010 must not be destroyed by an in-place sync. A persona-add change requires a fresh provisioning pass (which preserves the volume) — that's where the OAuth tokens stay safe.

### Validation hooks

The TS validator (`src/lib/operator/customer-yaml/`) is the canonical schema validator and runs on PR review and at provisioning. The Python translation in `bootstrap/translate.py` performs a **runtime re-validation** to defend against (a) corrupted volume content, (b) R2-source drift relative to the TS validator's expectations, (c) test-fixture drift. The Python validator is a thin re-check, not a replacement — the TS validator is the source of truth.

## Alternatives Considered

### Pattern 1: Inline translation in `bootstrap.sh` (bash)

Have the container entrypoint shell-out to `yq` or similar to produce the per-profile configs.

**Rejected.** customer.yaml has structural complexity (nested arrays, conditional bindings, capability-vs-connector wiring) that is uncomfortable in bash. Python translation is testable, type-checkable, and reusable from the customer-sync sidecar.

### Pattern 2: Pre-translate at provisioning time, ship per-profile configs to the volume

The provisioning script (`bin/provision-customer.sh`) renders the per-profile configs and ships them; the Machine entrypoint reads them directly.

**Rejected.** Removes the runtime-revalidation safety net. Also doesn't support the `customer-sync` sidecar's non-structural-reload behavior (which requires re-translation in-place).

### Pattern 3: Bootstrap CLI in the overlay repo + customer-sync sidecar (this decision)

Selected. Localizes the translation logic in code that's plugin-adjacent, testable, and reusable. The structural-vs-non-structural cut lives in one place.

## Consequences

**Positive.**

- The translation is testable in isolation. `tests/test_bootstrap_translate.py` exercises customer.yaml fixtures against expected profile-config outputs.
- Non-structural changes apply without a Machine restart, preserving OAuth tokens on the volume.
- The `.smd-manifest.json` per profile provides a clean diff surface — the sidecar can detect what changed and classify it.
- Captain has explicit visibility into when a structural change has been queued for re-provision (the admin portal event), preventing silent drift.

**Negative / accepted.**

- A bug in the Python translator can cause a Machine to start with an inconsistent profile config. The runtime re-validation catches structural problems; behavioral bugs (e.g., wrong trust ceiling per skill) only surface at agent runtime. The `tests/test_bootstrap_translate.py` fixtures need to cover the common cases.
- The non-structural / structural classification is policy. If a customer's expected change classification doesn't match the code's, the customer sees either an unexpected restart (we classified non-structural as structural) or no behavior change (we classified structural as non-structural and didn't trigger re-provision). The classification list above is conservative — when unsure, classify as structural.
- The sidecar polls R2; cost is one R2 LIST + zero-or-one R2 GET per cadence per customer. At 5-minute cadence with 10 customers, that's 2,880 LIST + occasional GET operations per day, well under R2 free-tier limits.

## Verification

1. **Bootstrap translates a known fixture to a known output.** `pytest tests/test_bootstrap_translate.py` against `operator/templates/customer-no-pm-system.yaml` produces a profile config that matches a checked-in golden.
2. **Idempotency.** Running `hermes-smd bootstrap` twice in succession produces no file changes on the second run (compared via `sha256sum` of every generated file).
3. **Structural change is rejected (not applied) by the sidecar.** Synthetic customer.yaml change in R2 that renames a persona slug produces an admin-portal event and no file changes on the volume.
4. **Non-structural change is applied without restart.** Synthetic customer.yaml change in R2 that adds an escalation contact triggers a config rewrite and SIGHUP; the Hermes process PID does not change.
5. **OAuth tokens survive non-structural sync.** Verify `/opt/data/oauth/*.json` files are byte-identical before and after a non-structural sync.

## References

- Locked Hermes-alignment build plan dated 2026-05-24, §6 (Machine architecture rework)
- [`docs/specs/operator/customer-yaml-schema.md`](../specs/operator/customer-yaml-schema.md) — the canonical customer.yaml schema
- [`operator/templates/customer-no-pm-system.yaml`](../../operator/templates/customer-no-pm-system.yaml) — the most common template
- [ADR 0006 (rewrite)](./0006-capability-adapter-pattern.md) — connector backend prefixes
- [ADR 0007](./0007-per-customer-machine-isolation.md) — per-customer Machine model
- [ADR 0010](./0010-per-customer-oauth-token-storage.md) — OAuth token storage on volume; the structural-vs-non-structural cut protects this
- [ADR 0011 (rewrite)](./0011-multi-persona-per-customer.md) — persona = profile; this ADR is how the translation produces the profile directories
- [ADR 0012](./0012-customer-yaml-storage.md) — git as source of truth + R2 as materialized replica; the sidecar reads from R2
- [ADR 0015 (rewrite)](./0015-hermes-fork-vs-upstream.md) — plugin-only overlay; the bootstrap CLI lives in the overlay repo
- [ADR 0016 (rewrite)](./0016-honcho-disposition.md) — Honcho config knobs the translation applies per profile
- [ADR 0017 (rewrite)](./0017-skill-curator-disposition.md) — `skill_manage` stays enabled in profile configs
