# operator/templates

Templates an operator copies during provisioning. Each file is read once at provision time and never imported by runtime code — runtime configuration lives under `operator/customers/{customer-slug}/` and the per-Machine R2 vault at `s3://${R2_BUCKET_CONFIG}/vaults/<slug>/`.

## Overview

A customer Machine is a single Fly.io Machine running one container that supervises multiple processes under `tini`. Inside that container:

- **Hermes Agent** — the substrate. Skills, profiles, the tool registry, the plugin hook surface, and MCP integration are all Hermes-native. We do not modify core files (ADR 0015, plugin-only overlay). Pinned via `customer.yaml.hermes_ref` to an upstream release (`vYYYY.M.D@<40-hex-sha>`) on `NousResearch/hermes-agent` directly — ADR 0024 retired the `venturecrane/hermes-agent` fork. The Dockerfile's stage-1 clone fetches the pinned upstream tag and asserts the cloned commit SHA matches the SHA carried in `hermes_ref`; divergence (an upstream re-tag, or a stale pin) fails the build. This is the integrity check that proves we ship unmodified upstream Hermes (ADR 0024).
- **Memory (ADR 0016, revised 2026-05-30)** — Phase 1 runs on Hermes' always-on flat-file core (`MEMORY.md`/`USER.md`), which Hermes auto-creates and maintains at profile boot. The customer-owned memory file lives in D1/R2. **Honcho** — the inferred-memory engine — is a swappable provider that sits behind that file and is **deferred to Phase 2**; the earlier in-container `honcho-ai` server install was fictional (that package is the client SDK, not the server). **Postgres** and **Redis** remain installed in the image for the Phase-2 Honcho data plane but are **not started** in Phase 1.
- **hermes-smd-overlay plugins** — narrow plugins installed at image-build time from `venturecrane/hermes-smd-overlay`:
  - `hermes-smd-audit` — per-tool / per-LLM audit emission to per-customer SQLite.
  - `hermes-smd-trust` — trust-ceiling enforcement + Composio per-connection guard.
  - `hermes-smd-voice` — sample-driven voice transformation.
  - `hermes-smd-memory-mirror` — mirrors Honcho conclusions to per-customer SQLite with provenance. Inert in Phase 1 (no Honcho to poll); active in Phase 2.
