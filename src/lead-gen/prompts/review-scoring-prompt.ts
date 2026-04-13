/**
 * Review Scoring Prompt — Pipeline 1
 *
 * Analyzes Google/Yelp reviews for Phoenix-based businesses to detect
 * operational pain signals. Distinguishes operational problems (scheduling
 * chaos, never called back) from service quality complaints (rude staff,
 * bad haircut). Only operational signals matter for lead qualification.
 *
 * Supports both single-business and batch (5-10 businesses) scoring
 * to optimize operations budget.
 *
 * Used in: CF Worker → Anthropic API → this prompt
 * Input: Business data + recent reviews from Outscraper
 * Output: ReviewScoring or BatchReviewScoring JSON (see review-signal.ts)
 *
 * @see Decision #20 — Voice Standard ("we" voice)
 */

import type {
  BusinessReviewInput,
  ReviewScoring,
  BatchReviewScoring,
} from '../schemas/review-signal.js'
import { PROBLEM_IDS } from '../schemas/lead-scoring-schema.js'

export type { ReviewScoring, BatchReviewScoring, BusinessReviewInput }

/**
 * System prompt for review-based operational pain scoring.
 */
export const REVIEW_SCORING_SYSTEM_PROMPT = `You are a review analysis assistant for SMD Services, an operations consulting team that works with Phoenix-based small and mid-size businesses ($750k–$5M revenue).

Your job is to analyze Google and Yelp reviews and score businesses for OPERATIONAL pain — problems with how the business runs, not the quality of the service or product itself. This distinction is critical.

## Operational vs. Service Quality — The Key Distinction

**OPERATIONAL problems (what we care about):**
- "Called three times, nobody ever called back" → customer_pipeline
- "They double-booked us and forgot our appointment" → process_design
- "The owner had to come out personally because nobody else could help" → process_design
- "Got a bill 3 months later with no explanation" → data_visibility
- "Their software doesn't work, kept losing my info" → tool_systems
- "Different tech every time, none of them knew what the last one did" → team_operations

**SERVICE QUALITY complaints (NOT what we care about — ignore these):**
- "The plumber was rude" — personality, not operations
- "Overpriced for the work done" — pricing perception, not operations
- "The food was cold" — execution quality, not operations
- "Took too long to finish the job" — speed of service delivery, not operations
- "Didn't clean up after themselves" — professionalism, not operations
- "Work quality was poor, had to redo it" — skill/competence, not operations

**Gray area (score only if the root cause is operational):**
- "Waited 2 hours past my appointment" — could be process_design (operational) or just running behind (service)
- "They lost my paperwork" — team_operations (operational) if systemic, one-off if isolated
- "Nobody knew the status of my order" — team_operations (operational) if pattern

## 5 Solution Capability Areas

1. **process_design** — No documented processes, everything runs through the owner. Signals: "only the owner could help," "had to wait for the boss," "nobody knew the process," "showed up on the wrong day," "double-booked."
2. **tool_systems** — Software doesn't work or doesn't exist. Signals: "their software doesn't work," "kept losing my info," "had to call because their system was down," "no online booking," "website broken."
3. **data_visibility** — Billing chaos, no financial clarity. Signals: "surprise charges," "couldn't give me a quote," "billing errors," "months late on invoice," "no receipt."
4. **customer_pipeline** — Leads and follow-ups fall through cracks. Signals: "never called back," "ghosted after estimate," "had to chase them," "no follow-up," "left a message, no response."
5. **team_operations** — No accountability or consistency across team members. Signals: "different person every time," "new guy didn't know what to do," "nobody knew the status," "left hand doesn't know what the right is doing."

## Scoring Calibration

- **1-3:** No meaningful operational signals. Complaints are about service quality, pricing, or one-off issues. Not a prospect.
- **4-6:** Isolated operational signals, not patterns. A single "never called me back" in 20 positive reviews is noise, not signal. Worth monitoring but not outreach-ready.
- **7-8:** Clear operational pattern across multiple reviews. At least 2-3 reviews independently mentioning the same type of operational failure. This business has a real problem. Worth outreach.
- **9-10:** Severe, repeated operational failures documented by many customers. Multiple problem types present. The reviews are practically writing the assessment report for us.

**Key calibration rule:** A single complaint is noise. Repeated patterns are signal. Two different customers independently saying "they never called me back" is a pattern. One customer saying it once is an anecdote. Score based on PATTERNS, not individual reviews.

## Output Rules

- Output ONLY valid JSON matching the schema. No markdown, no code fences, no commentary.
- Only include reviews in the signals array that contain genuine operational signals. Do not include service quality complaints.
- The outreach_angle must use "we" voice (never "I"). Reference the specific operational pattern found. Never mention pricing or timeframes.
- Quote reviews exactly — do not paraphrase or clean up language.
- If a business has zero operational signals, still return a result with pain_score 1-2, empty signals array, and empty outreach_angle.

## Example

Input business: "Reliable Rooter Plumbing"
Input reviews:
- ★★★★★ "Great work, fixed our leak same day. Highly recommend."
- ★★★☆☆ "Good plumber but I had to call three times before anyone picked up. Left two voicemails that were never returned. Finally got through on the third try."
- ★★☆☆☆ "They missed our appointment entirely. No call, no text, just didn't show up. When I called they said they had us down for next week."
- ★★★★☆ "Quality work. A bit pricey but they got the job done."
- ★★☆☆☆ "Tried to schedule a repair for 3 weeks. Called multiple times, got bounced around. Finally gave up and called someone else."
- ★★★★★ "Owner came out personally to handle our issue since his techs were all booked. Appreciated the personal touch."

Output:
{"business_name":"Reliable Rooter Plumbing","place_id":"ChIJ_example123","pain_score":8,"top_problems":["customer_pipeline","process_design"],"signals":[{"problem_id":"customer_pipeline","quote":"I had to call three times before anyone picked up. Left two voicemails that were never returned.","review_rating":3,"severity":8},{"problem_id":"process_design","quote":"They missed our appointment entirely. No call, no text, just didn't show up.","review_rating":2,"severity":9},{"problem_id":"customer_pipeline","quote":"Tried to schedule a repair for 3 weeks. Called multiple times, got bounced around. Finally gave up and called someone else.","review_rating":2,"severity":9},{"problem_id":"process_design","quote":"Owner came out personally to handle our issue since his techs were all booked.","review_rating":5,"severity":5}],"outreach_angle":"Your team clearly does quality work — the 5-star reviews say that. But we noticed a pattern: customers are having trouble reaching you, and appointments are slipping through the cracks. We help businesses like yours fix exactly that — so the phone gets answered and every appointment stays on the books."}`

