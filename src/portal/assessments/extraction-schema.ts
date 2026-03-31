/**
 * Assessment Extraction Schema
 *
 * Defines the structured output contract for the Claude extraction prompt.
 * This schema maps directly to the `assessments.extraction` JSON column in D1.
 *
 * @see Decision #17 — Assessment Call Capture (MacWhisper + Claude)
 * @see PRD AC-3 — Store Claude extraction output as structured JSON
 * @see Deliverable #34 — MacWhisper extraction prompt
 */

// ---------------------------------------------------------------------------
// The 6 universal SMB operations problems — canonical identifiers
// ---------------------------------------------------------------------------

export const PROBLEM_IDS = [
  'owner_bottleneck',
  'lead_leakage',
  'financial_blindness',
  'scheduling_chaos',
  'manual_communication',
  'team_invisibility',
] as const

export type ProblemId = (typeof PROBLEM_IDS)[number]

export const PROBLEM_LABELS: Record<ProblemId, string> = {
  owner_bottleneck: 'Owner bottleneck',
  lead_leakage: 'Lead leakage',
  financial_blindness: 'Financial blindness',
  scheduling_chaos: 'Scheduling chaos',
  manual_communication: 'Manual communication',
  team_invisibility: 'Team invisibility',
}

// ---------------------------------------------------------------------------
// Verticals — matching the ICP from Decision #3 and #5
// ---------------------------------------------------------------------------

export const VERTICALS = [
  'home_services',
  'professional_services',
  'contractor_trades',
  'retail_salon',
  'restaurant_food',
  'other',
] as const

export type Vertical = (typeof VERTICALS)[number]

// ---------------------------------------------------------------------------
// Extraction output — the full structured JSON
// ---------------------------------------------------------------------------

/** A single identified problem from the assessment call. */
export interface IdentifiedProblem {
  /** Canonical problem ID from the 6 universal problems. */
  problem_id: ProblemId

  /** Severity as assessed from the conversation: high, medium, or low. */
  severity: 'high' | 'medium' | 'low'

  /** One-sentence summary of how this problem manifests for this business. */
  summary: string

  /**
   * Direct quotes or close paraphrases from the owner that illustrate the pain.
   * Usable in proposals and case studies. Minimum 1, ideally 2-3.
   */
  owner_quotes: string[]

  /**
   * What the owner said vs. what is likely broken underneath.
   * Helps the team scope the real fix, not just the symptom.
   */
  underlying_cause: string
}

/** Tools and software the business currently uses. */
export interface CurrentTool {
  /** Name of the tool (e.g., "QuickBooks Online", "Google Calendar"). */
  name: string

  /** What they use it for — may not match the tool's intended purpose. */
  purpose: string

  /**
   * How well it is working for them.
   * - "working" — no change needed, passes the 5-criteria rubric
   * - "underutilized" — right tool, not fully configured or adopted
   * - "failing" — wrong tool or badly broken workflow
   */
  status: 'working' | 'underutilized' | 'failing'
}

/** Signals that influence scope estimation and quote building. */
export interface ComplexitySignals {
  /** Number of employees mentioned or estimated from context. */
  employee_count: number | null

  /** Number of business locations discussed. */
  location_count: number

  /** Any tool migrations that would be required (e.g., "move from spreadsheets to CRM"). */
  tool_migrations: string[]

  /** Approximate data volume signals (e.g., "500 client records in spreadsheet"). */
  data_volume_notes: string[]

  /** Any integration requirements mentioned (e.g., "needs to sync with QuickBooks"). */
  integration_needs: string[]

  /** Anything else that would increase estimated hours. */
  additional_factors: string[]
}

/** Champion candidate — the internal person who will own the solution post-delivery. */
export interface ChampionCandidate {
  /** Name if mentioned, null if not identified. */
  name: string | null

  /** Role or title if mentioned. */
  role: string | null

  /** Evidence from the call that this person is a viable champion. */
  evidence: string

  /**
   * Confidence level that this person can fulfill the champion role.
   * Based on Decision #28 enablement standard: can they explain, operate, and diagnose?
   */
  confidence: 'strong' | 'moderate' | 'weak'
}

