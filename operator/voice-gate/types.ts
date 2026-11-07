/**
 * Voice-gate harness — shared types.
 *
 * Source: Platform PRD §9.6 (three-gate voice quality model) and
 * docs/specs/operator/voice-gate-fallback.md (three-state Pass /
 * Near-pass / Fail contract). This file defines the typed shapes the
 * harness, scoring, panel, and (future) D1 writer agree on.
 *
 * Nothing in this module reads or writes external state. Persistence
 * is the responsibility of callers (CLI runner, future D1 writer in a
 * separate workstream — see README integration section).
 */

/**
 * Recipient cohort for blind-test framing. Aligned with the schema's
 * audience vocabulary (`operator/bin/lib/voice_corpus.py` BASE_COHORTS; the schema's
 * cohort block was retired with ADR 0083 decision 6).
 *
 * v1 base cohorts per PRD §9.3 Layer 3: `client`, `opposing-counsel`,
 * `court`, `internal`. Issue #857 added `court` and `internal` to the
 * union when the cohort vocabulary was lifted into the schema.
 *
 * `internal-team` is preserved as a legacy alias so archived blind-
 * test runs scored against the old PR-#857-predates label keep
 * rendering. New runs should use `internal`. The legacy slug stays in
 * the union but does NOT appear in the fixed audience vocabulary.
 *
 * New cohorts must be added here AND threaded through the panel +
 * fixture loader + scoring; the closed union prevents silent drift.
 */
export type RecipientCohort = 'client' | 'opposing-counsel' | 'court' | 'internal' | 'internal-team'

/**
 * Authorship label attached to a draft. The blind test hides this label
 * from judges; the harness uses it to grade their identifications.
 */
export type DraftAuthorship = 'customer' | 'agent'

/**
 * Judge's identification choice. `uncertain` is a deliberate third option
 * per voice-gate-fallback.md §Contract: indistinguishability counts both
 * "labeled customer when actually agent" AND "uncertain — could be either"
 * as the judge being unable to reliably identify the agent.
 */
export type JudgeChoice = 'customer' | 'agent' | 'uncertain'

/**
 * One draft presented to judges in the blind test. The harness shuffles
 * these so judges see them in randomized order; `authorship` is the
 * ground-truth label the panel layer hides during presentation and the
 * scoring layer reads when grading.
 */
export interface BlindTestDraft {
  /** Stable ID, stable across runs (e.g. "smith-pi-firm/client/draft-001"). */
  id: string
  /** Recipient cohort this draft was authored for. */
  cohort: RecipientCohort
  /** Ground-truth label. Hidden from judges during presentation. */
  authorship: DraftAuthorship
  /** Draft body text. Plaintext or markdown; harness does not render HTML. */
  body: string
  /**
   * Optional metadata (subject line, scenario tag). Surfaced to judges only
   * if `includeInPresentation: true` to preserve realism without leaking
   * the authorship label.
   */
  metadata?: {
    subject?: string
    scenario?: string
    includeInPresentation?: boolean
  }
}

/**
 * One judge's identification of one draft. Recorded by the panel layer
 * during a blind-test run; consumed by scoring.
 */
export interface JudgeIdentification {
  /** Draft this judgment refers to. */
  draft_id: string
  /** Judge identifier. Opaque to the harness. */
  judge_id: string
  /** Judge's choice. */
  choice: JudgeChoice
  /** Optional free-text reasoning the judge gave. */
  notes?: string
}

/**
 * The complete record of one blind-test run, persisted to per-customer
 * storage (D1 + R2 — wiring is out of scope for this workstream; see
 * voice-gate-fallback.md §Verification).
 */
export interface BlindTestRun {
  /** ULID-shaped run ID. */
  run_id: string
  /** Customer slug (e.g. "smith-pi-firm"). */
  customer_slug: string
  /** Cohort scoped for this run, or 'all' if mixed. */
  cohort: RecipientCohort | 'all'
  /** ISO 8601 UTC timestamp when the run was created. */
  started_at: string
  /** ISO 8601 UTC timestamp when scoring completed. May be null if mid-run. */
  scored_at: string | null
  /** Drafts presented to judges. */
  drafts: BlindTestDraft[]
  /** Judge IDs invited to the panel. */
  panel: string[]
  /** All identifications captured. */
  identifications: JudgeIdentification[]
  /**
   * Cycle count: 0 for the first run on a customer, 1 for the first
   * near-pass retry, 2 for the second. See voice-gate-fallback.md
   * §Near-pass cycle.
   */
  cycle_count: number
}

/**
 * Outcome state for a blind test. Matches the three-state contract in
 * voice-gate-fallback.md §Three states.
 */
export type GateState = 'pass' | 'near-pass' | 'fail'

/**
 * Audit-log action_type values written when a gate run resolves. See
 * d1-schema.md §1 (audit_log) accepted action_type list. The audit-log
 * writer is a separate workstream; this harness emits the structured
 * record and the integration point is documented in README.
 */
