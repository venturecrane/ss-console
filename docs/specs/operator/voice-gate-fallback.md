# Voice Quality Gate — Failure Fallback

Status: **Runner removed 2026-11-10** (Captain-approved, code audit of that day). The blind-test harness this spec names as its runner (`operator/voice-gate/`, `bin/run-voice-gate.sh`) was never wired into a workflow, a package script or a seat, and was deleted. The live runtime voice gate (the trust plugin's per-class downgrade, ADR 0083) is a different thing and stays. This file is kept as the record of the design.

**Spec for issue #797.** Three-state fallback (Pass / Near-pass / Fail) extending the §9.6 blind-test gate. Target Customer named this existential: a single "did you write this?" disclosure incident kills the relationship. The fallback path is the operational doctrine for the most likely beta-1 awkward moment.

## Contract

### Three states

The blind-test (§9.6 Gate 3) presents 10 reviewer-written + 10 agent-drafted communications, unlabeled, to 3 judges who know the reviewer well. Each judge labels each as "reviewer" or "agent." Indistinguishability score = % of judgments where the judge was unable to reliably identify the agent-drafted item (i.e., labeled it "reviewer" OR explicitly marked "uncertain — could be either").

```
Pass:       ≥80%   First external draft is unlocked. Audit-log VOICE_GATE_PASSED.
Near-pass:  60-79% Calibration cycle (see below). First external draft remains blocked.
Fail:       <60%   Captain disclosure (see below). First external draft remains blocked.
```

State recorded in `audit_log` per d1-schema.md:

- `VOICE_GATE_PASSED` (metadata: score, judge_ids, sample_set_id)
- `VOICE_GATE_NEAR_PASS` (metadata: score, cycle_count, judge_ids)
- `VOICE_GATE_FAILED` (metadata: score, cycle_count, disclosure_artifact_r2_key)

### Near-pass cycle (60-79%)

1. Captain reviews the misidentified samples with the partner+operator. Identifies which voice rules misfired, which cohort routing was off, which samples need re-sanitization.
2. Customer uploads ≥10 more anchor samples or refines voice rules.
3. After 7 calendar days minimum (allows real Sent-folder data to accumulate if opted-in), re-run blind-test with a fresh sample set.
4. Maximum 2 near-pass cycles. If the third blind-test still scores <80%, transition to Fail state.

Cycles tracked in `voice_samples.notes` and `audit_log` (cycle_count metadata).

### Fail state (<60% after two cycles, OR any single <60% score that Captain elects not to retry)

Captain runs the disclosure protocol:

1. **Disclosure conversation.** Captain calls the partner. Transparent script:

   > "The voice gate hasn't passed. Three judges scored the Operator's drafts at {score}% indistinguishable from yours. That's below the threshold we set so that no client could ever say 'did you write this?' We don't ship external drafts until that threshold is met. We have two paths forward."

2. **Path A — Internal-drafts-only mode.** the Operator continues drafting against internal surfaces only (intake notes, status reports to the partner, never to the partner's clients). Reduced retainer per the pricing strategy doc. Continue voice calibration in parallel; re-attempt blind-test at next monthly checkpoint.

3. **Path B — Pause beta-1.** Suspend the engagement with transparent explanation. Customer keeps Memory + audit log; refunds last month pro rata. Captain re-engages after Captain-side voice-model improvement, or releases the customer.

4. **Captain disclosure artifact.** Generate a markdown disclosure summary (samples, scores, recommendation) and store at `{slug}/voice-gate-disclosure/disclosure-{ts}.md`. Partner receives a copy.

### Internal-drafts-only mode (Path A)

- Skills authored as `external_send` action class produce drafts only to a `notes/` folder visible to Principal+Operator; no draft can land in the reviewer's actual email drafts folder.
- Operator dashboard shows banner: "Voice gate not yet passed. External drafts paused."
- The `external_send_blocked_by_voice_gate` flag in `skill_state` rows is set to 1.
- Trust-ceiling promotion for any `external_send` skill is blocked at the API layer regardless of role.
- Pricing reduction: per the pricing-strategy doc, internal-only retainer is 50-60% of the full retainer. Specifics deferred to that doc.

## Failure modes

- **Judge pool too small** (fewer than 3 partners-who-know-the-reviewer): adjust gate threshold to consensus rather than %; Captain documents in audit log. A 2-judge near-unanimous result (≥75% combined) ≈ a 3-judge 80% score per the original Captain calibration.
- **Sample-set leakage** (judges accidentally see which is which because of metadata): treat the blind-test as invalid, re-run with fresh sample set, no cycle count advances.
- **Customer pushes to ship external drafts despite a Fail score** ("we'll take the risk"): Captain declines — the §17.2 per-customer kill criterion "External AI disclosure incident" makes a customer-side risk a platform-side existential risk. The pause is unilateral.

## Verification

1. **Gate runner**: `bin/run-voice-gate.sh {customer-slug} {sample-set-id}` drives the blind-test, records judge inputs, computes score, writes `audit_log` event.
2. **Internal-drafts-only test** (`tests/operator/voice-gate-internal-only.test.ts`): flip `external_send_blocked_by_voice_gate = 1` on a fixture customer; assert every `external_send`-class draft is routed to notes/, every send API returns 403, dashboard banner is rendered.
3. **Disclosure artifact test**: generate a fixture disclosure summary; assert the R2 key, format, and partner-readable language match the template in `templates/voice-gate-disclosure.md`.
4. **Cycle bound test**: simulate 3 near-pass scores; assert the third triggers state transition to Fail.

## Implementation notes

- New file: `templates/voice-gate-disclosure.md` — Captain-readable template with placeholders for score, judge names, sample IDs.
- New script: `bin/run-voice-gate.sh` orchestrates the blind-test. Judge inputs collected via dashboard form at `/portal/operator/voice-gate/{run-id}` (compliance role-restricted; only invited judges see it).
- API gate: `src/pages/api/operator/drafts/[draft_id]/approve.ts` rejects with 403 when `external_send_blocked_by_voice_gate = 1` AND the draft's skill action class is `external_send`.
- Dashboard banner: `src/components/operator/VoiceGateBanner.tsx` reads the latest `audit_log` `VOICE_GATE_*` event for this customer.

## Resolved decision — judge pool for solo practitioners

**Captain proxies for missing judges. Audit log records the proxy explicitly.**

For solo practitioners (no firm staff to recruit as judges), Captain serves as one or two of the three judges. The audit log records `judge_panel: {N}_customer_chosen + {3-N}_captain_proxy` so the calibration record is honest and reviewable. The relaxed-threshold fallback at "Judge pool too small" in the Edge cases section above remains as a last-resort path **only when Captain is unavailable** (vacation, illness, oncall conflict) — not as the default solo-practitioner pattern.

Why this over a relaxed threshold: the blind-test gate exists to enforce independent verification. Relaxing the threshold for the solo cohort weakens the safety floor exactly where independent verification matters most (a solo practitioner has the least staff oversight to catch a voice mismatch in flight). Captain time is a real cost (~30-60 min per gate per customer); we accept that cost to preserve the floor.

[AMBIGUITY: The pricing strategy does not yet specify internal-drafts-only retainer math. This spec assumes 50-60% but the actual number is gated on §15.1 cost modeling. Resolve before first customer signs.]
