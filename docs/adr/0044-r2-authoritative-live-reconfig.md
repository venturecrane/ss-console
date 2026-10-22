---
title: R2-Authoritative Live Reconfiguration — Broker-Owned Apply
date: 2026-06-14
status: partially-reverted
amended-date: 2026-10-22
captain: Scott Durgan
related-adr: docs/adr/0012-customer-yaml-storage.md, docs/adr/0026-config-surface-is-a-security-boundary.md, docs/adr/0043-operator-runtime-read-path.md, docs/adr/0007-per-customer-machine-isolation.md, docs/adr/0094-customer-configuration-as-built.md
amends: docs/adr/0012-customer-yaml-storage.md
enforced_by:
  - tests/customer-yaml-parity-contract.test.ts
  - tests/config-publish-guards.test.ts
  - tests/r2-config-reconcile.test.ts
  - scripts/ci-publish-customer-configs.sh
  - scripts/ci-reconcile-r2-customer-configs.sh
  - .github/workflows/customer-config-reconcile.yml
  - operator/templates/entrypoint.sh
  - operator/templates/r2-account-key-strip-probe.py
  - operator/bin/boot-smoke-test.sh
---

# ADR 0044 — R2-Authoritative Live Reconfiguration

> **AMENDED 2026-10-22: partially reverted, not reverted in full.** The
> 2026-07-14 banner below says nothing in this ADR is wired. That was wrong
> when it was written: the root config applier (Decision 5) had been running on
> every seat since 2026-06-15 and still does. What the 2026-07-14 rip removed
> was Decisions 1, 2 and 4, the parts that would have let R2 diverge from git.
> Per-decision status, each with its evidence, is in
> [Status by decision (2026-10-22)](#status-by-decision-2026-10-22) at the end
> of this record; the config model as a whole is
> [ADR 0094](./0094-customer-configuration-as-built.md), which also supersedes
> ADR 0012. The 2026-07-14 text is kept as history.

> **REVERTED 2026-07-14 (Captain decision).** The realization (#1840) — the
> `config_divergence.py` guard, the `SS_CONFIG_SOURCE=r2` / `SS_CONFIG_FORCE_GIT`
> provisioning modes, `reconcile-r2-config.sh`, and the daily
> `r2-config-reconcile.yml` workflow — has been removed. It was scaffolding for
> a live-apply-to-R2 feature (Decision 5, the root config applier) that was
> **never built**: the portal write-back spine is unbuilt and there is no admin
> live-apply endpoint, so R2 never diverges from git and the reconciler guarded
> a window that cannot open. The daily workflow failed loud every morning on a
> scoped read credential that was never minted. Per venture ethos (build only
> what we use), it was ripped root-and-all. In practice **git is the single
> source of truth for `customer.yaml`** — [ADR 0012](./0012-customer-yaml-storage.md)
> §1's git-first invariant that this ADR amended is back in force. The design
> below stands as the plan for **if/when** live reconfiguration is actually
> needed; nothing in it is wired today. Do not treat any part of it as running
> code without rebuilding and re-deciding at that time.

**Status:** Reverted (Captain decision, 2026-07-14). Originally accepted 2026-06-14 to enable applying a `customer.yaml` change to a running Operator without a reboot, durably and reversibly, in service of pilot-time responsiveness — but the apply path was never built and the realized guard/reconciler were removed as unused. Design retained for future reference only.

## Context

A running Operator enforces entitlement ceilings by reading `/opt/data/customer.yaml` **fresh on every action** (`shared/customer_config.py` `from_volume()` is uncached; `plugins/hermes-smd-trust/enforce.py` re-resolves per `pre_tool_call`). So a ceiling/scope change applies the instant that file is replaced — no reboot. Today the only thing that delivers a changed file to a running Machine is a reboot (bootstrap re-fetches from R2 on boot) or a full reprovision. We have been rebooting Machines to deliver a file the agent would re-read on its own.

[ADR 0012](./0012-customer-yaml-storage.md) §1 declares **git the source of truth**, with R2 and the volume as projections, and bootstrap fetching customer.yaml from R2 at boot. Three facts make a naive "just write the live volume" approach unsafe:

1. **Silent revert.** `reprovision.sh` reads `operator/customers/<slug>/customer.yaml` from git. Any live change not also in git is silently reverted on the next reprovision.
2. **Self-loopback ceiling-raise.** A push write-endpoint on the Machine authenticated by a key already present in the agent's process environment would let a prompt-injected agent rewrite its own ceiling file via a `127.0.0.1` self-call — defeating [ADR 0026](./0026-control-plane-data-plane.md) (the agent cannot raise its own ceiling).
3. **On-box privilege reality (confirmed live on `hermes-smd-staging`, 2026-06-15).** The boot script (`operator/templates/entrypoint.sh`) establishes three principals: **root** (the lifetime respawn supervisor), **`workspace-broker`** (the audit broker), and **`hermes`** (the agent/gateway). `/opt/data/customer.yaml` is `hermes:hermes` — only root or the agent can write it; the broker uid **cannot**. The broker also cannot signal the gateway (cross-uid). These two facts force the applier to be **root-owned**, not broker-owned.

   On the R2 write credential: the **gateway process itself does not hold it** — `bootstrap.sh:745` strips the account-wide `R2_ACCESS_KEY_ID/SECRET` before `exec`-ing the gateway (OP-P0-2), leaving the gateway only `R2_BUCKET_CONFIG`/`R2_ENDPOINT_URL` (a bucket name + URL, not credentials) and the bucket-scoped skill-bodies pair. **But** the account-wide key survives in sibling `hermes`-owned processes forked _before_ the strip (the webhook gate; verified `hermes` can read `R2_ACCESS_KEY_ID` from a sibling's `/proc/<pid>/environ`). So a `hermes`-uid agent **with code execution** could exfiltrate the account-wide key from a sibling and write `vaults/<slug>/customer.yaml` in R2 itself — re-opening the self-loopback ceiling-raise. This is gated by `execute_code` being authored (CODE_EXECUTION class, fail-closed by default), so it is not a default-open hole, but it must be closed before the R2-authoritative apply ships on any `execute_code`-enabled customer (Decision 8).

## Decision

### 1. R2 is the operational source of truth for live state; git is the reviewed/DR record

A live apply writes the new `customer.yaml` to R2 (`vaults/<slug>/customer.yaml`). R2 is what a running Machine boots from and what a live apply updates. Git becomes the **reviewed history + disaster-recovery copy**, reconciled from R2 asynchronously. This **amends [ADR 0012] §1**: git is no longer the single live source of truth; it is the reviewed record. Transient git-behind-R2 divergence is accepted and surfaced in the config-history trail.

### 2. `reprovision.sh` reads from R2, not git

To eliminate the silent-revert (Context fact 1), `operator/bin/reprovision.sh` reads the customer.yaml it materializes from **R2**, not the git working copy. Git is reconciled into R2 (not the reverse) by the reconciler below.

### 3. Every apply writes a durable byte-snapshot to R2

Each apply writes `customers/<slug>/history/<digest>.yaml` and records the key in `customer_config_history.r2_shadow_key` (non-null for apply-sourced rows). **Undo restores from this R2 snapshot**, not from a git SHA that may not exist yet. This makes "reversible" real rather than a pointer to an un-made commit.

### 4. A named reconciler keeps git honest

A scheduled job (CI or queue-triggered Worker) commits reconciled R2 versions back to the customer.yaml repo as the reviewed record. The reconciler is owned and runs on a schedule — git divergence is bounded, not "someone remembers to."

### 5. Apply is pull-based and root-owned (not a push write-endpoint, not broker-owned)

The runtime seam ([ADR 0043](./0043-operator-runtime-read-path.md)) stays **read-only**. Config application is performed by a **control-plane applier running as root** — hosted in the existing lifetime respawn supervisor in `entrypoint.sh` (the only principal that can both write the `hermes`-owned `/opt/data/customer.yaml` and signal the gateway; Context fact 3). It runs with **no agent-readable credential**:

- The console writes the validated new `customer.yaml` (+ snapshot) to R2.
- The root applier pulls from R2 with a credential present only in root's env (never in the broker `env -i` allowlist, never in the agent's `hermes` drop), validates in-process (`bootstrap/validate.py`, parity-hardened), runs floor/allow-list/monotonicity checks, applies via atomic temp-write+`fsync`+`rename` to `/opt/data/customer.yaml`, signals the gateway for reload-class fields, and writes the `CONFIG_WRITE` audit row through the broker's append-only ledger — **apply and audit as one unit** (the writer cannot suppress its own log).
- The agent holds no inbound verb that can trigger a config change, and — once Decision 8 lands — no credential that can write the R2 config object either. The self-loopback ceiling-raise primitive (Context fact 2) does not exist.

This preserves [ADR 0026]: control-plane authority (changing ceilings) stays off the data plane (the agent). It is the correctly-scoped revival of the buried `customer-sync` sidecar — a **root-owned** R2 pull-applier, **not** the agent-triggered SIGHUP mechanism (which stays retired). _(Originally specified as broker-owned; recast to root-owned on 2026-06-15 after the on-box privilege confirmation in Context fact 3 — the `workspace-broker` uid can neither write the agent's config file nor signal the gateway.)_

### 6. Graceful reload via SIGUSR1

For changes that require Hermes to reload (voice tone, connector enable — fields read at gateway start), the **root applier** triggers Hermes' **native** graceful restart by sending `SIGUSR1` to the gateway PID (`gateway/run.py` installs `restart_signal_handler` → `request_restart(via_service=True)` → drain → exit-75 → service-manager restart). No Hermes-core change; no data-plane injection. Root can signal the `hermes`-owned gateway; the broker uid cannot (Context fact 3), which is the second reason the applier is root-owned.

### 7. Safety semantics on the apply path

- **Floor-preserving + allow-list.** The root applier refuses any diff that would bypass a vertical/content floor, and refuses changes to fields outside an explicit live-writable allow-list (`vertical`, `model`, persona OAuth, connector backends, memory namespace are never live-writable — they are rebuild-class).
- **Tightening vs widening.** A ceiling _tightening_ (toward `refused`) is confirmed-applied-or-failed-loudly; a _widening_ may defer to next boot. Direction is computed against the order `refused < draft_for_review < autonomous`.
- **Config-epoch.** Each applied config carries a monotonic epoch; a _loosening_ change does not retroactively apply to sessions started before the epoch (it must not bless an in-flight, possibly-tainted turn). The `CONFIG_WRITE` row records the active session set at write time.

### 8. Prerequisite (blocking for `execute_code`-enabled customers): finish the account-wide R2 key strip (OP-P2-1)

With R2 as the operational source of truth (Decision 1), any path by which a compromised agent can write `vaults/<slug>/customer.yaml` in R2 re-opens the self-loopback ceiling-raise (Context fact 2) one layer up. The gateway's own env is already clean (OP-P0-2). The remaining hole (Context fact 3): the account-wide `R2_ACCESS_KEY_ID/SECRET` survives in sibling `hermes` processes forked before the `bootstrap.sh:745` strip (the webhook gate), and a same-uid agent with code execution can read it from `/proc`.

Therefore, **before the R2-authoritative apply path ships on any `execute_code`-enabled customer**, OP-P0-2's strip must be _completed_ so no `hermes`-owned process retains the account-wide key: launch the webhook gate (and any other pre-strip background fork) **after** the strip, or strip the key inside their subshells, so `execute_code` cannot reach it from a sibling's environ. The agent keeps the bucket-scoped skill-bodies credential and reads config via the existing read seam ([ADR 0043](./0043-operator-runtime-read-path.md), `OPERATOR_RUNTIME_READ_KEY`); the applier's R2 pull credential lives in root's env only. This is the OP-P2-1 scoped-R2 workstream — narrower than first scoped (a fork-ordering fix, not a credential overhaul), and surfaced here as a real dependency. (Independently, this sibling-environ leak is a cross-tenant exfil risk worth closing on its own merits, regardless of the apply path.)

## Consequences

- Live entitlement/scope/escalation/webhook/demo changes apply instantly, durably (survive reboot + reprovision), reversibly (R2 snapshot), and audited (the root applier writes through the broker's append-only ledger).
- ADR 0012's git-first invariant is relaxed to git-reviewed; the reconciler and the reprovision-reads-R2 change are load-bearing and ship in the same wave.
- The control/data-plane separation of ADR 0026 is preserved by construction (root applier + no agent-reachable config-bucket write credential), not by added checks — **contingent on Decision 8 (OP-P2-1) for any `execute_code`-enabled customer**. The gateway env is already clean; the apply path must not ship on an `execute_code` customer while the account-wide R2 key is still reachable from a sibling process's environ.

## Realized (2026-07-13, #1840)

Decisions 2 and 4 shipped with one deliberate reshaping, recorded here so the
ADR matches what runs:

- **Decision 2 is realized as a provenance-stamped divergence guard + an
  explicit adopt-R2 mode, not an unconditional read-from-R2.** While the
  portal write-back spine is unbuilt, git PRs are the only reviewed authoring
  path, and the git → R2 projection _is_ how a merged config change deploys —
  an unconditional reads-from-R2 reprovision would make merged changes
  undeployable. Instead: every git projection stamps the uploaded object with
  `projected-sha256` user metadata; `provision-customer.sh` (Step 0.5)
  classifies the current R2 object before overwriting (`absent` /
  `identical` / `clean-projection` → proceed; anything else → **fail closed**
  with the diff that would be lost). A missing or mismatched stamp can never
  allow a clobber — live-apply writes carry no stamp, so they are always
  guarded. `SS_CONFIG_SOURCE=r2` provisions from the live R2 config (the
  Decision 2 read path, on demand), and `SS_CONFIG_FORCE_GIT=1` is the
  explicit revert. Verdict logic: `operator/bin/lib/config_divergence.py`;
  tests: `operator/bin/tests/test_config_divergence.py`. The silent-revert
  primitive in Context fact 1 is closed either way.
- **Decision 4 reconciler:** `operator/bin/reconcile-r2-config.sh` (local via
  Infisical, or CI) compares every provisioned customer's live R2 config
  against git and, in `--pr` mode, opens a `reconcile/r2-<slug>` PR carrying
  the R2 version as the reviewed record. Scheduled daily by
  `.github/workflows/r2-config-reconcile.yml`, which **fails loudly** until
  its scoped read credentials (`R2_ENDPOINT_URL`,
  `R2_RECONCILE_ACCESS_KEY_ID`, `R2_RECONCILE_SECRET_ACCESS_KEY`) are
  provisioned as repo secrets — a silently-skipping reconciler would be
  paper compliance.

## Out of scope

Portal client self-serve apply (admin-driven first); proactive entitlement-promotion nudges.

## Status by decision (2026-10-22)

The 2026-07-14 rip (`eee5504b`, #1898) removed the divergence guard, the
adopt-R2 provisioning modes, `operator/bin/reconcile-r2-config.sh` and the
daily `r2-config-reconcile.yml`. Its rationale was that "the live-apply-to-R2
path (Decision 5) was never built". The half of Decision 5 that was missing is
the console writing R2; the root applier itself shipped on 2026-06-15 (overlay
`b3294de`, launched by `operator/templates/entrypoint.sh` since ss `7b7d9b28`)
and has applied merged config live on every seat since. Line citations are
against ss-console `8cb62e6d` and overlay `3d64eda` (`overlay:` prefix).

| Decision                                                          | Status                                                                                                                                                                                                                                                                                                                                                                                                                                              | Evidence                                                                                                                                               |
| ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1. R2 is the operational source of truth, git the reviewed record | **Reverted.** Git is the only authoring source; every writer of `vaults/<slug>/customer.yaml` copies a git file, and no console path writes it.                                                                                                                                                                                                                                                                                                     | ADR 0094 sections 1 and 3; `scripts/ci-publish-customer-configs.sh:30-37`                                                                              |
| 2. `reprovision.sh` reads from R2                                 | **Reverted** (realized 07-13 as a divergence guard, `66cb3f6c`; ripped 07-14). Provisioning uploads the git file unconditionally.                                                                                                                                                                                                                                                                                                                   | `operator/bin/provision-customer.sh:166-168`, `:225-238`                                                                                               |
| 3. Every apply writes an R2 byte-snapshot for undo                | **Never built.** The history row's `r2_shadow_key` is always written `NULL`; undo is a git revert, published like any merge.                                                                                                                                                                                                                                                                                                                        | `src/lib/portal/customer-config-projection.ts:231-241`                                                                                                 |
| 4. A reconciler commits R2 back to git                            | **Reverted** (ripped 07-14). **Replaced by the opposite direction:** `scripts/ci-reconcile-r2-customer-configs.sh` (ss#2305, `7ac6f0f2`) makes R2 converge on git nightly and opens an issue on drift.                                                                                                                                                                                                                                              | `scripts/ci-reconcile-r2-customer-configs.sh:1-3`, `:27-33`; `.github/workflows/customer-config-reconcile.yml:37`, `:164`                              |
| 5. Pull-based, root-owned applier                                 | **Live.** Forked as uid 0 before the drop to `hermes`; polls the R2 ETag every 15 s; validates with the parity validator; writes atomically to the root-owned `/var/lib/smd-config/customer.yaml` (moved off `/opt/data` by the keystone, `20544569`); best-effort `CONFIG_WRITE` audit row. The input is the git publisher, not a console write.                                                                                                   | `operator/templates/entrypoint.sh:28-29`, `:610-617`, `:677-705`; `overlay:config_applier/__main__.py:62`; `overlay:config_applier/applier.py:463-555` |
| 6. Graceful reload via `SIGUSR1`                                  | **Not built.** v1 sends no signal; reload-class fields are held as rebuild-class and land at the next boot.                                                                                                                                                                                                                                                                                                                                         | `overlay:config_applier/__main__.py:20-24`                                                                                                             |
| 7. Safety semantics                                               | **Partly live.** The live-writable allow-list, the rebuild-class never-list and the vertical floor check are enforced, and one held path holds the whole diff. Not wired: the tightening/widening distinction (`classify_direction` exists but `apply` never calls it, so an allow-listed widening applies immediately) and the config epoch (stamped and persisted, but nothing reads it, so the "no retroactive loosening" rule is not enforced). | `overlay:config_applier/safety.py:94-111`, `:242-291`, `:297-339`; `overlay:config_applier/applier.py:415-460`, `:533`                                 |
| 8. Finish the account-wide R2 key strip (OP-P2-1)                 | **Built.** The strip landed in `7b7d9b28`; boot smoke proves on every provision that no agent-uid process carries the key (root processes, including the applier, keep it by design).                                                                                                                                                                                                                                                               | `operator/bin/boot-smoke-test.sh:690-698`; `operator/templates/r2-account-key-strip-probe.py`                                                          |

The handbook already describes Decision 5 as live
(`docs/handbook/operator-platform.md:91`,
`docs/handbook/changing-a-clients-operator.md:65`,
`docs/handbook/deployment-release.md:309-310`); with this amendment the ADR
agrees with it.
