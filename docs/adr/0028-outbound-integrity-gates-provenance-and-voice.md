---
title: Outbound Integrity Gates — Provenance and Voice Fidelity Run on Live Output, Not Just at Boot
date: 2026-05-29
status: accepted
captain: Scott Durgan
related-adr: 0005-external-send-identity.md, 0025-autonomy-ceilings-configurable-exposure-vs-initiation.md
related-issue: https://github.com/venturecrane/ss-console/issues/855
related-note: note_01KSS3TCTKWYVF6EZ04482X389, note_01KSTYSNC9CYPKYFJZ3TJ7F6RM
---

# ADR 0028 — Outbound Integrity Gates: Provenance and Voice Fidelity

**Status:** Accepted (Captain decision, 2026-05-29). Two outbound gates the 2026-05-29 audit found built but **disconnected from live output**.

**Source:** The harness's outbound membrane has two integrity properties beyond the autonomy ceiling: the output must not **fabricate** (provenance / no-fabrication — a P0 venture rule), and it must sound like the **principal's voice**, not generic model prose. The 2026-05-29 build audit (`note_01KSTYSNC9CYPKYFJZ3TJ7F6RM`) found both _implemented_ and both _unwired_: the citation filter runs only in a boot test, and voice transformation/gating runs only against synthetic fixtures. A built control that never executes on real output is, for assurance purposes, absent. This ADR makes "runs on live output" the doctrine.

---

## Context

Two functions, one shared defect.

**Provenance / no-fabrication.** The venture's hardest rule is that no client-facing content may be fabricated (CLAUDE.md "No fabricated client-facing content," a P0 with merge-gate enforcement on committed copy). For agent-_generated_ output the corresponding runtime control is `operator/safety_substrate/citation_filter.py` — it refuses output containing citation-shaped strings that aren't backed by a real source. It is real code. But its only references are itself and `invariants/invariant_6.py` (a boot-time invariant test) — verified: **no live-output call site exists.** So today an agent could emit a fabricated citation into a real deliverable and the filter would never see it; the only thing exercising the filter is a self-test at startup.

**Voice fidelity.** The product promise (thesis note) includes an employee that writes in _the principal's_ voice. The overlay injects voice _samples_ into the prompt live (`pre_llm_call`, per the audit), which primes the model — but the deterministic output _transform_ (`operator/adapter/voice/transform.py`, a real 1,200-line module) is not wired onto live deliverables, and the quality _gate_ (`operator/voice-gate/cli.ts`) runs synthetic-only: live mode is explicitly "not yet implemented" (`cli.ts:~174`), gated behind per-customer Hermes D1 binding (#800) and a sample-ingestion store. Voice is _primed_ but not _gated_. Issue #855 (Voice Layer 2 — sample-driven draft transformation) is the open P0 for this.

Both pass the harness membership test — provenance integrity and voice fidelity are promised regardless of which engine writes the words, so both are harness functions. And both fail the same way: the capability is built, but it does not sit on the live action path. The audit named the outbound enforcement gap the **sharpest next target**, precisely because the highest-leverage, lowest-risk fix is connecting a control that already exists.

This matters more under ADR 0025: once a customer can configure autonomous `EXTERNAL_SEND`, there is no human reviewer to catch a fabricated citation or an off-voice message before it ships. The draft-for-review posture (ADR 0035) provided an incidental human integrity check; autonomous configurations remove it, so the gates must run in code.

## Decision

**Provenance and voice fidelity are gates on the outbound action path. They execute on every live customer-facing deliverable, not only in boot tests or synthetic runs. A deliverable that fails the provenance gate is blocked; a deliverable that fails the voice gate is blocked or down-ranked per its configured policy. Both outcomes are audited.**

Specifically:

### 1. Provenance gate runs on live output

`citation_filter` (and any successor provenance check) executes on agent-generated content before it becomes a deliverable — drafted _or_ sent. A fabricated/unbacked citation is refused on the live path, with the refusal audited, exactly as the trust-ceiling refusals are. The boot-time invariant test stays as a regression check; it is not the enforcement.

### 2. Voice transform + gate run on live output

The voice transform applies to live deliverables (not just fixtures), and the voice gate evaluates real output against the principal's ingested samples. Live mode (#800/#855 dependencies) is the target state; until those land, the honest status is "primed, not gated," and **a customer must not be configured for autonomous external send while voice is un-gated** (binds to ADR 0025 sequencing).

