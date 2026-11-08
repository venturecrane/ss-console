/**
 * Voice-gate scoring — threshold logic and fallback dispatch.
 *
 * Source: Platform PRD §9.6 (Gate 3 acceptance threshold ≥80%) and
 * docs/specs/operator/voice-gate-fallback.md §Three states (Pass /
 * Near-pass / Fail bands and audit-log action mapping).
 *
 * The threshold is encoded once here as a named constant; never repeat
 * the magic number. Future changes — Captain-led threshold tuning,
 * judge-pool size adjustments — land in this file.
 */

import type {
  BlindTestDraft,
  BlindTestRun,
  CohortScore,
  FailureRecord,
  GateAuditAction,
  GateResult,
  GateState,
  JudgeIdentification,
  NearPassRecord,
  RecipientCohort,
} from './types.js'

/**
 * Pass threshold per PRD §9.6 and voice-gate-fallback.md §Three states.
 * Indistinguishability ≥80% unlocks first external draft.
 */
export const VOICE_GATE_PASS_THRESHOLD_PCT = 80

/**
 * Near-pass lower bound. Scores in [60, 80) trigger calibration cycle,
 * not failure. Per voice-gate-fallback.md §Near-pass cycle.
 */
export const VOICE_GATE_NEAR_PASS_LOWER_PCT = 60

/**
 * Maximum near-pass cycles before auto-transition to fail. Per
 * voice-gate-fallback.md §Near-pass cycle step 4 ("Maximum 2 near-pass
 * cycles. If the third blind-test still scores <80%, transition to
 * Fail state.").
 */
export const VOICE_GATE_MAX_NEAR_PASS_CYCLES = 2

/**
 * Minimum calendar days a customer must wait between near-pass cycles,
 * per voice-gate-fallback.md §Near-pass cycle step 3.
 */
export const VOICE_GATE_MIN_DAYS_BETWEEN_CYCLES = 7

/**
 * Cohorts the v1 model recognizes. Aligned with the schema's
 * audience vocabulary (`operator/bin/lib/voice_corpus.py` BASE_COHORTS; the schema's
 * cohort block was retired with ADR 0083 decision 6).
 * Issue #857 added `court` and `internal` here when the cohort
 * vocabulary was lifted into the schema; `internal-team` is the
 * legacy alias retained for archived blind-test runs and is included
 * here so existing fixtures keep loading.
 *
 * Centralized here so scoring + fixture loading + panel agree.
 */
export const RECIPIENT_COHORTS: ReadonlyArray<RecipientCohort> = [
  'client',
  'opposing-counsel',
  'court',
  'internal',
  'internal-team',
]

/**
 * Cohorts required for a valid `'all'` blind-test run. Mirrors the
 * historical three-cohort baseline (client, opposing-counsel, internal
 * OR internal-team). `court` is accepted (in `RECIPIENT_COHORTS`) but
 * NOT required so transactional firms with no court practice can still
 * pass an `'all'` run.
 *
 * `internal` / `internal-team` are interchangeable for coverage — a
 * run with either satisfies the internal slot. Issue #857.
 */
export const REQUIRED_COHORTS_FOR_ALL_RUN: ReadonlyArray<RecipientCohort> = [
  'client',
  'opposing-counsel',
]

/**
 * Equivalence classes for cohort coverage. The internal slot is
 * satisfied by either `internal` or the legacy `internal-team` alias.
 */
const INTERNAL_COHORT_EQUIVALENTS: ReadonlySet<RecipientCohort> = new Set([
  'internal',
  'internal-team',
])

/**
 * Resolve which cohorts of `REQUIRED_COHORTS_FOR_ALL_RUN` (plus the
 * internal-equivalence class) are NOT covered by a draft set. Used by
 * the panel layer to flag missing-cohort 'all' runs without forcing
 * customers to cover the optional `court` cohort.
 */
export function missingRequiredCohorts(seen: ReadonlySet<RecipientCohort>): RecipientCohort[] {
  const missing: RecipientCohort[] = []
  for (const c of REQUIRED_COHORTS_FOR_ALL_RUN) {
    if (!seen.has(c)) missing.push(c)
  }
  const hasInternal = [...INTERNAL_COHORT_EQUIVALENTS].some((c) => seen.has(c))
  if (!hasInternal) missing.push('internal')
  return missing
}

/**
 * Map a gate state to the audit_log action_type per d1-schema.md §1.
 */
export function auditActionFor(state: GateState): GateAuditAction {
  switch (state) {
    case 'pass':
      return 'VOICE_GATE_PASSED'
    case 'near-pass':
      return 'VOICE_GATE_NEAR_PASS'
    case 'fail':
      return 'VOICE_GATE_FAILED'
  }
}

/**
 * Convert a numeric score to its gate state. The state machine is
 * intentionally simple; cycle-count auto-transition (third near-pass
 * → fail) is layered on top by `scoreRun`, not in this function.
 */
export function stateForScore(score_pct: number): GateState {
  if (score_pct >= VOICE_GATE_PASS_THRESHOLD_PCT) return 'pass'
  if (score_pct >= VOICE_GATE_NEAR_PASS_LOWER_PCT) return 'near-pass'
  return 'fail'
}

