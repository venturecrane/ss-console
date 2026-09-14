# Durable Task-Execution Substrate (B1) — Design

**Decision record:** [ADR 0051](../../adr/0051-operator-durable-task-execution-substrate.md). This document is its backing detail — the spec the implementation follows. Status: accepted (2026-06-18), hardened against three critique rounds + a source-level Phase-0 verification.

## Context

The SMD Operator is sold as a coordinator **hire** (ADR 0037): it must take a job, run it **unattended** to completion, survive crashes/restarts, and deliver a **retrievable result**. Today a long task runs in one synchronous turn against a hard **55s reply budget** (`hermes-smd-overlay/webhook_gate.py:264`), times out, and restarts from zero — the receipts job burned ~$50 and delivered nothing.

This is **B1** from ADR 0050's backlog. The receipts task itself is **B2** (process-in-code), separate; the runner is for genuinely long **Class-D / multi-step** work.

## Verified facts (source-level)

- **Resume is lossless via the session _lineage_, not a pinned id.** `state.db` `messages` is append-only (`hermes-agent/hermes_state.py:1480`); on compaction Hermes **forks a new session_id** (`parent_session_id` set) and never deletes rows (`run_agent.py:10732-10761`); reload via `get_messages_as_conversation(tip, include_ancestors=True)` (`hermes_state.py:1686`).
- **V1 — `run_job` is a _fire-fresh_ executor.** `cron/scheduler.py:1024` is standalone (no gateway dep), loads `config.yaml`, resolves model (`:1298-1314`), constructs its own `AIAgent`, and mints a fresh `cron_<job_id>_<ts>` session (`:1205`) with no `conversation_history` — so it cannot resume. The taint/entitlement/outbound guardrails are **process-global plugin hooks**, inherited by any agent in a plugin-loaded process.
- **V2 — real provider-reported usage + cost exist.** `agent/usage_pricing.py` (`normalize_usage`, `estimate_usage_cost`, imported `run_agent.py:164`); the agent accumulates `session_input/output/cache_tokens` (`run_agent.py:2428,13500-13522`). Pre-flight via `estimate_request_tokens_rough()`.
- **V3 — delivery is standalone-capable.** `_deliver_result(job, content, adapters=None, loop=None)` (`cron/scheduler.py:489`) falls back to a standalone send when no live adapter is passed; managed mail (Gmail) routes through the broker (DWD).
- **V4 — a long in-gateway job does not block the event loop _iff it is a thread_.** `run_conversation` is synchronous (`run_agent.py:12094`); the gateway runs cron on a separate `threading.Thread` (`gateway/run.py:16572,16991`) whose `tick()` dispatches via a `ThreadPoolExecutor` under a file lock (`cron/scheduler.py:1669-1743`), off the asyncio loop. Agent work is I/O-bound (GIL released on LLM/tool waits) — which is why cron already coexists with live traffic.
- **B0 taint hole:** the gate keys on `session_id` (`enforce.py:877`) but `SESSION_TAINT.mark()` runs only at the two inbound chokepoints (`plugins/hermes-smd-inbound/__init__.py:83,217`). A worker session is a new unmarked origin.
- **B3 cost breaker built-but-unwired:** `operator/safety_substrate/sticky_stop.py` (`record_cost_cents:608`, `record_runtime_seconds:565`, `assert_allowed:663`) has no live turn-path caller.
- **Blueprint:** the broker-owned audit ledger (uid-gated socket, bind-mounted past `chmod 0700`) + the `entrypoint.sh` supervisor pattern.

## Architecture

A long job = a **lean control row in the broker-owned ledger** + a **Hermes run whose conversation lives in `state.db`**, driven by an **in-gateway background thread** (the cron model, off the event loop).

### Decisions