- **customer-sync sidecar** (from the overlay's `bootstrap/` package) — polls R2 for non-structural `customer.yaml` changes at a 5-minute cadence.

No public HTTP. All inbound traffic is SSH via `fly ssh console -a hermes-<slug>`.

## Runtime templates

| File                | Used by                       | Purpose                                                              |
| ------------------- | ----------------------------- | -------------------------------------------------------------------- |
| `Dockerfile`        | `bin/provision-customer.sh`   | Hermes Machine image; per-customer Fly app builds from this          |
| `fly.toml.template` | `bin/provision-customer.sh`   | Fly app config; placeholders resolved per-customer at provision time |
| `bootstrap.sh`      | Per-customer Fly Machine boot | First-run substrate setup inside the Machine                         |

## Provisioning a new customer

The flow runs as `operator/bin/provision-customer.sh <slug>` from the repo root.

### Operator prerequisites

Set these in your local shell (e.g., via `.envrc` + `direnv`) before running provisioning. The R2 credentials below are used by `aws s3 cp` for the customer.yaml upload step; the Machine gets its own R2 credentials staged via the `pbpaste` flow.

| Env var                | Purpose                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| `R2_ENDPOINT_URL`      | Cloudflare R2 endpoint (e.g., `https://<account-id>.r2.cloudflarestorage.com`)                |
| `R2_ACCESS_KEY_ID`     | R2 access key with R/W on the config bucket                                                   |
| `R2_SECRET_ACCESS_KEY` | R2 secret paired with the access key                                                          |
| `R2_BUCKET_CONFIG`     | R2 bucket holding `customer.yaml` + voice vaults (defaults to `smd-customer-config` if unset) |

Tools required on the operator machine: `fly`, `aws` (any version with S3-compatible `--endpoint-url`), `pbpaste` (macOS), `python3`, `uv`.

### Provisioning steps

`provision-customer.sh` executes the following sequence. Every step is idempotent — re-running the script after a partial failure picks up where it left off.

1. **Validate `customer.yaml`** via `npx tsx scripts/validate-customer-yaml.ts` (the canonical TS validator at `src/lib/operator/customer-yaml/` per ADR 0019). Fail-fast on schema errors.
2. **Upload `customer.yaml` to R2** at `s3://${R2_BUCKET_CONFIG}/vaults/<slug>/customer.yaml`. This happens BEFORE the Fly deploy so the first Machine boot can fetch it. The customer-sync sidecar polls this same key for non-structural updates.
3. **Render `fly.toml`** from `fly.toml.template` to `operator/.rendered/<slug>/fly.toml` (gitignored).
4. **Create the Fly app** `hermes-<slug>` (skipped if it exists).
5. **Create the 10GB volume** `hermes_state` in the customer's region (skipped if it exists). 10GB hosts customer.yaml + audit SQLite + Hermes profiles (incl. flat-file memory) + voice cache + OAuth tokens with headroom (Phase 2 adds Postgres + Redis + observations SQLite).
6. **Stage secrets via the pbpaste flow.** For each secret, the operator copies the value to clipboard and presses Enter; the value flows from `pbpaste` directly into `fly secrets import --stage` over stdin. Values never appear on the command line, in the terminal, or in any chat transcript. The set of staged secrets:
   - `ANTHROPIC_API_KEY` — model access for Hermes.
   - `COMPOSIO_API_KEY` — long-tail connector access.
   - `AGENTMAIL_API_KEY` — agent-side mailbox.
   - `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_ENDPOINT_URL` — Machine-scoped R2 credentials used by `bootstrap.sh` and the customer-sync sidecar.
   - `HONCHO_API_KEY` — **deferred to Phase 2** (no in-Machine Honcho server in Phase 1; nothing generated).
7. **Deploy** with `fly deploy` against the rendered `fly.toml`.
8. **Boot smoke test** (`bin/boot-smoke-test.sh <slug>`) — see the section below.
9. **Per-connector prod smoke tests** (`run_prod_smoke_test.py`) — one read-only call per enabled BUILD or COMPOSIO connector against the customer's tenant. Surfaces auth / scope / shape issues before any write capability is exercised.

### Non-secret config in fly.toml

These are set as `[env]` entries in `fly.toml.template` (rendered per-customer):

- `R2_BUCKET_CONFIG` — the bucket name (NOT a credential).
- `SMD_D1_AUDIT_BINDING` — defaults to `/opt/data/audit.db`.
- `SMD_D1_OBSERVATIONS_BINDING` / `HONCHO_BASE_URL` / `HONCHO_DATABASE_URL` — **deferred to Phase 2** (no Honcho in Phase 1; these env entries are not rendered).
- `HERMES_HOME` — `/opt/data`.
- `CUSTOMER_SLUG` — the slug, propagated for log filtering and skill resolution.

## Storage layout on `/opt/data`

The 10GB Fly volume mounts at `/opt/data` and hosts everything stateful. `customer.yaml` is volume-write at provisioning and R2-mirrored for non-structural updates (ADR 0019).

| Path                         | Contents                                                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `/opt/data/customer.yaml`    | Live customer config. Fetched from R2 at first boot; refreshed by sidecar.                                                  |
| `/opt/data/honcho/pg/`       | Postgres data dir — **Phase 2** (Honcho's store; unused in Phase 1).                                                        |
| `/opt/data/honcho/redis/`    | Redis AOF — **Phase 2** (Honcho's cache; unused in Phase 1).                                                                |
| `/opt/data/audit.db`         | Per-customer audit log SQLite (`hermes-smd-audit` writes).                                                                  |
| `/opt/data/observations.db`  | Honcho-mirror SQLite (`hermes-smd-memory-mirror` writes) — **Phase 2**. ADR 0016.                                           |
| `/opt/data/profiles/<slug>/` | Hermes per-persona profiles + flat-file memory (`MEMORY.md`/`USER.md`), one per `customer.yaml.personas[]`. ADR 0011, 0019. |
| `/opt/data/oauth/`           | Per-provider OAuth token files. ADR 0010.                                                                                   |
| `/opt/data/voice/`           | Voice samples warm cache (R2-backed).                                                                                       |

## Boot sequence (summary)

`bootstrap.sh` runs as the container entrypoint under `tini`. The Phase-1 sequence (steps 3–6, the Honcho data plane, are deferred to Phase 2 — see ADR 0016 revised):

1. Validate required env vars.
2. Fetch `customer.yaml` from R2 if `/opt/data/customer.yaml` is missing; otherwise use the volume copy.
   3–6. **Honcho data plane (Postgres / Redis / migrations / FastAPI) — deferred to Phase 2.** Phase 1 runs on Hermes' flat-file memory core.
3. Run `hermes-smd bootstrap` from the overlay repo — translates `customer.yaml.personas[]` into N profile directories under `/opt/data/profiles/`, writes each profile's `config.yaml` and `SOUL.md`.
4. Run the safety_substrate invariant checks (`/app/safety_substrate/run_invariants.py`).
5. Pause-guard check.
6. Start the `hermes-smd customer-sync` sidecar in the background (R2 polling).
7. `exec hermes gateway run` — the unattended gateway daemon (listens for cron + webhook triggers and drives them through the agent + overlay plugins). NOT `hermes chat`, which is an interactive REPL that would exit on EOF as PID-1's child.

The full step list and the exact failure-handling lives in `bootstrap.sh` itself; this section is a summary.

## Memory semantics (Phase 1)

Phase 1 runs on Hermes' always-on flat-file memory core (`MEMORY.md`/`USER.md`), which Hermes auto-creates and maintains per profile. This is in-session memory only: the customer-owned explicit memory (D1/R2 rules, person-mappings, voice) is **not on the runtime read path** until the tail-log drain (#821), and inferred memory (Honcho) is **deferred to Phase 2**. A first inbound message proves the harness (quarantine → draft → reviewer), not the product memory.

When Honcho lands in Phase 2, it degrades gracefully if unresponsive mid-session: the agent continues without Honcho writes for that turn, `hermes-smd-audit` emits `MEMORY_PROVIDER_DEGRADED`, and `MEMORY_PROVIDER_RECOVERED` on reconnect. We do not fail-closed — a memory-system bug should not brick the Operator, and draft-for-review external send catches any single bad draft regardless of memory state. ADR 0016 governs the mirror, the dismissal flow, and TTL archival.

## Updating customer.yaml

`customer.yaml` lives in three places:

- Git: `operator/customers/<slug>/customer.yaml` (source of truth, ADR 0012).
- R2: `s3://${R2_BUCKET_CONFIG}/vaults/<slug>/customer.yaml` (uploaded by provisioning, polled by sidecar).
- Volume: `/opt/data/customer.yaml` (mirror, refreshed by sidecar on R2 change).

The change rule (ADR 0019):

| Change kind        | Examples                                                     | What happens                                                                                                                              |
| ------------------ | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Non-structural** | Escalation contacts, voice samples, rule edits, scope tweaks | Sidecar diffs R2, rewrites the volume copy, signals each profile to reload its config. No Machine restart.                                |
| **Structural**     | Persona add/remove, adapter swap, new connector binding      | Sidecar logs a warning, posts to the admin portal that a Captain re-provision is required. No automatic restart (preserves OAuth tokens). |

Captain-initiated re-provision is the only path for structural changes; the OAuth tokens on the volume are deliberately scoped to the Machine lifetime per ADR 0010.

## Customer.yaml starter templates

| File                                                                           | When to use                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`../customers/_template/customer.yaml`](../customers/_template/customer.yaml) | The fully bracketed default. Use when the customer's capability bindings are not yet known — the assessment call fills them in.                                                                                              |
| [`customer-no-pm-system.yaml`](customer-no-pm-system.yaml)                     | The customer has no working practice-management system (paper + Outlook + OneDrive + QuickBooks for billing). Ships with `no_pm` PracticeManagement + Microsoft Graph + DocuSign + QuickBooks + OneDrive bindings pre-wired. |

Both pass through the same validator (`src/lib/operator/customer-yaml/validator.ts`); the bracketed-field shape rejects an unedited template at validation time, forcing the operator to substitute real values before provisioning.

## Onboarding artifacts

| File                                                   | When to use                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`ENTITLEMENTS.template.md`](ENTITLEMENTS.template.md) | Copy to `operator/customers/<slug>/ENTITLEMENTS.md` at onboarding. The forward, explicit surface where the **customer specifies** each entitlement (per action class × connector) and we only **recommend** defaults. Materializes into `customer.yaml` (`personas[].entitlements.exposure`, `inbound_allow_from`, connector grants). Doctrine: ADR 0035 (no imposed defaults). Worked example: `operator/customers/ashton-price/ENTITLEMENTS.md`. |

### no-PM-system mode

The most common state at the target-buyer profile is no working PM system at all. The `customer-no-pm-system.yaml` template is the matching capability binding set — see the spec at [`docs/specs/operator/no-pm-system-mode.md`](../../docs/specs/operator/no-pm-system-mode.md) for the scene-by-scene demo flow and the `no_pm` adapter README at [`../connectors/no_pm/README.md`](../connectors/no_pm/README.md) for the synthetic matter store. Issue [#853](https://github.com/venturecrane/ss-console/issues/853).

## Operating runbook

| Task                           | Command                                                                                                                                                |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Pause a Machine                | `fly machine stop -a hermes-<slug> <machine-id>`                                                                                                       |
| Decommission a customer        | `fly apps destroy hermes-<slug>` (irreversible; volume contents are lost)                                                                              |
| Run the boot smoke test        | `operator/bin/boot-smoke-test.sh <slug>` (customer.yaml → profiles → plugins → curator-disabled; Postgres/Redis/Honcho are Phase 2)                    |
| Run connector prod smoke tests | `uv run python3 operator/adapter/run_prod_smoke_test.py --customer <slug> --app hermes-<slug> --customer-yaml operator/customers/<slug>/customer.yaml` |
| Inspect logs                   | `fly logs -a hermes-<slug>`                                                                                                                            |
| Interactive shell              | `fly ssh console -a hermes-<slug>`                                                                                                                     |
| Generate evidence packet       | `fly ssh console -a hermes-<slug> --command "tar czf - /opt/data/audit.db /opt/data/observations.db" > evidence-<slug>-$(date +%Y%m%d).tar.gz`         |
| Force customer.yaml resync     | `fly ssh console -a hermes-<slug> --command "kill -HUP \$(pgrep -f customer-sync)"`                                                                    |

### Boot smoke test scope

`boot-smoke-test.sh` exercises the dependency chain only: Machine state, `customer.yaml` presence, profiles directory presence, overlay plugins installation, and curator-disabled. (The Postgres/Redis/Honcho-health checks are deferred to Phase 2 — ADR 0016 revised.) It does **not** run a real agent turn or exercise live MCP / Anthropic credentials. Those are the job of `run_prod_smoke_test.py` (per-connector) and the end-to-end test described in §6 of the build plan.

## ADR references

| ADR                                                           | Topic                                                                                        |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [0007](../../docs/adr/0007-per-customer-machine-isolation.md) | Per-customer Machine isolation (one Fly app, one Machine, per customer).                     |
| [0010](../../docs/adr/0010-oauth-token-storage.md)            | Per-customer OAuth token storage on the Fly volume.                                          |
| [0016](../../docs/adr/0016-honcho-disposition.md)             | Honcho disposition — mirror, don't gate; tuned config; TTL archival.                         |
| 0019                                                          | `customer.yaml` → per-profile config translation. Structural vs. non-structural change rule. |
| 0020                                                          | Connector strategy — `mcp:` / `build:` / `composio:` / `synthetic:` backend prefixes.        |
