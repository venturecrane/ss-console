/**
 * Review Signal Schema — Pipeline 1 Output Types
 *
 * Defines the structured output contract for the review scoring prompt.
 * When Google/Yelp reviews for a Phoenix-area business reveal operational
 * pain patterns, the AI produces a ReviewScoring result matching this schema.
 *
 * @see docs/collateral/lead-automation-blueprint.md — Pipeline 1 architecture
 * @see src/lead-gen/prompts/review-scoring-prompt.ts — The prompt that produces this output
 */

import type { ProblemId, ProblemEvidence } from './lead-scoring-schema.js'

export { type ProblemEvidence }

// ---------------------------------------------------------------------------
// Review scoring output — single business
// ---------------------------------------------------------------------------

/** A single review that contains an operational signal. */
export interface ReviewSignal {
  /** Which of the 6 universal problems this review maps to. */
  problem_id: ProblemId

  /** The exact quote from the review that signals the problem. */
  quote: string

  /** Star rating of this specific review (1-5). */
  review_rating: number

  /** Severity of the operational signal: 1-10. */
  severity: number
}

/** Result of AI scoring for a single business's reviews. */
export interface ReviewScoring {
  /** Business name. */
  business_name: string

  /** Google Places place_id for deduplication. */
  place_id: string

  /**
   * Overall operational pain score: 1 (no signal) to 10 (severe, repeated patterns).
   * Scoring calibration:
   * - 1-3: No meaningful operational signals. Complaints are about service quality, not operations.
   * - 4-6: Some operational signals but isolated incidents, not patterns.
   * - 7-8: Clear operational pattern across multiple reviews. Worth outreach.
   * - 9-10: Severe, repeated operational failures documented by multiple customers.
   */
  pain_score: number

  /** The top 1-3 problems detected, by canonical ID. Ordered by severity. */
  top_problems: ProblemId[]

  /** Individual review signals supporting the score. */
  signals: ReviewSignal[]

  /**
   * Suggested outreach angle — how to approach this business.
   * References their specific pain as seen in reviews.
   * Written in "we" voice. Never mentions pricing or fixed timeframes.
   */
  outreach_angle: string
}

// ---------------------------------------------------------------------------
// Batch scoring output — multiple businesses in one call
// ---------------------------------------------------------------------------

/** Result of a batch scoring call (5-10 businesses per prompt). */
export interface BatchReviewScoring {
  /** Array of scored businesses. */
  businesses: ReviewScoring[]

  /** Total reviews analyzed across all businesses. */
  total_reviews_analyzed: number
}

// ---------------------------------------------------------------------------
// Input data for the prompt
// ---------------------------------------------------------------------------

/** A single review as received from Outscraper or similar API. */
export interface ReviewInput {
  /** Review author name. */
  author: string

  /** Star rating (1-5). */
  rating: number

  /** Full review text. */
  text: string

  /** Review date (ISO 8601 or human-readable). */
  date: string
}

/** Input data for scoring a single business's reviews. */
export interface BusinessReviewInput {
  /** Business name. */
  business_name: string

  /** Google Places place_id. */
  place_id: string

  /** Business category (e.g., "plumber", "HVAC", "dentist"). */
  category: string

  /** Phoenix sub-area. */
  area: string

  /** Google overall rating (1-5). */
  overall_rating: number

  /** Total review count on Google. */
  total_review_count: number

  /** The reviews to analyze (recent reviews from Outscraper). */
  reviews: ReviewInput[]
}
