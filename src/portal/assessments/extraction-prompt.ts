/**
 * Assessment Extraction Prompt — Deliverable #34
 *
 * Standard prompt template that turns a MacWhisper speaker-separated transcript
 * into structured assessment data matching the AssessmentExtraction schema.
 *
 * Phase 1 (manual): Admin copies the prompt output, pastes transcript, copies result
 *                    into the portal assessment record.
 * Phase 5 (automated): Portal calls Claude API with this prompt + transcript,
 *                       parses JSON response, populates assessment record.
 *
 * @see Decision #17 — Assessment Call Capture
 * @see Decision #4 — Disqualification Criteria
 * @see Decision #5 — Ideal Client Profile
 * @see Decision #9 — Tool Evaluation Framework
 * @see Decision #15 — ROI Anchor Math
 * @see Decision #28 — Internal Champion
 */

import { PROBLEM_IDS, VERTICALS, REVENUE_RANGES } from './extraction-schema.js'
import type { AssessmentExtraction } from './extraction-schema.js'

// Re-export the type so callers can import both from this module
export type { AssessmentExtraction }

/**
 * The system prompt that establishes context for extraction.
 * Separated from the user prompt so it can be used as a Claude API
 * system message in Phase 5.
 */
export const EXTRACTION_SYSTEM_PROMPT = `You are an assessment extraction assistant for SMD Services, an operations consulting firm that works with Phoenix-based small and mid-size businesses (750K to 5M annual revenue).

Your job is to analyze a speaker-separated transcript from an assessment call and produce structured JSON output. The call is between our consulting team and a business owner (or their representative) to understand their operational challenges and determine if we can help.

## The 5 Solution Capability Areas

These are the core areas where growing businesses get stuck. Your job is to identify which 2-3 are most acute for this business:

1. **Process design** (process_design) — Workflows live in people's heads. No documentation, unclear delegation, decisions bottleneck at the owner. The business can't run without them.
2. **Tools & systems** (tool_systems) — Tools are missing, underutilized, or not connected. The team works around gaps with manual effort, duplicate entry, and tribal knowledge about how things "really" work.
3. **Data & visibility** (data_visibility) — The owner can't see the business in real time. Books are behind, pricing is gut feel, and profitability by job or service is unknown.
4. **Customer pipeline** (customer_pipeline) — Leads come in but there's no consistent capture, follow-up, or conversion flow. Past customers go cold because nobody stays in touch.
5. **Team operations** (team_operations) — Hiring is ad hoc, onboarding is trial by fire, performance issues surface late. No structured way to train, track, or give feedback.

## Disqualification Criteria

Flag these if detected in the conversation:

**Hard disqualifiers (automatic no):**
- Not speaking to the owner or decision-maker (proxy decisions)
- Scope clearly exceeds a single engagement phase (multi-location rollout, ERP migration, franchise system). Follow-on phases are fine.
- No tech baseline at all (no email, no internet, no existing tools)
- Business in crisis mode (active layoffs, pending closure)

**Soft disqualifiers (yellow flags):**
- No internal champion identified who would own the solution post-delivery
- Books more than 90 days behind
- No willingness to change (diagnosis with no intent to act)
- Revenue below 500K (signals may not support engagement investment)
- More than 3 decision-makers involved in the buying process

## Revenue and Qualification Signals

Look for revenue signals rather than just employee count. Indicators include:
- Office or shop size, fleet vehicles, multiple locations
- Team size and payroll (2+ employees, not all contractors)
- 3+ years in business
- Mentions of revenue ranges, annual volume, or contract sizes
- Industry-specific signals (truck count, chair count, patient volume, etc.)

Classify into a revenue range: under_500k, 500k_1m, 1m_3m, 3m_5m, 5m_10m, over_10m, or unknown.

## Champion Identification

Look for mentions of a team member who could own the solution after we leave. The enablement standard is: can they explain why it was built this way, operate it without us, and handle common failure modes?

## Output Rules

- Output ONLY valid JSON matching the schema provided. No markdown, no commentary, no code fences.
- Use direct quotes from the owner where possible (mark with quotation marks in the owner_quotes arrays).
- If information is not available from the transcript, use null for nullable fields and empty arrays for array fields.
- Severity should reflect how much operational pain this problem causes, not just whether the owner mentioned it.
- Order identified_problems by severity (highest first).
- recommended_problems in quote_drivers should be the 2-3 most impactful problems to address, which may differ from severity ordering if one problem is easier to fix with high ROI.`