1. **Resume by lineage, with a recorded tip.** The ledger stores `root_session_id` and a transactionally-updated `current_tip_session_id`. Resume prefers the recorded tip (the lineage may be a _tree_; root-walk alone is ambiguous). Before handing reloaded history to the LLM, repair a trailing `tool_call` with no matching `tool_result` by injecting a synthetic "interrupted — not executed" result so the model re-plans rather than the runtime re-executing or erroring.
2. **In-gateway background _thread_ (V1 + V4).** Mirror the cron-ticker + `ThreadPoolExecutor` model. **Reuse** the `config→AIAgent` construction `run_job` uses (`cron/scheduler.py:1298-1342`); **net-new** is the _invocation_ — `run_conversation(conversation_history=…)`, tip selection, partial-pair repair, cross-segment usage accumulation. Guardrails are inherited from the process-global plugins. Conformance asserts concrete invariants on the net-new surface: after a resume the agent's `session_*_tokens` reflect the reloaded history; taint state is fail-closed-present; a repaired trailing `tool_call` yields exactly one synthetic result. **Defer the dedicated `smd-jobs` uid to the send-capable milestone.**
3. **Restart-survival via ledger + boot-sweep behind a readiness barrier.** The worker thread is disposable. On boot, the sweep re-claims/resumes non-terminal jobs only after the broker socket pings, the expected plugin tool set is registered, and (if `deliver_to` needs it) the live adapter is connected. Fold the job ledger into the existing broker DB/socket (reuse the audit DB's mount, uid, `SO_PEERCRED` gating).
4. **Lean control row.** `jobs(id, customer_slug, persona_id, model, brief_digest, status, root_session_id, current_tip_session_id, lease_owner, lease_epoch, attempts, budget_cents, spent_cents, deliver_to, result_ref, error)` + `idempotency_keys(job_id, step_key, state)`. `state.db` owns conversation recovery; the ledger holds only control facts it can't.
5. **Lease fencing token.** Each claim mints a monotonic `lease_epoch`; every privileged broker write carries it; the broker rejects stale-epoch writes.
6. **Cost: wire `sticky_stop`, enforce pre-spend from real usage.** Add the per-job `budget_cents` `sticky_stop` lacks. Pre-flight skip via `estimate_request_tokens_rough()`. The authoritative guard is real accumulated tokens at the **per-tool-iteration boundary**: after each tool result is appended, recompute projected next-request input cost and hard-stop if `spent + projected_next_input > budget`. Record real usage (`session_*_tokens` + `estimate_usage_cost`) into `spent_cents` after each segment. Count metered reads against the budget.
7. **Safety.** (a) Taint at construction, fail-closed. (b) `deliver_to` validated against a `customer.yaml` channel allowlist, broker-enforced. (c) Idempotency key recorded **before** the effect (claim-then-act), keyed on the logical effect (action + target + stable content id); on broker-socket failure → bounded backoff, never proceed past an un-journaled effect, then park to `needs_review`. The broker respawn-supervisor is a hard prerequisite.
8. **Delivery is a first-class, retried state:** `complete → delivering → delivered` (idempotent, epoch-fenced, own dead-letter). The result artifact persists to per-customer R2 before `complete → delivering`. Leverage `_deliver_result` for gateway channels; managed mail via the broker.
9. **Worker identity from the ledger row** (`customer_slug`, `persona_id`, `model`) captured at intake, loaded from the row, never defaulted; boot assertion on mismatch → `needs_review`.
10. **Kill switch in MVP:** `job_cancel(ticket)` sets a `cancel_requested` flag checked at the per-tool-iteration boundary, then dead-letters as `cancelled`.
11. **In-turn work obeys its execution class** (process-in-code); the runner provides duration + delivery only.

### Lifecycle

intake (`start_background_job` → control row w/ identity, ticket <55s) → claim (`lease_epoch++`, `attempts++`) → resume? (prefer recorded tip, repair trailing tool_call) → per segment: pre-spend + cancel check → run (Hermes agent path) → record real usage + advance tip transactionally → complete → result to R2 → **delivering → delivered** (retried, epoch-fenced) → done. Any breach/cancel/broker-stall → dead-letter (`needs_review` / `cancelled`) → notify.

## MVP (read-mostly) and deferred

**MVP:** folded broker ledger + verbs · in-gateway worker thread (no new uid) · `hermes-smd-jobs` plugin (`start_background_job`, `job_status`, `job_cancel`) · pre-spend cost via wired `sticky_stop` + `budget_cents` · taint-at-construction (B0) · `deliver_to` allowlist · delivery state machine · R2 result store · `jobs` runtime-read observability (`GET /runtime/jobs`) + `job_status`/`job_cancel` MCP verbs. Proof: a long **Class-D** job (multi-document review), **not receipts**.

**Deferred (sequenced):** send-capable jobs (full idempotency enforcement + the `smd-jobs` uid/isolation) · concurrency >1 · progress streaming · console D1 mirror · TTL/archival.

## File map

**Overlay (`hermes-smd-overlay`):** `plugins/hermes-smd-jobs/__init__.py` (new); `shared/job_ledger_client.py` (new — `BrokerJobClient`, twin of the audit client); `webhook_gate.py` (read-only `job_status` + `job_cancel` verbs); the in-gateway worker module + boot-sweep (new).

**Console (`ss-console`):** `operator/workspace_broker/job_ledger.py` (new) + verbs in `server.py` (epoch-fenced, on the existing audit DB); `operator/templates/entrypoint.sh` (broker respawn-supervisor; worker in-gateway, no new uid for MVP); `operator/migrations/00xx_operator_jobs.sql` (new); `operator/contracts/customer-yaml-blocks.yaml` (declare new blocks).

**Reuse, do not reinvent:** audit-ledger + `SO_PEERCRED` (`operator/workspace_broker/`); supervisor/bind-mount (`operator/templates/entrypoint.sh`); taint register (`hermes-smd-overlay/shared/inbound.py:311,334`); cost breaker (`operator/safety_substrate/sticky_stop.py`); resume (`hermes_state.py:1686`). **Leverage:** Hermes' in-process `AIAgent`/`run_conversation` construction (the one `run_job` uses); `agent/usage_pricing.py`; `_deliver_result` (standalone fallback). **Do not:** fork the agent path into a separate replicated process; use the Hermes Runs API (in-memory); build an external durable-execution engine.

## Verification

- **Deterministic crash test (CI):** `os._exit(1)` after committing step N's effect but before recording completion; restart in-test; assert N is not re-executed (idempotency hit) and N+1 proceeds. Variant: kill between a `tool_call` and its `tool_result`; assert the synthetic-interrupt repair prevents double-execution.
- **Fencing test (CI):** two claimants; the stale-epoch one's writes are rejected; no double-delivery/double-spend.
- **Cost test (CI):** a segment that would exceed `budget_cents` is refused before the call and dead-letters; a tool returning an oversized payload mid-segment hard-stops at the next iteration boundary; provider-reported usage matches `spent_cents`.
- **Readiness-barrier test (CI):** worker started with the broker socket not-yet-listening claims nothing until broker + plugins + adapter are ready.
- **Identity test (CI):** worker loads `model`/`persona` from the row; a mismatch parks to `needs_review`.
- **Staging acceptance (one-time, manual):** `OVERLAY_REF` bump + `reprovision.sh <staging-slug>` (explicit Captain authorization) — boot-smoke; `start_background_job` returns a ticket <55s; a real Fly machine-restart mid-run resumes with no duplicated work; budget breach + `job_cancel` dead-letter; an injection fixture cannot drive an autonomous send; an off-allowlist `deliver_to` is refused; result lands on the authored surface; `job_status` returns `done` + `result_ref`.
- **CI suite:** `cd operator && python3 -m pytest bin/tests safety_substrate/tests adapter/tests -q`; overlay tests; the `run_job` construction-equivalence smoke.

**Acceptance:** MVP completes a long Class-D proof job, survives a deterministic mid-step crash (CI) and a real Fly restart (staging) with no duplicated work, enforces a pre-spend cost ceiling, honors a manual cancel, and resists the injection/exfiltration fixtures.
