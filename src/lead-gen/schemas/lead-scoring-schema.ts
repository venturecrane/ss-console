/**
 * Lead Scoring Schema — Shared Types for Lead Generation Pipelines
 *
 * Defines the shared vocabulary used across all 5 lead generation pipelines.
 * Re-exports canonical problem and vertical types from the assessment schema
 * to maintain a single source of truth.
 *
 * @see docs/collateral/lead-automation-blueprint.md — Pipeline architecture
 * @see src/portal/assessments/extraction-schema.ts — Canonical problem/vertical types
 */

// ---------------------------------------------------------------------------
// Re-exports from assessment schema — single source of truth
// ---------------------------------------------------------------------------

export {
  PROBLEM_IDS,
  PROBLEM_LABELS,
  VERTICALS,
  type ProblemId,
  type Vertical,
} from '../../portal/assessments/extraction-schema.js'

// ---------------------------------------------------------------------------
// Lead source pipeline identifiers
// ---------------------------------------------------------------------------

export const PIPELINE_IDS = [
  'review_mining',
  'job_monitor',
  'new_business',
  'social_listening',
  'partner_nurture',
] as const

export type PipelineId = (typeof PIPELINE_IDS)[number]

export const PIPELINE_LABELS: Record<PipelineId, string> = {
  review_mining: 'Review Mining',
  job_monitor: 'Job Posting Monitor',
  new_business: 'New Business Detection',
  social_listening: 'Social Listening',
  partner_nurture: 'Referral Partner Nurture',
}

// ---------------------------------------------------------------------------
// Shared scoring types
// ---------------------------------------------------------------------------

/** A single piece of evidence supporting a problem signal. */
export interface ProblemEvidence {
  /** Which of the 6 universal problems this evidence maps to. */
  problem_id: import('../../portal/assessments/extraction-schema.js').ProblemId

  /** The exact text (review quote, job description excerpt, etc.) that signals the problem. */
  quote: string

  /** Severity of this specific signal: 1 (barely noticeable) to 10 (screaming pain). */
  severity: number
}

/** Scoring result produced by any lead qualification prompt. */
export interface ScoringResult {
  /** Overall operational pain score: 1 (no signal) to 10 (severe, multiple problems). */
  pain_score: number

  /** The top 1-3 problems detected, by canonical ID. */
  top_problems: import('../../portal/assessments/extraction-schema.js').ProblemId[]

  /** Supporting evidence for the scoring. */
  evidence: ProblemEvidence[]

  /**
   * A suggested outreach angle — how to open the conversation with this prospect.
   * Written in "we" voice, references their specific pain, never mentions pricing
   * or fixed timeframes.
   */
  outreach_angle: string
}

// ---------------------------------------------------------------------------
// Lead record — the universal output row for all pipelines
// ---------------------------------------------------------------------------

/** A qualified lead surfaced by any pipeline, ready for the output sheet. */
export interface LeadRecord {
  /** Business name as found in the source data. */
  business_name: string

  /** Phone number, if available. */
  phone: string | null

  /** Website URL, if available. */
  website: string | null

  /** Business category or type (e.g., "plumber", "CPA firm"). */
  category: string

  /** Phoenix metro sub-area (e.g., "Scottsdale", "Chandler", "Central Phoenix"). */
  area: string | null

  /** Which pipeline surfaced this lead. */
  source_pipeline: PipelineId

  /** Overall pain score from AI qualification. */
  pain_score: number

  /** Top problems detected, as canonical IDs. */
  top_problems: import('../../portal/assessments/extraction-schema.js').ProblemId[]

  /** Key evidence text (abbreviated for the sheet). */
  evidence_summary: string

  /** Suggested outreach angle. */
  outreach_angle: string

  /** ISO 8601 date when this lead was found. */
  date_found: string
}
