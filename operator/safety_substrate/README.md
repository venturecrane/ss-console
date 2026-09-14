# Safety substrate

The seven irreducible invariants the Operator enforces in code, not in prompt. This directory holds the fixture tests that prove each invariant holds across the failure modes that historically broke similar agents (context compaction, restart, tool failure, prompt injection, cross-customer leakage).

The substrate runs every container start (via `bootstrap.sh`). On `--strict`, any test failure halts the agent loop — the agent does not run.

## The seven invariants

1. **No destructive action without confirmation.** Delete, archive, drop, irreversible writes. Agent refuses unless the current invocation contains explicit user approval text. "Confirmed by previous session" is invalid; approval must live in the current turn. Reference incident: OpenClaw, Meta AI Safety director's agent deleted operator's inbox after compaction dropped a "don't act" instruction (Feb 2026).
2. **Outbound external send is governed by the authored ceiling, per recipient class.** A send to a rostered internal recipient (`external_send_internal`) and a send to anyone outside (`external_send`) each resolve against their own authored, fail-closed ceiling (ADR 0025 / 0035), narrowed by the vertical floor, the content-sensitivity floor (ADR 0031), and the taint gate. Unauthored is refused — no send, no draft. The recipient axis is enforced in code (`recipient_classifier`); the one source is [`operator/references/send-posture.md`](../references/send-posture.md). (History: this invariant was once a universal "hold every send for review, the agent never sends" rule; reformed by ADR 0025/0035 and the recipient split. That universal framing is retired and CI-guarded against.)
3. **No contract or commitment execution autonomously.** Signing, accepting terms, agreeing to scope, agreeing to dates. A commitment never fires autonomously — a human makes binding commitments (refused, or gated on an explicit current-turn approval).
4. **"Don't act" / "stop" instructions are sticky.** Pinned outside compressible turn history; survive context compaction; honored across the session.
5. **Autonomy is enforced in code, not prompt.** The overlay's `hermes-smd-trust` plugin (runtime — `venturecrane/hermes-smd-overlay`) and `adapter.trust_ceiling.enforce` (substrate test primitive) both resolve the effective ceiling from the persona's per-action-class **exposure** authored in the trusted `customer.yaml` (`personas[].entitlements.exposure`, ADR 0056) — never from model-supplied args or the prompt. The retired per-skill `trust_ceiling` scalar is not read (ADR 0056; no compatibility shim). Refusal fires when prompt drift tries to escalate. The overlay plugin runs in the customer Machine; the substrate primitive backs the in-tree invariant test fixture.
6. **No fabricated citations / source-provenance discipline.** Ships in two complementary layers per [`docs/specs/operator/safety-invariants.md`](../../docs/specs/operator/safety-invariants.md):
   - **Refusal layer** (`citation_filter.py`): refuses any law-vertical skill output that contains a fabricated citation (case names, reporter cites, statute references, court rules). Reference incident: Mata v. Avianca, S.D.N.Y. 2023 — ChatGPT fabricated six federal cases; attorney sanctioned. Exercised by `tests/test_invariant_6_no_citations.py`.
   - **Enforcement layer** (`invariants/invariant_6.py`): every fact a skill renders into a declared fact-bearing field must carry a `Citation` attached to a real source. Exercised by `tests/test_invariant_6.py`.
7. **Cross-Machine query prohibition at boot.** The runtime refuses to start if its storage bindings (D1, R2, Vectorize) name another customer's resource. Per ADR 0007 (per-customer Machine isolation) and ADR 0009 (cross-Machine query prohibition), this is enforced at Machine boot — a misconfigured binding fails the boot, never reaches a tool call. Exercised by `tests/test_invariant_7.py` against `invariants/invariant_7.py`.

**Test files per invariant:** one file per invariant, except invariant #6 which has two (the two complementary layers above). Eight test files total, seven invariants.

## Test layout

Each invariant is exercised against four failure-mode fixtures. Test files at `tests/test_<invariant>_<failure_mode>.py`. Convention:

- A test file exports a `run() -> tuple[bool, str]` callable.
- `bool` is True iff the invariant held under the failure mode.
- `str` is a one-line message describing the verdict.
- The runner (`run_invariants.py`) imports each test, calls `run()`, collects results.

## Running locally

Inside the customer container (during `bootstrap.sh`):

```
python3 /app/safety_substrate/run_invariants.py \
  --customer "${CUSTOMER_SLUG}" \
  --fixtures /app/safety_substrate/tests \
  --strict
```

From the repo root for development:

```
uv run --with pyyaml python3 operator/safety_substrate/run_invariants.py \
  --customer smd \
  --fixtures operator/safety_substrate/tests
```

(Omit `--strict` when developing; add it when committing the substrate as a gate.)

## What "ships" means for a substrate test

- The test simulates the failure mode (e.g., serializes a session, truncates the early turns to mimic compaction, then deserializes and replays an action).
- For invariants #1-#5 it invokes the relevant substrate primitive (`adapter.trust_ceiling.enforce`, the sticky-stop state machine, `citation_filter`, etc.) against a mock skill or fixture.
- It asserts the primitive refuses or routes-to-draft the way the invariant requires.
- Any actual call to a real Composio/MCP/build connector is monkey-patched to record-only — substrate tests never reach a real tool.

The substrate primitives the tests exercise are the same primitives the overlay's `hermes-smd-trust` plugin invokes at runtime. The substrate is the test surface; the overlay is the production surface; both use the same enforcement code paths so a passing test means the runtime invariant is real.

## When to extend

Add new test files when:

- A real incident reveals a failure mode the current invariants don't cover.
- A new action class is introduced and needs ceiling enforcement.
- The overlay's plugin hook surface changes (e.g., a new lifecycle event) and we need to verify the substrate still catches escalations through it.

Every Hermes SHA bump re-runs the full substrate on container start. The build is gated; a failing substrate blocks the container from serving traffic.
