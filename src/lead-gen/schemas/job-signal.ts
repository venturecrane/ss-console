/**
 * Job Signal Schema — Pipeline 2 Output Types
 *
 * Defines the structured output contract for the job posting qualification prompt.
 * When a Phoenix-area job posting signals operational pain at a small business,
 * the AI produces a JobQualification result matching this schema.
 *
 * @see docs/collateral/lead-automation-blueprint.md — Pipeline 2 architecture
 * @see src/lead-gen/prompts/job-qualification-prompt.ts — The prompt that produces this output
 */

import type { ProblemId } from './lead-scoring-schema.js'
import type { ProblemEvidence, ScoringResult } from './lead-scoring-schema.js'

export { type ProblemEvidence, type ScoringResult }

// ---------------------------------------------------------------------------
// Job qualification output
// ---------------------------------------------------------------------------

/** Result of AI qualification of a single job posting. */
export interface JobQualification {
  /** Company name from the job posting. */
  company: string

  /** Whether this company qualifies as a prospect. */
  qualified: boolean

  /** Confidence in the qualification decision. */
  confidence: 'high' | 'medium' | 'low'

  /**
   * Estimated employee count range, inferred from the job description.
   * "unknown" if no signals present.
   */
  company_size_estimate: string

  /** Which of the 6 universal problems the job posting signals. */
  problems_signaled: ProblemId[]

  /**
   * Key evidence from the job description supporting the qualification.
   * Direct quotes or close paraphrases from the posting.
   */
  evidence: string

  /**
   * Suggested outreach angle — how to approach this company.
   * Written in "we" voice, references their specific pain.
   * Never mentions pricing or fixed timeframes.
   */
  outreach_angle: string

  /**
   * Reason for disqualification, if qualified is false.
   * Null when qualified is true.
   */
  disqualification_reason: string | null
}

// ---------------------------------------------------------------------------
// Input data for the prompt
// ---------------------------------------------------------------------------

/** Raw job posting data as received from SerpAPI or Craigslist. */
export interface JobPostingInput {
  /** Job title as listed. */
  title: string

  /** Company name. */
  company: string

  /** Location (city, state). */
  location: string

  /** Full job description text. */
  description: string

  /** Source platform. */
  source: 'google_jobs' | 'craigslist' | 'other'

  /** URL to the original posting, if available. */
  url?: string
}