/**
 * Indistinguishability counts when a judge labels an agent-drafted item
 * as `customer` OR `uncertain`. Per voice-gate-fallback.md §Contract:
 * "indistinguishability score = % of judgments where the judge was
 * unable to reliably identify the agent-drafted item (i.e., labeled it
 * 'reviewer' OR explicitly marked 'uncertain — could be either')."
 */
function isIndistinguishable(judgment: JudgeIdentification): boolean {
  return judgment.choice === 'customer' || judgment.choice === 'uncertain'
}

/**
 * Compute the indistinguishability percentage for a subset of drafts.
 * Returns 0 with a denominator of 0 when no agent-drafted items were
 * judged (caller decides whether that's a fail-closed or skip).
 */
function scoreForDraftSubset(
  drafts: BlindTestDraft[],
  judgments: JudgeIdentification[]
): { score_pct: number; indistinguishable: number; total: number } {
  const agentDraftIds = new Set(drafts.filter((d) => d.authorship === 'agent').map((d) => d.id))
  const agentJudgments = judgments.filter((j) => agentDraftIds.has(j.draft_id))
  const total = agentJudgments.length
  if (total === 0) {
    return { score_pct: 0, indistinguishable: 0, total: 0 }
  }
  const indistinguishable = agentJudgments.filter(isIndistinguishable).length
  const score_pct = Math.round((indistinguishable / total) * 1000) / 10
  return { score_pct, indistinguishable, total }
}

/**
 * Pick out the judge IDs whose misidentifications (correct identification
 * of an agent draft) drove the score down. Used by the failure record to
 * help Captain review which judges' instincts to weight in calibration.
 */
function flaggedJudgesFor(drafts: BlindTestDraft[], judgments: JudgeIdentification[]): string[] {
  const agentDraftIds = new Set(drafts.filter((d) => d.authorship === 'agent').map((d) => d.id))
  const flagged = new Set<string>()
  for (const j of judgments) {
    if (agentDraftIds.has(j.draft_id) && j.choice === 'agent') {
      flagged.add(j.judge_id)
    }
  }
  return [...flagged].sort()
}

/**
 * Recommend Path A vs Path B per voice-gate-fallback.md §Fail state.
 * Severity-based: catastrophic failures (<40%) lean toward Path B
 * (pause engagement); marginal failures (40-59%) lean toward Path A
 * (internal-drafts-only). Captain makes the final call.
 */
function recommendedPathFor(
  score_pct: number,
  auto_transitioned: boolean
): 'A_internal_drafts_only' | 'B_pause_engagement' | 'either' {
  if (auto_transitioned) return 'A_internal_drafts_only'
  if (score_pct < 40) return 'B_pause_engagement'
  if (score_pct < 50) return 'either'
  return 'A_internal_drafts_only'
}

/**
 * Compute the per-cohort score breakdown when the run mixed cohorts.
 * Returns undefined when the run was scoped to a single cohort to
 * keep the CLI output uncluttered.
 */
function perCohortBreakdown(run: BlindTestRun): Record<RecipientCohort, CohortScore> | undefined {
  if (run.cohort !== 'all') return undefined
  const result = {} as Record<RecipientCohort, CohortScore>
  for (const cohort of RECIPIENT_COHORTS) {
    const cohortDrafts = run.drafts.filter((d) => d.cohort === cohort)
    const cohortJudgments = run.identifications.filter((j) =>
      cohortDrafts.some((d) => d.id === j.draft_id)
    )
    const sub = scoreForDraftSubset(cohortDrafts, cohortJudgments)
    result[cohort] = {
      cohort,
      score_pct: sub.score_pct,
      indistinguishable_count: sub.indistinguishable,
      total_agent_judgments: sub.total,
    }
  }
  return result
}

/**
 * Score a blind-test run and emit the full structured result.
 *
 * Threshold logic:
 *
 *   1. Compute overall indistinguishability % across all agent-drafted
 *      judgments (single cohort or 'all').
 *   2. If `cycle_count >= MAX_NEAR_PASS_CYCLES` AND score < 80%, force
 *      fail with `auto_transitioned_from_near_pass: true`. This is the
 *      "third blind-test still scores <80%" auto-transition rule.
 *   3. Otherwise map score → state via `stateForScore`.
 *   4. Build the audit action + structured per-state record.
 *
 * Pure function — does not write to D1, does not call audit log, does
 * not send messages. Caller persists the result.
 */
