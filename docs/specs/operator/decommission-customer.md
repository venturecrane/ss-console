# Decommission Customer Script

**Spec for issue [#820](https://github.com/venturecrane/ss-console/issues/820).** Full per-customer off-boarding sequence. Composes the existing `decommission_source` hooks (memory + voice) with substrate-deletion steps (R2, Vectorize, AgentMail, Fly), the compliance evidence packet archive, and the `customers/{slug}/` tombstone. Implements PRD §14.3 and the contractual obligation in §13.

## Source

- Platform PRD §13 (Compliance & Privacy Posture), §14.3 (Phase 1 ops deliverable)
- [R2 + Vectorize Naming](./r2-vectorize-naming.md) — per-customer namespace convention (#801)
- [OAuth Lifecycle](./oauth-lifecycle.md) — per-customer OAuth token lifecycle (build: adapters)
- [Compliance Evidence Packet](./compliance-evidence-packet.md) — packet structure (#802)
- [D1 Schema](./d1-schema.md) §1 — accepted `action_type` values

## Files

- `operator/bin/decommission-customer.sh` — shell wrapper, dispatches to the Python CLI
- `operator/bin/lib/decommission.py` — `DecommissionPipeline` + Protocols + NoOp stubs
- `operator/bin/lib/decommission_cli.py` — argparse-based CLI entrypoint, audit-writer construction
- `operator/bin/tests/test_decommission.py` — end-to-end tests against the `smd` fixture
- `operator/bin/fixtures/smd/` — synthetic customer-zero fixture for tests

## Contract

### Invocation

```
operator/bin/decommission-customer.sh <slug> [--dry-run]
operator/bin/decommission-customer.sh <slug> --live [--confirm-slug <slug>]
```

Default is `--dry-run`. A live run is confirmed by typing the slug a second time: on a terminal the wrapper prompts for it, and a non-interactive caller passes `--confirm-slug <slug>`. The Python CLI may also be invoked directly:

```
cd operator && uv run --quiet --with pyyaml python3 \
  -m bin.lib.decommission_cli <slug> [--dry-run|--live --confirm-slug <slug>] \
  [--customers-root PATH] [--archive-root PATH] [--audit-db PATH] [--actor NAME] \
  [--allow-unwired]
```

`--allow-unwired` tolerates unwired destructive backends on a live run. It is a fixture-only flag and is enforced as one: the CLI refuses it (exit 5) unless `--customers-root` was passed explicitly and resolves outside the repo's `operator/customers`. The flag skips only the unwired backends; every wired one still executes, so against the real customers root it would destroy a real seat while announcing an incomplete decommission.

### Exit codes

| Code  | Meaning                                                                                                                                                                          |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`   | Dry-run completed, or live decommission completed cleanly.                                                                                                                       |
| `2`   | Pre-flight failed (missing slug, no `customer.yaml`, no tombstone, or `--live` without `--confirm-slug` equal to the slug).                                                      |
| `3`   | Live decommission halted mid-sequence. Re-run the same slug to resume.                                                                                                           |
| `4`   | Unexpected non-step exception (audit writer init failure, etc.).                                                                                                                 |
| `5`   | Refused, nothing touched: a `--live` run found a destructive backend unwired (stderr names the credential), or `--allow-unwired` was given without a fixture `--customers-root`. |
| `130` | Interrupted by Ctrl-C.                                                                                                                                                           |

### The steps

The pipeline also runs a trailing `09_observability_cleanup` step (ADR 0023 Wave 1) not enumerated here; see `bin/lib/decommission.py`.

| #   | Step name                  | Action                                                                                                                                                                                                                                                                       | Idempotency mechanism                                                                              |
| --- | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 1   | `01_drain`                 | Verify in-flight LLM drain marker (#805 covers the pause).                                                                                                                                                                                                                   | Always re-runnable; records drain window seconds.                                                  |
| 2   | `02_preserve_machine_data` | Pull the Machine-local audit ledger + ADR-0016 memory tables through the runtime-read seam to the archive dir (`bin/lib/seam_pull.py`; pull-before-destroy, #1355). The ADR-0008 control-plane memory/voice sweep was removed — that store was never provisioned or written. | Preservation is idempotent per UTC date; a re-run with today's manifest present skips.             |
| 3   | `03_r2_namespace`          | Delete every R2 object under the seat's prefixes (`vaults/{slug}/`, `customers/{slug}/`, skills `{slug}/`) except a `decommission-archive/` subtree; the audit-archive bucket is never touched.                                                                              | Second call returns `skipped: true, reason: namespace_already_empty`.                              |
| 4   | `04_vectorize_indexes`     | Delete `hermes-{slug}-vault` and `hermes-{slug}-corrections`.                                                                                                                                                                                                                | Second call returns `skipped: true, reason: indexes_already_absent`.                               |
| 5   | `05_agentmail`             | `AgentMailInboxDeprovisioner`: resolve the seat's inbox the way the broker does, find it in `GET /v0/inboxes`, `DELETE /v0/inboxes/{id}` (inbox-scoped keys die with it). Wired from `AGENTMAIL_API_KEY`.                                                                    | Absent inbox returns `skipped: true`; unwired credential leaves the stub and `--live` refuses.     |
| 6   | `06_fly_machine`           | `FlyAppDestroyer`: `fly apps list --json`, then `fly apps destroy hermes-{slug} --yes` (the app, its Machine, volume and secrets). Wired from `FLY_API_TOKEN` only; a logged-in `fly` CLI does not arm it.                                                                   | Absent app returns `skipped: true`; unwired credential leaves the stub and `--live` refuses.       |
| 7   | `07_compliance_archive`    | Generate the compliance evidence packet per `compliance-evidence-packet.md` and copy to `archive_root/{slug}/`.                                                                                                                                                              | Each call writes a new timestamped manifest; the cold-storage retention policy handles overwrites. |
| 8   | `08_tombstone`             | Rename `operator/customers/{slug}/` to `{slug}.decommissioned.{iso-date}` and drop a `DECOMMISSIONED.md` marker.                                                                                                                                                             | Returns `skipped: true, reason: already_tombstoned` when the dated tombstone is present.           |

### Step 1 drain-window contract (#805)

`01_drain` is more than a marker write — it is the 60-second in-flight grace gate that makes atomic decommission (BR-013) achievable. Without it, D1 deletion races in-flight Anthropic responses returning mid-stream.

- **Pause, then poll.** After the agent is paused, poll `audit_log` every 5s for in-flight `DRAFT_%` activity in the trailing 60s window. Proceed as soon as the count hits zero, or when 60s elapse — whichever comes first.
- **Hard-kill at the boundary.** When the window elapses with calls still in flight, do not wait further: `fly machine stop hermes-{slug} --signal SIGKILL` terminates the runtime. Any in-flight Anthropic stream is dropped and no D1 write completes. The lost draft was mid-generation and never sent, so the cost is one truncated draft, not a customer-facing failure. Emit `DECOMMISSION_HARD_KILL` with `metadata = {in_flight_count: N, drain_duration_seconds: <window>}`.
- **Configurable per customer.** Default 60s. Override via `customer.yaml.decommission.drain_window_seconds`, bounded 30–300s (the script enforces the bounds). Heavy-draft workflows may request a longer window; fastest off-boarding may request shorter.

### Audit-log emission

Every step writes audit rows via `adapter.audit_log.AuditLogWriter`:

- Before the step runs: `DECOMMISSION_INITIATED` with `metadata.step = <step_name>`.
- After success: `DECOMMISSION_DRAIN_COMPLETE` with `metadata.step` plus the step's detail manifest.
- On failure: `DECOMMISSION_INITIATED` with `metadata.detail.failed = true` and the exception class + message.
- Final marker: `DECOMMISSION_FINAL` with `metadata.detail.steps = [...]`.

All three `action_type` values are in `ACCEPTED_ACTION_TYPES` in `adapter/audit_log.py` (Decommission lifecycle section). The local audit log is written to `customers/{slug}/.decommission-audit.sqlite` so the trail survives after the per-customer D1 is deleted by step 2.

### Real backends (2026-09-10)

Every destructive Protocol has a real implementation in `bin/lib/decommission_backends.py`, and the CLI wires each one from the environment through `backends_from_env`. A backend whose credential is absent stays a stub, and the `--live` gate (exit 5) refuses, naming the credential. Each backend is the inverse of what `provision-customer.sh` created and of what the account actually holds, which is not what the naming spec planned:

| Protocol                | Implementation                      | What it does                                                                                                                                                                                                                                                              | Needs                                                        |
| ----------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `R2NamespaceDeleter`    | `CloudflareR2NamespaceDeleter`      | Lists and deletes by prefix through the Cloudflare R2 API: `vaults/<slug>/` and `customers/<slug>/` in the shared `smd-customer-config` bucket, `<slug>/` in `ss-ai-employee-smd-skills`. Never touches `smd-audit-archive` (the retention copy).                         | `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` (or `CF_*`) |
| `VectorizeIndexDeleter` | `WranglerVectorizeIndexDeleter`     | `wrangler vectorize list --json`, then `wrangler vectorize delete` for whichever of `hermes-<slug>-vault` / `hermes-<slug>-corrections` exists. None exist today; absence is a skip.                                                                                      | `CLOUDFLARE_API_TOKEN`                                       |
| `AgentMailProvisioner`  | `AgentMailInboxDeprovisioner`       | Resolves the seat's inbox as the broker does (authored `connectors.Email.inbox_address`, else `<slug>@agentmail.to`), finds it in `GET /v0/inboxes`, `DELETE /v0/inboxes/{id}`. Inbox-scoped keys die with the inbox.                                                     | `AGENTMAIL_API_KEY` (org key)                                |
| `FlyMachineManager`     | `FlyAppDestroyer`                   | `fly apps list --json`, then `fly apps destroy hermes-<slug> --yes`: Machine, volume (`/opt/data`) and secrets in one act.                                                                                                                                                | `FLY_API_TOKEN` (a logged-in `fly` CLI does not count)       |
| `ObservabilityCleanup`  | `HealthchecksAndFleetStatusCleanup` | Deletes the `hermes-<slug>` healthchecks.io check (found by tag, deleted by uuid) and the seat's console D1 rows in `operator_runtime_summary`, `fleet_status` and `machine_credentials` (the Machine's control-plane bearer, revoked by the delete) through `ConsoleD1`. | `HEALTHCHECKS_API_KEY` + `CLOUDFLARE_API_TOKEN`              |

Every backend is idempotent: an already-absent object, index, inbox, app, or check is reported as `skipped` with a reason, never raised, so a halted run resumes cleanly. Every backend arms itself only from a staged credential; the Fly destroyer used to also arm from a logged-in `fly` CLI and no longer does, because the Captain's shell is logged in most of the time and the `--live` refusal had silently stopped covering the layer that takes the Machine, its volume and its secrets in one act. The `NoOp*` stubs remain as the default for dry runs and for `--allow-unwired` fixture runs. Tests: `bin/tests/test_decommission_backends.py` drives every backend with fake HTTP and subprocess transports on the exists / already-gone / vendor-error paths.

The observability manifest reports what was observed, not what was attempted. A `DELETE` through `wrangler d1 execute --json` returns an empty result set whether it removed a row or matched nothing, so each table is counted for the slug before and after the delete, and the manifest carries `<table>_rows_deleted` (before minus after), `<table>_rows_remaining` (the count read back), and `<table>_row_deleted`, which is true only when the table read back empty. On a D1 whose migrations predate `machine_credentials` (0114) the manifest says `machine_credentials_table_present: false` instead of raising. That is the negative probe the removal doctrine asks for, produced by the pipeline rather than by a hand sweep afterwards.

A tombstoned seat dir (`<slug>.decommissioned.<date>`) keeps its `customer.yaml`. The CI publish and reconcile scripts, and `tests/shipped-customer-configs.test.ts`, skip that shape explicitly so a decommission merge never republishes the retired seat to R2 or projects it into D1.

### Dry-run vs live output

Both modes emit one line per step. The status column distinguishes them:

```
[ planned] 03_r2_namespace: {"deleter_wired":false,"namespace":"smd/"}
[executed] 03_r2_namespace: {"objects_deleted":12}
[ skipped] 03_r2_namespace: {"reason":"namespace_already_empty","skipped":true}
```

This keeps dry-run vs live diffs cheap — Captain can compare side-by-side before authorizing the live run.

### `smd-cli` integration

When the Captain CLI lands at `bin/smd-cli` (or `operator/bin/smd-cli`), register a `decommission` subcommand that delegates to this script:

```
smd-cli decommission <slug>     # equivalent to: bin/decommission-customer.sh <slug> --live
```

The CLI passes the operator's identity through `--actor`; the script defaults to `$DECOMMISSION_ACTOR` or `captain` so manual invocations still produce attributable audit rows.

### Idempotency contract

`P0 invariant.` Any sequence of `plan` + `run` invocations on the same slug must converge on a fully decommissioned state without raising. The test suite asserts:

1. `plan` x2 (no side effects).
2. `run` (full execution).
3. `run` again (every step reports `skipped` or executes a benign no-op).

Re-running after a mid-sequence failure (`exit 3`) is the supported recovery path; Captain does not have to clean up partial state by hand.

### Failure semantics

Live mode halts on the first step that raises. The failed step's audit row is written before the exception propagates so the trail names the failure. Re-running picks up where it left off because every step is idempotent. There is no rollback path: decommission is one-directional. If a substrate-deletion step fails partway through (e.g., R2 namespace delete throttled mid-batch), the live R2 deleter implementation must accept partial state and only delete what is still present on retry.

## Test plan

`operator/bin/tests/test_decommission.py` runs against the `smd` synthetic fixture (copied into a tmp path per test):

| Test                                                  | Asserts                                                                                                                                                                           |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_dry_run_returns_planned_steps_and_does_nothing` | Every step returns `PLANNED`; no audit rows written; live dir untouched.                                                                                                          |
| `test_live_runs_full_sequence_and_writes_audit_trail` | Full sequence executes; customer dir tombstoned; compliance manifest written; audit log contains `DECOMMISSION_INITIATED` + `DECOMMISSION_DRAIN_COMPLETE` + `DECOMMISSION_FINAL`. |
| `test_idempotent_repeated_runs`                       | `plan` x2 + `run` + `run` all succeed; second `run` reports `SKIPPED` for tombstone, R2, Vectorize.                                                                               |
| `test_failure_halts_with_step_failed`                 | A runner that raises mid-sequence halts with `DecommissionStepFailed`; audit log records the failure; resume path completes.                                                      |
| `test_tombstone_skips_when_no_customer_dir`           | Tombstoning a non-existent slug returns `skipped: true, reason: no_customer_dir`.                                                                                                 |
| `test_tombstone_idempotent_when_already_tombstoned`   | Second tombstone call returns `skipped: true, reason: already_tombstoned`.                                                                                                        |
| `test_noop_stubs_return_skipped_manifests`            | The AgentMail and Fly NoOp stubs return `skipped: true, reason: external_client_not_wired`.                                                                                       |
| `test_compliance_archiver_writes_manifest`            | The in-process archiver writes a manifest JSON to the archive dir.                                                                                                                |
| `test_cli_live_requires_confirm_slug`                 | `--live` without a matching `--confirm-slug` exits 2 and touches nothing.                                                                                                         |
| `test_cli_allow_unwired_refused_without_fixture_root` | `--allow-unwired` without `--customers-root`, or with the repo's real one, exits 5 and touches nothing.                                                                           |

`operator/bin/tests/test_decommission_backends.py` covers the observability probe (`test_observability_reports_observed_row_counts`, `test_observability_reports_a_delete_that_matched_nothing`, `test_observability_tolerates_a_d1_without_machine_credentials`), the Fly arming rule (`test_backends_from_env_does_not_arm_fly_from_a_logged_in_cli`) and the transport's `https://`-only rule (`test_http_request_refuses_non_https`).

Run with:

```
cd operator && uv run --quiet --with pytest --with pyyaml python3 -m pytest bin/tests/test_decommission.py -v
```

## Open work

- ~~Wire `AgentMailProvisioner`~~ and ~~wire `FlyMachineManager`~~: done 2026-09-10 (`decommission_backends.py`), along with R2, Vectorize and observability.
- Replace `InMemoryComplianceArchiver` with the `compliance-audit-export` skill output (#802).
- Land `bin/smd-cli` `decommission` subcommand and remove the standalone-invocation note in this spec.