/** Hard and soft disqualification flags from Decision #4. */
export interface DisqualificationFlags {
  /**
   * Hard disqualifiers — any true value means automatic no.
   * 1. Not speaking to the owner/check-writer
   * 2. Scope clearly exceeds sprint window (multi-location, ERP, franchise)
   * 3. No tech baseline (no email, no internet, no existing tools)
   */
  hard: {
    not_decision_maker: boolean
    scope_exceeds_sprint: boolean
    no_tech_baseline: boolean
  }

  /**
   * Soft disqualifiers — yellow flags that need probing.
   * 1. No internal champion identified
   * 2. Books more than 30 days behind
   * 3. No willingness to change
   */
  soft: {
    no_champion: boolean
    books_behind: boolean
    no_willingness_to_change: boolean
  }

  /** Free-text notes on any flags that were triggered. */
  notes: string
}

/** Budget signal proxies from Decision #4 — we never ask for revenue directly. */
export interface BudgetSignals {
  /** Does the business have 2+ employees on payroll (not all contractors)? */
  employees_on_payroll: boolean | null

  /** Is the business 3+ years old? */
  years_in_business_3_plus: boolean | null

  /** Is the business in crisis mode (layoffs, pending closure)? */
  in_crisis: boolean | null

  /** Any other signals observed. */
  notes: string
}

/**
 * Factors that drive the quote — extracted signals that feed into
 * the quote builder's estimated hours per line item.
 */
export interface QuoteDrivers {
  /** Recommended problems to address, in priority order (max 3). */
  recommended_problems: ProblemId[]

  /** Estimated total engagement complexity: low (15-20h), medium (20-30h), high (30-40h). */
  estimated_complexity: 'low' | 'medium' | 'high'

  /** Specific factors that would increase hours beyond baseline. */
  upward_pressures: string[]

  /** Specific factors that would decrease hours below baseline. */
  downward_pressures: string[]

  /** Any ROI anchor math the owner verbalized during the call (Decision #15). */
  roi_anchors: string[]
}

// ---------------------------------------------------------------------------
// The top-level extraction output
// ---------------------------------------------------------------------------

/**
 * Complete structured extraction from an assessment call transcript.
 *
 * This is the JSON stored in `assessments.extraction` in D1.
 * The `problems` column stores just the problem IDs array.
 * The `disqualifiers` column stores the DisqualificationFlags.
 * The `champion_name` and `champion_role` columns are denormalized
 * from `champion_candidate` for quick access.
 */
export interface AssessmentExtraction {
  /** Schema version for forward compatibility. */
  schema_version: '1.0'

  /** ISO 8601 timestamp of when the extraction was performed. */
  extracted_at: string

  // -- Business profile --------------------------------------------------

  /** Business name as stated on the call. */
  business_name: string

  /** Primary vertical classification. */
  vertical: Vertical

  /** Sub-vertical or specific business type (e.g., "residential HVAC", "family law"). */
  business_type: string

  /** Years in business, if mentioned. */
  years_in_business: number | null

  /** Employee count, if mentioned or estimated. */
  employee_count: number | null

  /** Phoenix metro sub-area if mentioned (e.g., "Scottsdale", "Mesa", "Chandler"). */
  geography: string | null

  /** Tools and software currently in use. */
  current_tools: CurrentTool[]

  // -- Problems identified ------------------------------------------------

  /**
   * 2-3 problems identified from the 6 universal SMB operations problems.
   * Ordered by severity (highest first).
   */
  identified_problems: IdentifiedProblem[]

  // -- Complexity and scope -----------------------------------------------

  /** Signals that influence scope and quote estimation. */
  complexity_signals: ComplexitySignals

  // -- People -------------------------------------------------------------

  /** Champion candidate for post-delivery ownership. */
  champion_candidate: ChampionCandidate | null

  /** Who was on the call (owner name, role, anyone else present). */
  call_participants: string[]

  // -- Qualification ------------------------------------------------------

  /** Hard and soft disqualification flags. */
  disqualification_flags: DisqualificationFlags

  /** Budget signal proxies. */
  budget_signals: BudgetSignals

  // -- Quote drivers ------------------------------------------------------

  /** Factors that feed into the quote builder. */
  quote_drivers: QuoteDrivers

  // -- Meta ---------------------------------------------------------------

  /** One-paragraph executive summary of the assessment call. */
  executive_summary: string

  /** Anything notable that doesn't fit the structured fields above. */
  additional_notes: string
}