> **Realized (2026-07-13, overlay#153, OVERLAY_REF 8806099).** The runtime voice gate now runs in the trust plugin's outbound path (same `pre_tool_call` seam as the provenance gate and the ADR 0031 content floor). Binding: seats whose live config authors a `voice_library` block. Firing: allowed **outside** `EXTERNAL_SEND` resolved `autonomous` (post-ADR 0072 scoping). Pass requires samples retrievable + the transform demonstrably applied on this turn (per-turn `shared/voice_status.py` marker); no-samples / transform-skipped / gate-error all downgrade to draft with a `VOICE_GATE_TRIGGERED` audit row (§4 fail-closed). The sequencing rule above is thereby code-enforced for voice-authored seats: an autonomous send cannot bypass an unproven voice pipeline. Residual for full #855 closure: the offline blind-test quality gate (#823 harness live mode) and per-seat anchor-pack ingestion.

### 3. The gates are engine-independent and sit in the Gates stratum

Provenance and voice enforcement are harness controls, invoked by the overlay on documented firing sites, not Hermes features we hope are on. If the engine changes, the gates still run.

### 4. Fail-closed, consistent with the substrate

Per the venture's fail-closed posture (`project_operator_fail_open_antipattern` memory), a gate that cannot run (sample store unavailable, filter errors) blocks or drafts the output — it does not silently pass it. A gate that defaults open is the fail-open anti-pattern this venture has been burned by repeatedly.

### 5. Voice fidelity ≠ fabrication license

The voice transform rewrites tone and phrasing; it may never introduce facts, claims, or commitments not present in the source (the transform module already asserts "never adds a sentence," `transform.py:60`). Voice and provenance are orthogonal gates and both must pass.

## Alternatives Considered

### A. Leave provenance as a boot test; rely on the draft-for-review to catch fabrication

**Rejected.** ADR 0025 makes draft-for-review optional; the human catch disappears for autonomous configs. And even with a reviewer, a plausible fabricated citation is exactly what a busy reviewer rubber-stamps. The control exists — run it.

### B. Keep voice as prompt-priming only (no output gate)

**Rejected.** Priming raises the average but provides no floor; the one off-voice message that ships to a client is the reputational damage. A gate provides the floor. (This is the #855 workstream, not new scope invented here.)

### C. One combined "output quality" gate

**Rejected.** Provenance (a correctness/integrity hard-block) and voice (a fidelity property with a configurable threshold) have different failure semantics and different blocking policies. Conflating them produces a gate that is either too soft on fabrication or too hard on voice. Two gates, both on the path.

## Consequences

**Positive.**

- The audit's sharpest target closes: built controls start protecting real output. Highest leverage, lowest risk.
- Autonomous-send configurations (ADR 0025) become safe to offer, because integrity no longer depends on a human reviewer being in the loop.
- The P0 fabrication rule gains a runtime enforcement point for agent-generated content, complementing the committed-copy merge gates.

**Negative / accepted.**

- Live voice gating depends on #800 (per-customer Hermes D1 binding) and the sample-ingestion store (#855) — genuine external dependencies. Until they land, voice status is "primed, not gated," and the ADR 0025 sequencing constraint (no autonomous external send while un-gated) holds. This is a real phase boundary, not deferral by preference.
- Running gates on the live path adds latency to deliverable production; acceptable, and the fail-closed default means latency never trades against integrity.

## Verification

1. `citation_filter` has a live-output call site on the deliverable path; a fixture with a fabricated citation is blocked at runtime (not only by the boot invariant).
2. A provenance refusal is written to the audit log.
3. The voice transform runs on a live deliverable; the voice gate evaluates against the principal's real samples (live mode), with synthetic mode retained for harness self-test.
4. A gate that cannot run blocks/drafts the output; no path lets an un-gated deliverable ship when a gate is configured.
5. No customer is configured for autonomous `EXTERNAL_SEND` while the voice gate is un-gated for them.

## References

- [ADR 0005 — External-send identity](./0005-external-send-identity.md) (the incidental human integrity check that autonomous configs remove)
- [ADR 0025 — Autonomy ceilings are configurable](./0025-autonomy-ceilings-configurable-exposure-vs-initiation.md) (why the gates must run in code once a reviewer is optional)
- CLAUDE.md "No fabricated client-facing content" (the P0 provenance rule these gates enforce at runtime)
- `operator/safety_substrate/citation_filter.py` + `invariants/invariant_6.py` (built provenance filter; boot-test-only today)
- `operator/adapter/voice/transform.py`, `operator/voice-gate/cli.ts` (built voice transform + synthetic-only gate)
- [Issue #855](https://github.com/venturecrane/ss-console/issues/855) (Voice Layer 2 — sample-driven draft transformation), Issue #800 (per-customer Hermes D1 binding)
- `project_operator_fail_open_antipattern` (fail-closed posture)
- Strategy notes: `note_01KSS3TCTKWYVF6EZ04482X389`, `note_01KSTYSNC9CYPKYFJZ3TJ7F6RM`
