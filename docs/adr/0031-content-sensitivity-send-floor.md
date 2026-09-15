---
title: Content-Sensitivity Send Floor — Money / Contract / Scope / Legal Always Drafts
date: 2026-05-31
status: accepted
captain: Scott Durgan
amends: 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md
related-adr: 0005-external-send-identity.md, 0028-outbound-integrity-gates-provenance-and-voice.md
related-interview: operator/customers/smd/onboarding-interview-2026-05-31.md
related-issue: Operator task #21 (overlay PR venturecrane/hermes-smd-overlay#22, tag v0.4.0)
---

# ADR 0031 — Content-Sensitivity Send Floor

**Status:** Accepted (Captain decision, customer-zero onboarding interview, 2026-05-31).

> **Amendment (2026-07-13).** [ADR 0072](./0072-recipient-aware-proactive-send.md) split external send into two classes: `external_send` (outside recipients) and `external_send_internal` (rostered internal staff). Post-0072 this content-sensitivity floor pins the **outside** class (`external_send`) **only** — internal alerts are deliberately **not** content-floored, because an internal alert must carry its money/matter context to be useful. This ADR predates the split and reads as if the floor applies to all external send; the live behavior narrows it to the outside class.

## Context

ADR 0025 made autonomous external send a **configurable per-action ceiling**: a
customer can author `action_ceilings[external_send] = autonomous` and the agent
sends from its own identity without per-message human approval, floored only by a
vertical-pack ceiling (ADR 0022). That decision is by **action class** — it does
not look at _what a specific message says_.

The customer-zero onboarding interview surfaced a requirement ADR 0025 does not
cover. Scott set Crane's default send level to **autonomous**, then immediately
qualified it:

> "Crane send from AgentMail — Autonomous, _except_ the content floor below.
> Even under autonomous send, anything touching **money, contracts, scope, or
> legal commitments** drops to draft-for-review."

This is a **content-derived** floor, orthogonal to the action-class ceiling. An
employee trusted to send routine email autonomously is still not trusted to
autonomously send a wire instruction, a sign-off on a contract, a scope
commitment, or a legal statement. Those must land in a draft a human ships.

### Why this is not already covered

- **ADR 0025 ceiling** decides by action class (`external_send`), not content. An
  autonomous `external_send` ceiling would send a money email.
- **ADR 0028 outbound gate** (`shared.outbound_gate`) scans for _fabrication_ —
  banned marker strings and fabricated legal citations ("did the agent invent
  something it must not say"). The content floor asks a different question: "is
  this the _kind_ of message a human must sign off on before it autonomously
  leaves." A perfectly truthful invoice email passes ADR 0028 and must still be
  caught by this floor.

## Decision

**A content-sensitivity floor sits on top of the ADR 0025 ceiling. When an
`EXTERNAL_SEND` is resolved to autonomous _send_, the message body (subject +
content) is scanned for money / contract / scope / legal content. On a hit, the
send is downgraded to `draft_for_review` — a human reviews and ships it. The
floor can only narrow (send → draft); it never widens.**

1. **Four content classes.** `money`, `contract`, `scope`, `legal` — curated
   keyword/pattern sets (see overlay `shared/content_floor.py`). Broad on purpose.
2. **Fail toward draft.** Draft is recoverable; an autonomous send of a
   commitment is not. An uninspectable body (e.g. `send_draft` by id, a bodyless
   forward) is treated as **sensitive** → draft, never waved through.
3. **Applies only on the allow path.** A ceiling decision of draft/refuse already
   withholds the send; the floor only acts where the ceiling _would_ send.
4. **Enforced in code, at the live seam.** The floor runs in the overlay
   `hermes-smd-trust` `pre_tool_call` hook, after the ceiling resolves an
   autonomous send. It is exception-safe and fails toward draft. (Residual
   invariant from ADR 0025 §5 — enforced in code, not prompt.)
5. **Overlay-only home.** The floor governs _live sends_. The ss-console
   `adapter/trust_ceiling.py` (boot invariant + grading harness) does not send,
   so the floor lives in the overlay runtime (`shared/content_floor.py`), not the
   adapter. This is deliberate, not drift.

## Consequences

**Positive.**

- A customer can run their employee at autonomous send velocity while the
  highest-stakes messages still get a human's eyes — the configuration Scott
  actually wants, and the one most customers will want.
- The floor is a single, testable, data-driven module; adding a category or
  pattern is a one-line edit plus a test row.

**Negative / accepted.**

- **False positives cost a human glance at a draft.** Acceptable by design — the
  alternative (a missed money/legal send) is a venture-risk, not an annoyance.
  The pattern set is tuned over time against real drafts.
- **Not yet a boot invariant.** The floor is enforced in the live fail-closed
  hook and covered by unit + integration tests, but it is not (yet) one of the
  safety_substrate boot invariants that re-run on every Hermes SHA bump. Adding a
  boot invariant for the floor is reasonable future hardening (defense-in-depth
  against a SHA-bump regression); it is tracked, not done here.
- **`send_draft` / bodyless forward are conservatively blocked** under an
  autonomous ceiling because their content is not inspectable at send time. A
  human sends those. If this proves too restrictive in practice, the mitigation
  is to scan at draft-creation time, not to weaken the send-time floor.

## Verification

1. Overlay `shared/content_floor.py::classify` returns `sensitive=True` for each
   of the four classes and `False` for routine prose; `None`/empty → sensitive.
2. The overlay trust hook downgrades an autonomous `agentmail:send_*` with a
   money/contract/scope/legal body to a draft directive, and allows a clean body.
3. Tests: overlay `tests/test_content_floor.py` (unit) and
   `tests/test_trust_enforce.py` (integration via `evaluate_tool_call`).

## References

- [ADR 0025 — Autonomy ceilings are configurable](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) (the action-class ceiling this floors)
- [ADR 0005 — External-send identity](./0005-external-send-identity.md) (the draft posture the floor restores for sensitive content)
- [ADR 0028 — Outbound integrity gates](./0028-outbound-integrity-gates-provenance-and-voice.md) (the orthogonal _fabrication_ gate)
- `operator/customers/smd/onboarding-interview-2026-05-31.md` (the decision source)
- `hermes-smd-overlay`: `shared/content_floor.py`, `plugins/hermes-smd-trust/enforce.py` (the implementation), PR #22 / tag v0.4.0