export function scoreRun(run: BlindTestRun): GateResult {
  const overall = scoreForDraftSubset(run.drafts, run.identifications)

  const cycleCountForcesfail =
    run.cycle_count >= VOICE_GATE_MAX_NEAR_PASS_CYCLES &&
    overall.score_pct < VOICE_GATE_PASS_THRESHOLD_PCT

  const naturalState = stateForScore(overall.score_pct)

  const per_cohort = perCohortBreakdown(run)
  // Skip cohorts that had no agent-drafted judgments in the run — they
  // were absent (not under-performing). #857 expanded RECIPIENT_COHORTS
  // so most runs won't cover every entry; flagging an absent cohort as
  // "below threshold" would mislead Captain into recalibrating something
  // they never tested.
  const below_threshold_cohorts: RecipientCohort[] = per_cohort
    ? RECIPIENT_COHORTS.filter(
        (c) =>
          per_cohort[c].total_agent_judgments > 0 &&
          per_cohort[c].score_pct < VOICE_GATE_PASS_THRESHOLD_PCT
      )
    : []

  // Per-cohort gating (issue #1124). For an 'all' run, the pooled overall
  // score can let a strong cohort (e.g. `client`) mask a failing one
  // (e.g. `opposing-counsel`), which would unlock sends to the very
  // cohort the firm is most exposed on. So an 'all' run passes ONLY if
  // every COVERED cohort independently clears the threshold; otherwise
  // the run is downgraded to the weakest covered cohort's state.
  let state: GateState = cycleCountForcesfail ? 'fail' : naturalState
  if (run.cohort === 'all' && state === 'pass' && per_cohort) {
    const coveredStates = RECIPIENT_COHORTS.filter(
      (c) => per_cohort[c].total_agent_judgments > 0
    ).map((c) => stateForScore(per_cohort[c].score_pct))
    if (coveredStates.includes('fail')) state = 'fail'
    else if (coveredStates.includes('near-pass')) state = 'near-pass'
  }

  let failure_record: FailureRecord | undefined
  let near_pass_record: NearPassRecord | undefined

  if (state === 'fail') {
    failure_record = {
      score_pct: overall.score_pct,
      cycle_count: run.cycle_count,
      auto_transitioned_from_near_pass: cycleCountForcesfail,
      below_threshold_cohorts,
      flagged_judge_ids: flaggedJudgesFor(run.drafts, run.identifications),
      recommended_path: recommendedPathFor(overall.score_pct, cycleCountForcesfail),
    }
  } else if (state === 'near-pass') {
    const isFinal = run.cycle_count >= VOICE_GATE_MAX_NEAR_PASS_CYCLES - 1
    near_pass_record = {
      score_pct: overall.score_pct,
      cycle_count: run.cycle_count,
      is_final_cycle: isFinal,
      minimum_days_to_retry: VOICE_GATE_MIN_DAYS_BETWEEN_CYCLES,
    }
  }

  const summary = formatSummary(state, overall.score_pct, run, below_threshold_cohorts)

  return {
    state,
    audit_action: auditActionFor(state),
    score_pct: overall.score_pct,
    indistinguishable_count: overall.indistinguishable,
    total_agent_judgments: overall.total,
    per_cohort,
    summary,
    failure_record,
    near_pass_record,
  }
}

/**
 * One-line human-readable summary. Mirrors what Captain would write into
 * the audit log; the CLI prints this verbatim and the disclosure template
 * splices it in.
 */
function formatSummary(
  state: GateState,
  score_pct: number,
  run: BlindTestRun,
  below_threshold_cohorts: RecipientCohort[] = []
): string {
  const cohortLabel = run.cohort === 'all' ? 'all cohorts' : `cohort ${run.cohort}`
  const cycleLabel = run.cycle_count > 0 ? ` (cycle ${run.cycle_count + 1})` : ''
  // When an 'all' run is held back by a specific cohort despite a passing
  // pooled score, name the culprit cohort(s) so the reason is legible.
  const cohortNote =
    state !== 'pass' &&
    run.cohort === 'all' &&
    below_threshold_cohorts.length > 0 &&
    score_pct >= VOICE_GATE_PASS_THRESHOLD_PCT
      ? ` [held back by cohort(s) below threshold: ${below_threshold_cohorts.join(', ')}]`
      : ''
  switch (state) {
    case 'pass':
      return `PASS at ${score_pct}% across ${cohortLabel}${cycleLabel} — external drafts unlocked.`
    case 'near-pass':
      return `NEAR-PASS at ${score_pct}% across ${cohortLabel}${cycleLabel}${cohortNote} — calibration cycle required.`
    case 'fail':
      return `FAIL at ${score_pct}% across ${cohortLabel}${cycleLabel}${cohortNote} — external drafts blocked; Captain disclosure protocol triggered.`
  }
}

/**
 * Build the JSON metadata payload for the audit_log row. Field set per
 * voice-gate-fallback.md §Three states ("State recorded in audit_log…").
 */
export function buildAuditMetadata(run: BlindTestRun, result: GateResult): string {
  const base: Record<string, unknown> = {
    score: result.score_pct,
    judge_ids: run.panel,
    sample_set_id: run.run_id,
    cycle_count: run.cycle_count,
  }
  if (result.failure_record) {
    base['failure_recommended_path'] = result.failure_record.recommended_path
    base['failure_auto_transitioned'] = result.failure_record.auto_transitioned_from_near_pass
    // disclosure_artifact_r2_key is filled in by the downstream disclosure
    // generator and merged onto this metadata when the row is written.
  }
  if (result.per_cohort) {
    base['per_cohort'] = result.per_cohort
  }
  return JSON.stringify(base)
}