/**
 * Builds the user prompt for scoring a single business's reviews.
 *
 * @param business - Business data with reviews from Outscraper
 * @returns The user prompt for Claude
 */
export function buildReviewScoringUserPrompt(business: BusinessReviewInput): string {
  const reviewLines = business.reviews
    .map((r) => `- ★${'★'.repeat(r.rating - 1)}${'☆'.repeat(5 - r.rating)} (${r.date}) "${r.text}"`)
    .join('\n')

  return `Analyze the following reviews for operational pain signals.

Business: ${business.business_name}
Place ID: ${business.place_id}
Category: ${business.category}
Area: ${business.area}
Overall Rating: ${business.overall_rating}/5
Total Reviews: ${business.total_review_count}

Recent Reviews (${business.reviews.length}):
${reviewLines}

Produce a single JSON object matching the ReviewScoring schema.`
}

/**
 * Builds the user prompt for batch scoring multiple businesses.
 * More efficient — scores 5-10 businesses per Claude API call,
 * saving Make.com operations (1 Anthropic module call vs. 5-10).
 *
 * @param businesses - Array of businesses with their reviews
 * @returns The user prompt for Claude
 */
export function buildBatchReviewScoringUserPrompt(businesses: BusinessReviewInput[]): string {
  const businessBlocks = businesses.map((b, i) => {
    const reviewLines = b.reviews
      .map(
        (r) => `  - ★${'★'.repeat(r.rating - 1)}${'☆'.repeat(5 - r.rating)} (${r.date}) "${r.text}"`
      )
      .join('\n')

    return `### Business ${i + 1}: ${b.business_name}
Place ID: ${b.place_id}
Category: ${b.category}
Area: ${b.area}
Overall Rating: ${b.overall_rating}/5
Total Reviews: ${b.total_review_count}

Recent Reviews (${b.reviews.length}):
${reviewLines}`
  })

  return `Analyze the following ${businesses.length} businesses for operational pain signals. Score each independently.

${businessBlocks.join('\n\n---\n\n')}

Produce a JSON object with this structure:
{
  "businesses": [<ReviewScoring for each business, in order>],
  "total_reviews_analyzed": <total across all businesses>
}`
}

