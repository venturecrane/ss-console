# First boot — standing up a customer Machine

How Captain boots a customer's Operator Machine end to end for the first time. This is the [ADR 0024](../../adr/0024-hermes-consumption-and-update-cadence.md) / [ADR 0007](../../adr/0007-per-customer-machine-isolation.md) "first real boot" milestone: it provisions a dedicated Fly.io Machine, builds the customer-shape Hermes container (upstream Hermes pinned by SHA + the hermes-smd overlay), and runs the boot smoke test.

The first boot is **Captain-gated**: it spends on a real Fly.io org, stages real credentials, and consumes a real Anthropic key. Everything up to `fly deploy` is automated by `provision-customer.sh`; the go/no-go is yours.

## What "first boot" verifies (and what it does not)

The boot smoke test (`operator/bin/boot-smoke-test.sh`, run automatically as the last step of provisioning) confirms the **dependency chain comes up cleanly**: Machine `started` → Postgres → Redis → Honcho health → `customer.yaml` on the volume → Hermes profiles materialized → overlay plugins registered (`hermes plugins list | grep hermes-smd-`) → curator disabled ([ADR 0017](../../adr/0017-skill-curator-disposition.md)).

It does **not** exercise a real agent turn, a live LLM call, or a connector write. Those need the customer's OAuth tokens and are the next step after a clean boot (see "After a clean boot" below). The first boot proves the substrate stands up; it does not prove a task runs end to end.

## Prerequisites

### Tooling (operator machine)

- `fly` CLI, authenticated to the target org (`fly auth login`). The script creates the app with `--org personal`; change that in `provision-customer.sh` if booting into a different org.
- `aws` CLI (for the R2 `customer.yaml` upload), `openssl`, `pbpaste` (macOS).

### R2 credentials — two kinds (operator-local fetch creds: don't re-mint; skill-bodies token: DO mint, bucket-scoped)

The operator-local R2 creds are stored in **Infisical `/ss` (prod)** and are injected
automatically by the wrapper. **Use the wrapper** and you never touch them:

```
operator/bin/reprovision.sh <slug>
```

For months these were not stored anywhere and every agent re-derived them (a ~2h
trap). They are now in `/ss` and are **derivable from `CLOUDFLARE_API_TOKEN`** (also
in `/ss`) — Cloudflare R2's S3 API accepts a CF API token directly:

- `R2_ACCESS_KEY_ID` = the CF token's id (`GET /user/tokens/verify` → `result.id`)
- `R2_SECRET_ACCESS_KEY` = `sha256_hex(CLOUDFLARE_API_TOKEN value)`
- `R2_ENDPOINT_URL` = `https://<account_id>.r2.cloudflarestorage.com`
- `R2_BUCKET_CONFIG` = `smd-customer-config`

The same derived pair has R/W on every bucket in the account. It is now used ONLY
for the operator-local `customer.yaml` upload/fetch (`R2_BUCKET_CONFIG`), and is
**stripped from the agent env after the boot fetch** (`bootstrap.sh`). It is **no
longer** allowed to back the agent's `R2_SKILL_BODIES_*` bucket: that account-wide
key would otherwise sit in the agent process env (`skill_capture.py` reads it
in-process) as a cross-tenant crown jewel (OP-P0-2, see
[operator-threat-model.md](../../security/operator-threat-model.md)).

`R2_SKILL_BODIES_*` back the agent's ability to persist skills it **authors for
itself**. They are now **optional and fail-soft, with no account-wide fallback** —
closing OP-P0-2 created **no new key**; it removed the over-broad one:

- **Absent (the default, incl. customer-zero):** nothing is staged; the agent
  simply does not persist agent-authored skills (`load_r2_config_from_env` → None,
  graceful no-op, no crash, no boot impact). Customer-zero runs fixed repo skills
  (`inbox-triage`, `workspace`) and authors none, so this is the intended posture.
  **No key needs to exist.**
- **Turning the feature on (deliberate, later):** mint ONE **bucket-scoped** R2
  token (read+write on `ss-operator-<slug>-skills` only) and store it in `/ss`
  (prod) as `R2_SKILL_BODIES_ACCESS_KEY_ID` / `R2_SKILL_BODIES_SECRET_ACCESS_KEY`
  — no code change. Cloudflare dashboard → **R2** → **Manage R2 API Tokens** →
  **Create API token** → **Object Read & Write** → **Apply to specific buckets
  only** → `ss-operator-<slug>-skills`. **NEVER** reuse the account-wide pair.

To verify presence: `crane_secret_check({ path: '/ss', env: 'prod', names: ['R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','R2_ENDPOINT_URL','R2_BUCKET_CONFIG','R2_SKILL_BODIES_ACCESS_KEY_ID','R2_SKILL_BODIES_SECRET_ACCESS_KEY'] })`.

```
# Optional (warn-and-skip if unset): CF_API_TOKEN + CF_ACCOUNT_ID (per-customer skill-bodies
# bucket auto-create), SENTRY_DSN_OPERATOR, HEALTHCHECKS_API_KEY (ADR 0023 Wave 1).
# MACHINE_HEARTBEAT_KEY is not an input: the provisioner mints it per seat (migration 0114)
# and the console keeps only its hash.
```

### Overlay plugins are volume-shadowed (handled in bootstrap)

