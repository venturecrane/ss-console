# Design Review — ADR 0050 B3: sticky-stop live in the dispatch path (+ live probe tier, inbound-boundary probe, B0 taint)

**Status:** Draft for Captain review. ADR 0050 gates B-series implementation on
"a detailed design review [that] precedes any build" — this is that review for
**B3**. Approving it unblocks the overlay/staging implementation.

**Scope.** Three things the runtime-control conformance ledger
(`operator/contracts/runtime-controls.yaml`, PR #1464) surfaced as not
proven-live, all routed here:

- **B3** — wire the `sticky_stop` circuit breakers into the live dispatch path
  (4 `inert` controls).
- **Component 3** — the live negative-fire probe tier that proves a breaker
  actually halts (turns `inert`→`enforced` and closes the `inbound_trust_boundary`
  `unprobed` gap).
- **B0** — propagate taint into `delegate_task` children (the `taint_gate`
  `unprobed` entry's verified bypass). B0 "fixed first; blocks B2" per ADR 0050;
  designed here, shipped ahead of B3.

**Sources.** `operator/safety_substrate/sticky_stop.py` (the breaker, fully
tested), `operator/migrations/0004_sticky_stop_state.sql` (state table), the
overlay `hooks/smd-overlay-activation/handler.py` (the proven activation
self-check pattern), `overlay-hook-surface.json`, ADR 0050 + the backing
framework doc.

---

## 1. Problem

`sticky_stop.py` is complete and tested but has zero callers in ss-console and
zero references in the overlay dispatch path. The breaker named in PRD §7.5 does
not fire at runtime; ADR 0050 honesty banner #3: _"no verified live cost
circuit-breaker."_ Four arms are dead:

| arm                  | substrate entrypoint       | data it needs per turn             |
| -------------------- | -------------------------- | ---------------------------------- |
| tool-failure streak  | `record_tool_failure()`    | tool-call error signal             |
| refusal cascade      | `record_refusal()`         | trust-ceiling refusal signal       |
| time budget (3600s)  | `record_runtime_seconds()` | per-turn wall-clock                |
| daily cost cap ($50) | `record_cost_cents()`      | per-turn LLM cost                  |
| (guard, all arms)    | `assert_allowed()`         | — (read state, block on HARD_STOP) |

**Three of four arms can be fed from existing hook data directly.** Only the cost
arm has a wrinkle (§3.3). This matters: the time/tool/refusal breakers are easy
wins that should not wait on the cost-data question.

---

## 2. Design — Part A: wire the breaker into dispatch

### 2.1 Where each signal is fed (overlay hooks)

A new plugin `plugins/hermes-smd-sticky-stop/` registers against the hooks
already declared in `overlay-hook-surface.json` (each addition must be reflected
there — the conformance test enforces it):

- **`pre_tool_call`** — the dispatch guard. Call `assert_allowed(customer, persona)`
  **before** the trust-ceiling check returns. This is the same hook the trust
  gate runs in, so the breaker sits on the exact path every tool call already
  traverses.
- **`post_tool_call`** — feed `record_tool_failure()` on a tool error result;
  `record_tool_success()` otherwise (resets the streak).
- **trust refusal point** — when `hermes-smd-trust` returns a block directive,
  feed `record_refusal()`. (Co-located in the trust plugin to avoid a second
  classification of "what counts as a refusal.")
- **per-turn timing** — feed `record_runtime_seconds()` from the turn boundary
  (`pre_llm_call`→`post_llm_call` delta, or the gateway turn clock).
- **`post_llm_call`** — feed `record_cost_cents()` (see §3.3 for the data).

### 2.2 StickyStopError → the hook block contract (the "don't swallow" rule)

`sticky_stop` raises `StickyStopError` on HARD_STOP and the docstring is explicit:
the caller **must propagate, not swallow**. But Hermes' `HookRegistry` swallows
exceptions raised inside hooks (verified in the activation-handler work). So the
integration must map the stop into the hook's first-class **block contract**, not
rely on a raised exception:

- `assert_allowed()` returns the current state.
- **HARD_STOP** → `pre_tool_call` returns `{"action": "block", "message": …}` —
  the tool short-circuits; the agent cannot act until Captain `clear()`.
- **SOFT_STOP** → do not block, but pin `trust_ceiling` to `draft_for_review` for
  the turn (the documented SOFT_STOP semantics).
- **Fail-closed** — if the state read raises, return a block (same posture as the
  trust gate's `except: return block`).

This preserves the "no further autonomous action under HARD_STOP" invariant
through the mechanism the gateway actually honors.

### 2.3 State store

`sticky_stop` persists to D1 (`sticky_stop_state`, migration 0004), one row per
`(customer, persona)`. Per-customer Machine = single-tenant, so the existing
`HttpD1StickyStopStore` (serial requests) is sufficient; no cross-row locking
needed. The store binding rides the existing per-customer D1 config — no new
secret.

---

## 3. Design — Part A cont.: the cost-data wrinkle

### 3.1 The constraint

`post_llm_call` kwargs carry `model` and `conversation_history` but **not** token
counts or cost (verified against the overlay hook surface + the audit emitter).
ADR 0015's pin-only fork forbids changing the upstream hook contract to add them.

### 3.2 Options

- **(a) Overlay-side estimation.** Estimate tokens from `conversation_history` +
  `assistant_response` (tokenizer, or a char/4 heuristic) × a per-model price
  table → cents. Local, no upstream change, fail-safe.
- (b) A Hermes-native usage hook — none exists for overlay use (`post_api_request`
  is marked unavailable).
- (c) Authoritative cost pushed from the console — heavy, and it makes a _safety_
  breaker depend on a network sync that can fail open. Wrong posture.

### 3.3 Recommendation: (a), with the precision caveat stated

A runaway-**cost** circuit breaker needs order-of-magnitude correctness ($50/day),
not billing precision. Estimation is the right tool; billing-grade numbers are
**B6**'s job and must not be conflated (ADR 0050: no client cost number until the
B6 metered rollup). The breaker over-counts slightly by design (safer to trip
early). Record the estimator + price table as the single source the probe asserts
against.

---

## 4. Design — Part B: the live negative-fire probe tier (Component 3)

Generalize the activation handler's two hand-coded self-checks (trust-blocks-
banned-send; audit-writes-row) into **one registry-driven loop** over
`runtime-controls.yaml` `enforced` entries. Per the approved harness plan, the
probe tier is **split by surface** so the destructive force-driven part never
halts a customer boot:

### 4.1 Probe isolation (mandatory — the Phase-0 de-risk)

- **probe-mode**: a flag threaded through dispatch so a side-effectful terminal
  action asserts-and-aborts **before** the irreversible step (no real send/exec).
- **segregated audit**: probe rows go to a `source=boot_probe` tag the customer
  ledger view filters out — the tamper-evident ledger is never polluted.
- **probe-scoped state key**: the breaker probe drives a `(customer, "__probe__")`
  persona key, **never** the production `(customer, persona)` counter.
- **crash-safe cleanup**: write the expected reset first / idempotent, so a crash
  mid-probe self-heals and can never leave a real breaker tripped.

If probe-mode proves infeasible under the pin-only fork, the tier falls back to
**observe-don't-force** (assert the breaker fired on real recent traffic) — this
is the Phase-0 spike and it gates building the rest of Part B.

### 4.2 Tiering by surface

- **Staging Machine = primary enforcement.** Full registry-driven negative-fire
  suite, **fail-closed (`os._exit`)**, on every `OVERLAY_REF` bump + a cron.
  Drives each breaker past HARD_STOP via the probe key and asserts the guard
  blocks. Staging crash-looping costs nothing.
- **Production = post-boot async health check, report/degrade, never boot-halt.**
  Run probes within the first N seconds; on failure mark unhealthy + page
  `team@smd.services` + optionally refuse customer-facing dispatch. A flaky probe
  pages a human; it does not dark a customer.
- **`os._exit` boot-gate stays limited** to the two already-proven deterministic
  checks. A breaker probe earns `prod-boot` (and its `os._exit`) only after a
  staging flakiness bar (N consecutive green staging boots), recorded in the
  registry's `probe_surface` field.

### 4.3 Close the `inbound_trust_boundary` gap

Add a negative-fire probe that feeds a synthetic untrusted webhook payload
through `pre_gateway_dispatch` and asserts the boundary withholds — flipping that
entry `unprobed`→`enforced`. Same isolation rules.

---

## 5. Design — Part C: B0 — taint into `delegate_task`

ADR 0050 B0: the taint gate keys on `session_id`; a `delegate_task` child runs
under a fresh, never-tainted session → it can read untrusted content and then
autonomously send/destroy/exec. **Fix first; blocks B2.**

**Design.** Propagate `SessionTaint` from parent to child at delegation
assembly: when the parent session is tainted, stamp the child session as tainted
at `delegate_task` spawn (carry the taint marker in the delegated context the
child's `pre_tool_call` taint gate reads). Add a negative-fire probe: a tainted
parent delegates; assert the child cannot autonomously send. Flips `taint_gate`
`unprobed`→`enforced` once the probe lands.

This is a distinct, smaller change than B3 and ships ahead of it (B2 depends on
it).

---

## 6. Registry transitions (what this produces)

| control                  | before   | after this work                                      |
| ------------------------ | -------- | ---------------------------------------------------- |
| `sticky_stop_*` (×4)     | inert    | enforced (probe_surface: staging → prod-boot on bar) |
| `inbound_trust_boundary` | unprobed | enforced                                             |
| `taint_gate`             | unprobed | enforced (after B0)                                  |

Each flip is one PR adding wiring + probe + the status change; the conformance
test (PR #1464) already enforces that `enforced` ⇒ names a live probe, so the
ledger and reality stay locked.

---

## 7. Verification / DoD

Mirrors the venture standard (verify the running Machine, not the config; OP-P1-4
DoD = negative-fire fails on staging):

1. **Dispatch does not swallow the stop** — unit test: with HARD_STOP state,
   `pre_tool_call` returns a block; the tool does not execute.
2. **Caps actually halt on staging** — drive each breaker past its threshold via
   the probe key; assert the guard blocks. (This _is_ ADR 0050 B3's acceptance.)
3. **Production safety** — the post-boot health check pages + degrades **without
   crash-looping**; a forced mid-probe crash leaves **no tripped production
   breaker and no `boot_probe` rows in the customer ledger view**.
4. **B0** — tainted parent → delegated child cannot autonomously send.
5. **Cost estimator** — probe asserts the estimator trips the cap at the expected
   synthetic spend; precision caveat documented (not a client number — B6).

---

## 8. Rollout

Overlay PR (`plugins/hermes-smd-sticky-stop/` + handler probe loop + taint
propagation) → bump `OVERLAY_REF` in `Dockerfile` + `overlay-pairs.json` (record
new `overlaySha256`) → **reprovision staging first** and run the staging suite →
only then customer-zero. **Reprovisioning a live Machine requires explicit
Captain authorization in the turn it happens** (standing rule).

---

## 9. Open questions for Captain

1. **Cost estimator precision** — accept overlay-side estimation for the breaker
   (§3.3), with billing-grade deferred to B6? (Recommended.)
2. **Does production ever earn `os._exit`?** Default here is no — prod stays
   report/degrade; only staging hard-fails. Confirm.
3. **Sequencing** — B0 ships ahead of B3 (it blocks B2). Land B0 first, then the
   three easy sticky_stop arms (time/tool/refusal), then the cost arm + estimator?
4. **Concurrent session** — the framework doc + ADR 0050 are being authored in a
   parallel session; this review should be reconciled with that work before the
   overlay PR opens.