/**
 * Builds the complete prompt for manual testing in Claude's chat interface.
 *
 * @param business - Business data with reviews
 * @returns The complete prompt string for manual use
 */
export function buildManualReviewScoringPrompt(business: BusinessReviewInput): string {
  return `${REVIEW_SCORING_SYSTEM_PROMPT}

---

${buildReviewScoringUserPrompt(business)}`
}

/**
 * Validates that a parsed JSON object conforms to the ReviewScoring schema.
 *
 * @param data - The parsed JSON to validate
 * @returns An object with `valid` boolean and `errors` array of issues found
 */
export function validateReviewScoring(data: unknown): {
  valid: boolean
  errors: string[]
} {
  const errors: string[] = []

  if (typeof data !== 'object' || data === null) {
    return { valid: false, errors: ['Root must be a non-null object'] }
  }

  const d = data as Record<string, unknown>

  // Required fields
  if (typeof d.business_name !== 'string' || d.business_name.length === 0) {
    errors.push('business_name must be a non-empty string')
  }

  if (typeof d.place_id !== 'string' || d.place_id.length === 0) {
    errors.push('place_id must be a non-empty string')
  }

  // Pain score range
  if (typeof d.pain_score !== 'number' || d.pain_score < 1 || d.pain_score > 10) {
    errors.push('pain_score must be a number between 1 and 10')
  }

  // Top problems
  const validProblemIds: readonly string[] = PROBLEM_IDS
  if (!Array.isArray(d.top_problems)) {
    errors.push('top_problems must be an array')
  } else {
    for (const p of d.top_problems) {
      if (!validProblemIds.includes(p as string)) {
        errors.push(`Invalid problem ID in top_problems: "${String(p)}"`)
      }
    }
    if (d.top_problems.length > 3) {
      errors.push('top_problems should have at most 3 entries')
    }
  }

  // Signals array
  if (!Array.isArray(d.signals)) {
    errors.push('signals must be an array')
  } else {
    for (let i = 0; i < (d.signals as unknown[]).length; i++) {
      const s = (d.signals as Record<string, unknown>[])[i]
      if (!s || typeof s !== 'object') {
        errors.push(`signals[${i}] must be an object`)
        continue
      }
      if (!validProblemIds.includes(s.problem_id as string)) {
        errors.push(`signals[${i}].problem_id must be a valid problem ID`)
      }
      if (typeof s.quote !== 'string' || s.quote.length === 0) {
        errors.push(`signals[${i}].quote must be a non-empty string`)
      }
      if (typeof s.review_rating !== 'number' || s.review_rating < 1 || s.review_rating > 5) {
        errors.push(`signals[${i}].review_rating must be 1-5`)
      }
      if (typeof s.severity !== 'number' || s.severity < 1 || s.severity > 10) {
        errors.push(`signals[${i}].severity must be 1-10`)
      }
    }
  }

  // Outreach angle voice check
  if (typeof d.outreach_angle === 'string' && d.outreach_angle.length > 0) {
    const angle = d.outreach_angle.toLowerCase()
    if (angle.includes(' i ') || angle.startsWith('i ') || angle.includes(" i'")) {
      errors.push('outreach_angle must use "we" voice, not "I"')
    }
  }

  return { valid: errors.length === 0, errors }
}

/**
 * Validates a batch scoring result.
 *
 * @param data - The parsed JSON to validate
 * @returns An object with `valid` boolean and `errors` array of issues found
 */
export function validateBatchReviewScoring(data: unknown): {
  valid: boolean
  errors: string[]
} {
  const errors: string[] = []

  if (typeof data !== 'object' || data === null) {
    return { valid: false, errors: ['Root must be a non-null object'] }
  }

  const d = data as Record<string, unknown>

  if (!Array.isArray(d.businesses)) {
    errors.push('businesses must be an array')
  } else {
    for (let i = 0; i < d.businesses.length; i++) {
      const result = validateReviewScoring(d.businesses[i])
      if (!result.valid) {
        errors.push(...result.errors.map((e) => `businesses[${i}]: ${e}`))
      }
    }
  }

  if (typeof d.total_reviews_analyzed !== 'number') {
    errors.push('total_reviews_analyzed must be a number')
  }

  return { valid: errors.length === 0, errors }
}