The Dockerfile installs the `hermes-smd-overlay` pack under `${HERMES_HOME}` (= the
Fly volume mount); on a **persisted** volume the build-time install is shadowed.
`bootstrap.sh` therefore re-installs + enables it at runtime (fail-closed) before the
gateway launches, so the trust/audit/voice safety harness always loads. The boot
smoke test step `hermes-plugins-installed` is the live verification.

### Secrets you'll be prompted to paste (clipboard → `fly secrets`, never echoed)

The script prompts for each; copy to clipboard, press Enter (or `s` to skip). Values flow straight into `fly secrets import --stage`:

- `ANTHROPIC_API_KEY` — the model key for this Machine.
- `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_ENDPOINT_URL` — Machine-scoped R2 access (R/W on `vaults/<slug>/`) so `bootstrap.sh` can fetch `customer.yaml` on boot.
- `R2_SKILL_BODIES_ACCESS_KEY_ID` / `R2_SKILL_BODIES_SECRET_ACCESS_KEY` — bucket-scoped to `ss-operator-<slug>-skills` ([ADR 0022](../../adr/0022-vertical-pack-architecture.md) Stream 2). Verify the token scope before pasting; the bucket is the trust boundary ([ADR 0007](../../adr/0007-per-customer-machine-isolation.md)).

`HONCHO_API_KEY` is generated locally (`openssl rand -hex 32`) and staged directly — no paste.

### Customer fixture

`operator/customers/<slug>/customer.yaml` must exist and validate. **customer-zero is `smd`** (`operator/customers/smd/customer.yaml`) — we dogfood the SMD Operator on SMD itself. Its `hermes_ref` is pinned (`v2026.5.16@a91a57fa…`); the Dockerfile clones `NousResearch/hermes-agent` at that tag and asserts the cloned HEAD matches the SHA.

## The command

From the **repo root**, use the wrapper (injects the R2 creds from Infisical `/ss`):

```bash
operator/bin/reprovision.sh smd
# fully non-interactive (skip the secret prompts — Machine secrets persist across deploy):
yes s | operator/bin/reprovision.sh smd
```

That single command: validates `customer.yaml` → uploads it to R2 → renders `fly.toml` → creates the Fly app + a 10 GB volume → prompts for secrets → `fly deploy` (builds the Dockerfile: clones+asserts upstream Hermes, installs the overlay **v0.4.5**, bundles Postgres/Redis/Honcho) → runs the boot smoke test. It is idempotent — safe to re-run.

## Overlay pin (why v0.4.5 matters)

The Dockerfile pins the overlay at **v0.4.5** (`OVERLAY_REF`). The runtime trust plugin imports `shared.outbound_gate`, which first shipped after v0.1.1. The build now hard-asserts the policy core is importable (`import shared.outbound_gate, shared.inbound, shared.action_classes`) — a stale pin fails the build instead of booting a harness-less Machine. If you bump the overlay, re-tag and bump `OVERLAY_REF` in lockstep. (Known follow-up: `hermes plugins install` clones the overlay's default branch and cannot pin a ref, so the _plugin surface_ tracks main HEAD at build time; for a tagged build, main == the tag.)

## What to watch on the FIRST ever boot

This is the first time the full chain runs on real infra, so budget for first-run surprises and watch the logs (`fly logs -a hermes-smd`):

- **Docker build** clones upstream Hermes + runs `npm install` + `playwright install chromium` + `uv sync` + web builds — it is a heavy, multi-minute build.
- **Hermes CLI surface** — bootstrap execs `hermes chat` and the overlay calls `hermes plugins …`; both are verified to exist at the pinned SHA, but a live boot is their first real exercise.
- **`hermes-smd bootstrap`** translating `customer.yaml` → profile config is first-exercised here.
- **Honcho `1.0.0`** schema migration runs against the in-container Postgres for the first time.

If the smoke test fails, the Machine is up but the chain is unhealthy: `fly logs -a hermes-smd` shows which step (`[bootstrap] FATAL: …` / `[smoke/smd] FAIL: …`).

## After a clean boot

1. `fly ssh console -a hermes-smd` — shell into the container.
2. `fly logs -a hermes-smd` — watch the agent loop.
3. Run the OAuth setup for the enabled connectors (Gmail / Calendar / Drive via MCP) inside the container so the agent can act on the principal's mailbox.
4. Drive a first real inbound message through quarantine → draft → outbound gate → reviewer to verify the harness end to end (the milestone-C "proven in life" step).

## Rollback / teardown

```bash
fly apps destroy hermes-smd            # removes the Machine + volume (irreversible)
```

For a non-destructive pause (keep state, stop the agent loop), use the pause sentinel path in `bootstrap.sh` step 9 rather than destroying the app.

## References

- `operator/bin/provision-customer.sh` — the provisioning entrypoint
- `operator/templates/bootstrap.sh` — in-container 11-step startup
- `operator/bin/boot-smoke-test.sh` — the dependency-chain smoke test
- [ADR 0024](../../adr/0024-hermes-consumption-and-update-cadence.md) (Hermes pin), [ADR 0007](../../adr/0007-per-customer-machine-isolation.md) (per-customer Machine), [ADR 0028](../../adr/0028-outbound-integrity-gates-provenance-and-voice.md) (overlay harness)