/**
 * Builds the user prompt with the transcript inserted.
 *
 * @param transcript - The full MacWhisper speaker-separated transcript text
 * @returns The complete user prompt to send to Claude
 */
export function buildExtractionUserPrompt(transcript: string): string {
  return `Analyze the following assessment call transcript and extract structured data.

## Output Schema

Produce a single JSON object with these fields:

\`\`\`
{
  "schema_version": "2.0",
  "extracted_at": "<ISO 8601 timestamp>",

  "business_name": "<string>",
  "vertical": "<home_services | professional_services | contractor_trades | retail_salon | restaurant_food | healthcare | technology | manufacturing | other>",
  "business_type": "<specific type, e.g. 'residential HVAC', 'family law', 'dental practice'>",
  "years_in_business": <number or null>,
  "employee_count": <number or null>,
  "revenue_range": "<under_500k | 500k_1m | 1m_3m | 3m_5m | 5m_10m | over_10m | unknown>",
  "geography": "<city or metro area, or null>",

  "current_tools": [
    {
      "name": "<tool name>",
      "purpose": "<what they use it for>",
      "status": "<working | underutilized | failing>"
    }
  ],

  "identified_problems": [
    {
      "problem_id": "<process_design | tool_systems | data_visibility | customer_pipeline | team_operations>",
      "severity": "<high | medium | low>",
      "summary": "<one sentence>",
      "owner_quotes": ["<direct quote from transcript>"],
      "underlying_cause": "<what is actually broken>"
    }
  ],

  "complexity_signals": {
    "employee_count": <number or null>,
    "location_count": <number>,
    "tool_migrations": ["<description of needed migration>"],
    "data_volume_notes": ["<relevant data volume signal>"],
    "integration_needs": ["<integration requirement>"],
    "additional_factors": ["<other complexity factor>"]
  },

  "champion_candidate": {
    "name": "<string or null>",
    "role": "<string or null>",
    "evidence": "<why this person could be the champion>",
    "confidence": "<strong | moderate | weak>"
  } or null,

  "call_participants": ["<name, role>"],

  "disqualification_flags": {
    "hard": {
      "not_decision_maker": <boolean>,
      "scope_exceeds_phase": <boolean>,
      "no_tech_baseline": <boolean>,
      "in_crisis": <boolean>
    },
    "soft": {
      "no_champion": <boolean>,
      "books_behind": <boolean>,
      "no_willingness_to_change": <boolean>,
      "revenue_too_low": <boolean>,
      "too_many_decision_makers": <boolean>
    },
    "notes": "<explanation of any flags triggered>"
  },

  "budget_signals": {
    "employees_on_payroll": <boolean or null>,
    "years_in_business_3_plus": <boolean or null>,
    "in_crisis": <boolean or null>,
    "revenue_signals": ["<observed revenue indicator, e.g. '3 service trucks', '12 chairs'>"],
    "notes": "<relevant observations>"
  },

  "quote_drivers": {
    "recommended_problems": ["<problem_id>", "<problem_id>"],
    "estimated_complexity": "<low | medium | high>",
    "upward_pressures": ["<factor increasing hours>"],
    "downward_pressures": ["<factor decreasing hours>"],
    "roi_anchors": ["<ROI math the owner verbalized>"]
  },

  "executive_summary": "<one paragraph summarizing the call and key findings>",
  "additional_notes": "<anything notable not captured above>"
}
\`\`\`

## Transcript

${transcript}`
}