export type GateAuditAction = 'VOICE_GATE_PASSED' | 'VOICE_GATE_NEAR_PASS' | 'VOICE_GATE_FAILED'

/**
 * Result of scoring a blind-test run. The harness emits one of these
 * regardless of outcome; downstream consumers branch on `state` to
 * dispatch fallback behavior (live promotion / calibration cycle /
 * disclosure protocol).
 */
export interface GateResult {
  /** Pass / near-pass / fail per the threshold constants in scoring.ts. */
  state: GateState
  /** Audit-log action_type to emit. */
  audit_action: GateAuditAction
  /** Indistinguishability percentage in [0, 100], rounded to one decimal. */
  score_pct: number
  /** Raw indistinguishable count over total agent-drafted judgments. */
  indistinguishable_count: number
  /** Total agent-drafted judgments evaluated. */
  total_agent_judgments: number
  /**
   * Per-cohort score breakdown when the run mixed cohorts. Empty when
   * the run was scoped to a single cohort.
   */
  per_cohort?: Record<RecipientCohort, CohortScore>
  /** Human-readable summary line (used by the CLI and disclosure artifact). */
  summary: string
  /**
   * Failure record per voice-gate-fallback.md §Fail state. Populated only
   * when `state === 'fail'`. Encodes the structured record the
   * disclosure protocol consumes; the actual disclosure-document
   * generation is downstream.
   */
  failure_record?: FailureRecord
  /**
   * Near-pass record per voice-gate-fallback.md §Near-pass cycle.
   * Populated only when `state === 'near-pass'`. Tells the caller which
   * cycle this is and whether a third near-pass would auto-transition
   * to fail.
   */
  near_pass_record?: NearPassRecord
}

/**
 * Per-cohort score breakdown.
 */
export interface CohortScore {
  cohort: RecipientCohort
  score_pct: number
  indistinguishable_count: number
  total_agent_judgments: number
}

/**
 * Structured failure record. Captain's disclosure protocol generates a
 * markdown artifact from this; the live-promotion gate fails closed
 * based on its presence in the audit log.
 */
export interface FailureRecord {
  /** Score that triggered failure (<60% OR third near-pass). */
  score_pct: number
  /** Cycle count at time of failure (0, 1, or 2). */
  cycle_count: number
  /** Whether this is an auto-transition from the third near-pass. */
  auto_transitioned_from_near_pass: boolean
  /**
   * Cohorts that scored below threshold. For an `'all'` run these are
   * GATING (issue #1124): any covered cohort below threshold blocks the
   * pass even when the pooled overall score clears it, so a strong cohort
   * cannot mask a failing one. For a single-cohort run this is empty.
   */
  below_threshold_cohorts: RecipientCohort[]
  /**
   * Judge IDs whose misidentifications drove the failure — used by
   * Captain when reviewing which voice rules misfired.
   */
  flagged_judge_ids: string[]
  /**
   * Recommended path per voice-gate-fallback.md §Fail state. The harness
   * does not choose between Path A and Path B; Captain does. The
   * recommendation is informational based on score severity.
   */
  recommended_path: 'A_internal_drafts_only' | 'B_pause_engagement' | 'either'
}

/**
 * Structured near-pass record. The calibration cycle is operationally
 * Captain-led; the harness records the count so the third near-pass
 * triggers fail-state auto-transition.
 */
export interface NearPassRecord {
  /** Score in the 60-79.9 band. */
  score_pct: number
  /** Cycle count this run resolved at. */
  cycle_count: number
  /** Whether this was the final allowed near-pass cycle. */
  is_final_cycle: boolean
  /** Calendar days the customer must wait before re-running, per spec. */
  minimum_days_to_retry: number
}

/**
 * Persistence shape for the per-customer Hermes D1 panel-score row.
 * Defined here so the future D1 writer (separate workstream) consumes a
 * stable typed contract. Mirrors the audit_log row layout from
 * d1-schema.md §1 with voice-gate-specific metadata.
 */
export interface VoiceGatePanelScoreRow {
  /** ULID matching the audit_log row. */
  id: string
  /** ISO 8601 UTC. */
  ts: string
  /** action_type discriminator. */
  action_type: GateAuditAction
  /** 'captain' for voice-gate runs (Captain orchestrates the panel). */
  actor: 'captain'
  actor_role: 'captain'
  /** Empty for voice-gate events. */
  skill_name: null
  matter_ref: null
  input_digest: null
  output_digest: null
  diff_digest: null
  trust_ceiling: null
  /**
   * JSON-encoded payload. Keys: score, judge_ids (string[]), sample_set_id,
   * cycle_count, disclosure_artifact_r2_key?. See voice-gate-fallback.md
   * §Three states for the metadata field-by-state contract.
   */
  metadata: string
}
