/**
 * New Business Signal Schema — Pipeline 3 Output Types
 *
 * Defines the structured output contract for the new business qualification prompt.
 * When a new business filing (ACC), TPT license (ADOR), or commercial permit
 * (city SODA API) signals a growing or launching business in the Phoenix area,
 * the AI produces a NewBusinessQualification result matching this schema.
 *
 * @see docs/collateral/lead-automation-blueprint.md — Pipeline 3 architecture
 * @see src/lead-gen/prompts/new-business-prompt.ts — The prompt that produces this output
 */

import type { Vertical } from './lead-scoring-schema.js'

// ---------------------------------------------------------------------------
// Source identifiers for new business signals
// ---------------------------------------------------------------------------

export const NEW_BUSINESS_SOURCES = [
  'acc_filing',
  'ador_tpt',
  'phoenix_permit',
  'scottsdale_permit',
  'chandler_permit',
  'sba_loan',
] as const

export type NewBusinessSource = (typeof NEW_BUSINESS_SOURCES)[number]

// ---------------------------------------------------------------------------
// Outreach timing recommendations
// ---------------------------------------------------------------------------

export const OUTREACH_TIMINGS = [
  'immediate',
  'wait_30_days',
  'wait_60_days',
  'not_recommended',
] as const

export type OutreachTiming = (typeof OUTREACH_TIMINGS)[number]

// ---------------------------------------------------------------------------
// New business qualification output
// ---------------------------------------------------------------------------

/** Result of AI qualification of a single new business filing or permit. */
export interface NewBusinessQualification {
  /** Business name as listed on the filing, license, or permit. */
  business_name: string

  /** Entity type from the filing (e.g., "LLC", "Corp", "Sole Prop", "Partnership"). */
  entity_type: string

  /** Registered or physical address from the filing. */
  address: string

  /** Phoenix metro sub-area (e.g., "Scottsdale", "Chandler", "Central Phoenix"). Null if unknown. */
  area: string | null

  /** Which public record source this filing came from. */
  source: NewBusinessSource

  /**
   * Best-guess vertical match based on business name, permit type, and available context.
   * "unknown" if the business type cannot be determined from available data.
   */
  vertical_match: Vertical | 'unknown'

  /**
   * Estimated employee count range, inferred from entity type, permit type, and context.
   * Examples: "10-25", "5-10", "unknown".
   */
  size_estimate: string

  /**
   * Recommended outreach timing based on source type and business readiness.
   * @see src/lead-gen/prompts/new-business-prompt.ts for timing guidance by source.
   */
  outreach_timing: OutreachTiming

  /**
   * Suggested outreach angle — how to approach this new business.
   * Written in "we" voice. Never mentions pricing or fixed timeframes.
   */
  outreach_angle: string

  /** Additional notes — reasoning, disqualification rationale, or context for the outreach team. */
  notes: string
}

// ---------------------------------------------------------------------------
// Input data for the prompt
// ---------------------------------------------------------------------------

/** Raw new business data as received from ACC, ADOR, or city SODA APIs. */
export interface NewBusinessInput {
  /** Business name as listed on the filing. */
  business_name: string

  /** Entity type (e.g., "Domestic LLC", "Foreign Corp", "Sole Proprietorship"). */
  entity_type: string

  /** Registered or physical address. */
  address: string

  /** Filing or license date (ISO 8601). */
  filing_date: string

  /** Which public record source this data came from. */
  source: NewBusinessSource

  /** Permit type, for city permits (e.g., "Commercial TI", "New Construction", "Change of Use"). */
  permit_type?: string

  /** Any additional data from the source (e.g., SIC code, NAICS, business description). */
  additional_data?: string
}