/**
 * Builds the complete prompt for manual use (Phase 1).
 *
 * Returns a single string that the admin can paste into Claude's chat interface
 * along with the transcript. The system context is embedded in the prompt since
 * the chat interface does not support separate system messages.
 *
 * @param transcript - The full MacWhisper speaker-separated transcript text
 * @returns The complete prompt string for manual use
 */
export function buildManualExtractionPrompt(transcript: string): string {
  return `${EXTRACTION_SYSTEM_PROMPT}

---

${buildExtractionUserPrompt(transcript)}`
}

/**
 * Validates that a parsed JSON object conforms to the AssessmentExtraction schema.
 *
 * This is a runtime validation function for use when parsing Claude's output.
 * It checks structural correctness and value constraints without a full
 * JSON Schema library dependency.
 *
 * @param data - The parsed JSON to validate
 * @returns An object with `valid` boolean and `errors` array of issues found
 */
export function validateExtraction(data: unknown): {
  valid: boolean
  errors: string[]
} {
  const errors: string[] = []

  if (typeof data !== 'object' || data === null) {
    return { valid: false, errors: ['Root must be a non-null object'] }
  }

  const d = data as Record<string, unknown>

  // Schema version
  if (d.schema_version !== '2.0') {
    errors.push(`schema_version must be "2.0", got "${String(d.schema_version)}"`)
  }

  // Required strings
  const requiredStrings = [
    'extracted_at',
    'business_name',
    'vertical',
    'business_type',
    'executive_summary',
  ]
  for (const field of requiredStrings) {
    if (typeof d[field] !== 'string' || (d[field] as string).length === 0) {
      errors.push(`${field} must be a non-empty string`)
    }
  }

  // Vertical enum
  const validVerticals: readonly string[] = VERTICALS
  if (typeof d.vertical === 'string' && !validVerticals.includes(d.vertical)) {
    errors.push(`vertical must be one of: ${validVerticals.join(', ')}`)
  }

  // Revenue range
  const validRevenueRanges: readonly string[] = REVENUE_RANGES
  if (typeof d.revenue_range === 'string' && !validRevenueRanges.includes(d.revenue_range)) {
    errors.push(`revenue_range must be one of: ${validRevenueRanges.join(', ')}`)
  }

  // Identified problems
  if (!Array.isArray(d.identified_problems)) {
    errors.push('identified_problems must be an array')
  } else {
    const validProblemIds: readonly string[] = PROBLEM_IDS
    for (let i = 0; i < d.identified_problems.length; i++) {
      const p = d.identified_problems[i] as Record<string, unknown>
      if (!p || typeof p !== 'object') {
        errors.push(`identified_problems[${i}] must be an object`)
        continue
      }
      if (typeof p.problem_id !== 'string' || !validProblemIds.includes(p.problem_id)) {
        errors.push(
          `identified_problems[${i}].problem_id must be one of: ${validProblemIds.join(', ')}`
        )
      }
      if (!['high', 'medium', 'low'].includes(p.severity as string)) {
        errors.push(`identified_problems[${i}].severity must be high, medium, or low`)
      }
      if (typeof p.summary !== 'string' || (p.summary as string).length === 0) {
        errors.push(`identified_problems[${i}].summary must be a non-empty string`)
      }
      if (!Array.isArray(p.owner_quotes) || p.owner_quotes.length === 0) {
        errors.push(`identified_problems[${i}].owner_quotes must have at least 1 quote`)
      }
      if (typeof p.underlying_cause !== 'string' || (p.underlying_cause as string).length === 0) {
        errors.push(`identified_problems[${i}].underlying_cause must be a non-empty string`)
      }
    }
    if (d.identified_problems.length < 1) {
      errors.push('identified_problems must contain at least 1 problem')
    }
    if (d.identified_problems.length > 5) {
      errors.push('identified_problems should contain at most 5 problems')
    }
  }

  // Current tools
  if (!Array.isArray(d.current_tools)) {
    errors.push('current_tools must be an array')
  } else {
    for (let i = 0; i < d.current_tools.length; i++) {
      const t = d.current_tools[i] as Record<string, unknown>
      if (!t || typeof t !== 'object') {
        errors.push(`current_tools[${i}] must be an object`)
        continue
      }
      if (typeof t.name !== 'string') errors.push(`current_tools[${i}].name must be a string`)
      if (typeof t.purpose !== 'string') errors.push(`current_tools[${i}].purpose must be a string`)
      if (!['working', 'underutilized', 'failing'].includes(t.status as string)) {
        errors.push(`current_tools[${i}].status must be working, underutilized, or failing`)
      }
    }
  }

  // Complexity signals
  if (typeof d.complexity_signals !== 'object' || d.complexity_signals === null) {
    errors.push('complexity_signals must be an object')
  } else {
    const cs = d.complexity_signals as Record<string, unknown>
    if (typeof cs.location_count !== 'number') {
      errors.push('complexity_signals.location_count must be a number')
    }
    for (const arrField of [
      'tool_migrations',
      'data_volume_notes',
      'integration_needs',
      'additional_factors',
    ]) {
      if (!Array.isArray(cs[arrField])) {
        errors.push(`complexity_signals.${arrField} must be an array`)
      }
    }
  }

  // Disqualification flags
  if (typeof d.disqualification_flags !== 'object' || d.disqualification_flags === null) {
    errors.push('disqualification_flags must be an object')
  } else {
    const df = d.disqualification_flags as Record<string, unknown>
    if (typeof df.hard !== 'object' || df.hard === null) {
      errors.push('disqualification_flags.hard must be an object')
    } else {
      const hard = df.hard as Record<string, unknown>
      for (const field of [
        'not_decision_maker',
        'scope_exceeds_phase',
        'no_tech_baseline',
        'in_crisis',
      ]) {
        if (typeof hard[field] !== 'boolean') {
          errors.push(`disqualification_flags.hard.${field} must be a boolean`)
        }
      }
    }
    if (typeof df.soft !== 'object' || df.soft === null) {
      errors.push('disqualification_flags.soft must be an object')
    } else {
      const soft = df.soft as Record<string, unknown>
      for (const field of [
        'no_champion',
        'books_behind',
        'no_willingness_to_change',
        'revenue_too_low',
        'too_many_decision_makers',
      ]) {
        if (typeof soft[field] !== 'boolean') {
          errors.push(`disqualification_flags.soft.${field} must be a boolean`)
        }
      }
    }
  }

  // Budget signals
  if (typeof d.budget_signals !== 'object' || d.budget_signals === null) {
    errors.push('budget_signals must be an object')
  } else {
    const bs = d.budget_signals as Record<string, unknown>
    if (!Array.isArray(bs.revenue_signals)) {
      errors.push('budget_signals.revenue_signals must be an array')
    }
  }

  // Quote drivers
  if (typeof d.quote_drivers !== 'object' || d.quote_drivers === null) {
    errors.push('quote_drivers must be an object')
  } else {
    const qd = d.quote_drivers as Record<string, unknown>
    if (!Array.isArray(qd.recommended_problems)) {
      errors.push('quote_drivers.recommended_problems must be an array')
    }
    if (!['low', 'medium', 'high'].includes(qd.estimated_complexity as string)) {
      errors.push('quote_drivers.estimated_complexity must be low, medium, or high')
    }
  }

  // Call participants
  if (!Array.isArray(d.call_participants)) {
    errors.push('call_participants must be an array')
  }

  return { valid: errors.length === 0, errors }
}
