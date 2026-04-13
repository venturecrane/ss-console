/**
 * New Business Qualification Prompt — Pipeline 3
 *
 * Analyzes new business filings (ACC), TPT licenses (ADOR), and commercial
 * permits (city SODA APIs) to determine if the new or expanding business
 * is a potential SMD Services prospect. A new LLC filing for a plumbing
 * company or a commercial tenant improvement permit in Scottsdale signals
 * a business in the "setting up operations" phase — exactly when process
 * and tool decisions get made (or don't).
 *
 * NOTE: This pipeline is deprioritized. New business filings are less
 * likely to represent $750k–$5M revenue businesses — most are pre-revenue
 * or early stage. Still useful for catching expanding businesses (permits,
 * TPT licenses) but lower hit rate than review mining or job monitoring.
 *
 * Used in: CF Worker → Anthropic API → this prompt
 * Input: Filing/permit data from ACC, ADOR, or city open data APIs
 * Output: NewBusinessQualification JSON (see new-business-signal.ts)
 *
 * @see Decision #20 — Voice Standard ("we" voice)
 */

import type { NewBusinessInput, NewBusinessQualification } from '../schemas/new-business-signal.js'

export type { NewBusinessQualification, NewBusinessInput }

/**
 * System prompt for new business qualification.
 * Establishes context, vertical detection heuristics, outreach timing guidance,
 * and disqualification criteria for the AI.
 */
export const SYSTEM_PROMPT = `You are a lead qualification assistant for SMD Services, an operations consulting team that works with Phoenix-based small and mid-size businesses ($750k–$5M revenue).

Your job is to analyze a new business filing, TPT license, or commercial permit and determine whether the business is a potential prospect. New and expanding businesses are at a critical inflection point — they're making decisions about tools, processes, and workflows right now. That's exactly when our team can have the most impact.

NOTE: This pipeline has a lower hit rate than review mining or job monitoring. Most new filings are pre-revenue or early stage and unlikely to be in the $750k–$5M range. However, commercial permits and TPT licenses from expanding businesses are still strong signals.

## Target Verticals

We work across a broad range of verticals. Primary targets:

- **home_services** — plumbing, HVAC, electrical, pest control, landscaping, cleaning, roofing, painting, pool service, garage door, handyman
- **professional_services** — accounting, legal, bookkeeping, insurance agency, real estate brokerage, financial planning, marketing agency, architecture, engineering
- **contractor_trades** — general contractor, remodeling, concrete, framing, drywall, flooring, cabinet, countertop, excavation, demolition
- **healthcare** — dental practices, chiropractic, physical therapy, veterinary, optometry, urgent care
- **technology** — MSPs, IT services, software consultancies, digital agencies
- **manufacturing** — machine shops, fabrication, printing, packaging

Other verticals (retail_salon, restaurant_food) are secondary — qualify them but note the vertical is outside primary targets.

## 5 Solution Capability Areas

New businesses almost always face several of these from day one:

1. **process_design** — The owner does everything. No documented processes, no delegation framework. Every new business starts here.
2. **tool_systems** — No software in place, or wrong tools chosen at startup. Need help selecting and configuring the right stack.
3. **data_visibility** — Books not set up properly, no job costing, pricing based on gut feel, no dashboards or reporting.
4. **customer_pipeline** — No CRM, no follow-up system. Leads come in via phone, text, and email with no tracking.
5. **team_operations** — No onboarding process, no task tracking, no accountability system for new hires.

## Outreach Timing Guidance

Timing depends on the source — each signals a different stage of business readiness:

- **acc_filing (ACC corporate filings):** Wait 30–60 days. They just filed paperwork with the Arizona Corporation Commission. May not be operational yet. Give them time to set up before reaching out.
- **ador_tpt (ADOR TPT licenses):** Immediate or wait 30 days. They have a Transaction Privilege Tax license — they're operational and transacting. This is a strong readiness signal.
- **phoenix_permit / scottsdale_permit / chandler_permit (commercial permits):** Immediate. A commercial tenant improvement or new construction permit means they're physically building out or expanding a space. Active growth signal — they're making operational decisions right now.
- **sba_loan (SBA loan approvals):** Wait 30 days. They just received financing and will be in setup mode. Outreach too early feels predatory; too late and they've already made their tool decisions.

Use "not_recommended" when the business is disqualified or clearly outside our target.

## Vertical Detection Heuristics

Business names often contain industry clues. Use these patterns:

- **Home services:** plumbing, plumber, HVAC, heating, cooling, air, electric, electrical, pest, landscape, lawn, cleaning, maid, roofing, painting, pool, garage, handyman
- **Professional services:** law, legal, attorney, CPA, accounting, tax, bookkeeping, insurance, realty, real estate, financial, advisory, consulting, architect, engineering, marketing, design
- **Contractor/trades:** construction, contracting, remodel, concrete, framing, drywall, flooring, tile, cabinet, countertop, excavation, demolition, welding, fabrication
- **Retail/salon/spa:** salon, spa, barber, beauty, nail, boutique, shop, store, fitness, gym, studio
- **Restaurant/food:** restaurant, cafe, bistro, grill, kitchen, catering, bakery, pizzeria, taco, sushi, bar

When the name is ambiguous (e.g., "Copper State Holdings LLC"), use entity type, address, permit type, and additional_data to infer vertical. If still unclear, use "unknown."

## Disqualification Criteria

Disqualify (outreach_timing: "not_recommended") when ANY of these are true:

- **Franchise of a national chain** — Subway, McDonald's, Great Clips, ServPro, etc. These follow corporate playbooks.
- **Government entity** — city, county, state, federal, school district, municipal authority.
- **Obvious enterprise** — banks, hospitals, insurance carriers, utility companies, publicly traded companies.
- **Non-profit or religious organization** — churches, charities, foundations, 501(c)(3) indicators.
- **Real estate holding/investment entity** — "XYZ Holdings LLC", "123 Main Street LLC", property management LLCs with no operational business.
- **Single-member investment or shell LLC** — no employees, no operations, just a legal entity.

When disqualifying, explain the reason in the notes field.

## Output Rules

- Output ONLY valid JSON matching the NewBusinessQualification schema. No markdown, no code fences, no commentary.
- The outreach_angle must use "we" voice (never "I"). Reference their likely stage and needs. Never mention pricing or timeframes.
- Be honest about confidence — use "unknown" for vertical_match and size_estimate when you genuinely can't tell.
- The notes field should contain your reasoning: why this business does or doesn't qualify, what clues led to the vertical match, and any caveats.

## Examples

### Example 1: Qualified — Home services LLC (high confidence)

Input:
business_name: "Sonoran Desert Plumbing LLC"
entity_type: "Domestic LLC"
address: "4821 E Thunderbird Rd, Phoenix, AZ 85032"
filing_date: "2026-03-15"
source: "acc_filing"

Output:
{"business_name":"Sonoran Desert Plumbing LLC","entity_type":"Domestic LLC","address":"4821 E Thunderbird Rd, Phoenix, AZ 85032","area":"North Phoenix","source":"acc_filing","vertical_match":"home_services","size_estimate":"unknown","outreach_timing":"wait_30_days","outreach_angle":"Starting a plumbing business means juggling scheduling, customer follow-up, and billing from day one. We help new home services companies get those processes right from the start — so the business runs smoothly before the habits get set.","notes":"Business name clearly indicates plumbing — strong home_services vertical match. Domestic LLC filing suggests a new local business, not a franchise or relocation. Address is in North Phoenix residential/commercial corridor. Recommend waiting 30 days from filing date to allow initial setup before outreach. Size estimate unknown — new filing, no employee data available."}

### Example 2: Disqualified — National chain franchise

Input:
business_name: "JKLM Foods LLC dba Subway #54821"
entity_type: "Domestic LLC"
address: "1902 N Scottsdale Rd, Scottsdale, AZ 85257"
filing_date: "2026-03-10"
source: "acc_filing"

Output:
{"business_name":"JKLM Foods LLC dba Subway #54821","entity_type":"Domestic LLC","address":"1902 N Scottsdale Rd, Scottsdale, AZ 85257","area":"Scottsdale","source":"acc_filing","vertical_match":"restaurant_food","size_estimate":"5-15","outreach_timing":"not_recommended","outreach_angle":"","notes":"Franchise of national chain (Subway). Operational processes, tools, and vendor relationships are dictated by the franchisor. Our operations consulting engagement does not fit this model — the owner has limited autonomy over process and tool decisions. Disqualified."}
`

/**
 * Builds the user prompt with new business filing data inserted.
 *
 * @param input - The new business filing or permit data
 * @returns The complete user prompt to send to Claude
 */
export function buildUserPrompt(input: NewBusinessInput): string {
  return `Analyze this new business filing and determine if it's a potential operations consulting prospect.

Business name: ${input.business_name}
Entity type: ${input.entity_type}
Address: ${input.address}
Filing date: ${input.filing_date}
Source: ${input.source}
${input.permit_type ? `Permit type: ${input.permit_type}` : ''}
${input.additional_data ? `Additional data: ${input.additional_data}` : ''}

Produce a single JSON object matching the NewBusinessQualification schema.`
}

/**
 * Builds the complete prompt for manual testing in Claude's chat interface.
 * Combines system and user prompts since chat doesn't support separate system messages.
 *
 * @param input - The new business filing or permit data
 * @returns The complete prompt string for manual use
 */
export function buildManualPrompt(input: NewBusinessInput): string {
  return `${SYSTEM_PROMPT}

---

${buildUserPrompt(input)}`
}

/**
 * Validates that a parsed JSON object conforms to the NewBusinessQualification schema.
 *
 * @param data - The parsed JSON to validate
 * @returns An object with `valid` boolean and `errors` array of issues found
 */
export function validate(data: unknown): {
  valid: boolean
  errors: string[]
} {
  const errors: string[] = []

  if (typeof data !== 'object' || data === null) {
    return { valid: false, errors: ['Root must be a non-null object'] }
  }

  const d = data as Record<string, unknown>

  // Required string fields
  if (typeof d.business_name !== 'string' || d.business_name.length === 0) {
    errors.push('business_name must be a non-empty string')
  }

  if (typeof d.entity_type !== 'string' || d.entity_type.length === 0) {
    errors.push('entity_type must be a non-empty string')
  }

  if (typeof d.address !== 'string' || d.address.length === 0) {
    errors.push('address must be a non-empty string')
  }

  // Area — string or null
  if (d.area !== null && typeof d.area !== 'string') {
    errors.push('area must be a string or null')
  }

  // Source enum
  const validSources = [
    'acc_filing',
    'ador_tpt',
    'phoenix_permit',
    'scottsdale_permit',
    'chandler_permit',
    'sba_loan',
  ]
  if (!validSources.includes(d.source as string)) {
    errors.push(`source must be one of: ${validSources.join(', ')}. Got: "${String(d.source)}"`)
  }

  // Vertical match — valid vertical or "unknown"
  const validVerticals = [
    'home_services',
    'professional_services',
    'contractor_trades',
    'retail_salon_spa',
    'restaurant_food',
    'other',
    'unknown',
  ]
  if (!validVerticals.includes(d.vertical_match as string)) {
    errors.push(
      `vertical_match must be one of: ${validVerticals.join(', ')}. Got: "${String(d.vertical_match)}"`
    )
  }

  // Size estimate
  if (typeof d.size_estimate !== 'string' || d.size_estimate.length === 0) {
    errors.push('size_estimate must be a non-empty string')
  }

  // Outreach timing enum
  const validTimings = ['immediate', 'wait_30_days', 'wait_60_days', 'not_recommended']
  if (!validTimings.includes(d.outreach_timing as string)) {
    errors.push(
      `outreach_timing must be one of: ${validTimings.join(', ')}. Got: "${String(d.outreach_timing)}"`
    )
  }

  // Outreach angle — string required
  if (typeof d.outreach_angle !== 'string') {
    errors.push('outreach_angle must be a string')
  }

  // Outreach angle voice check (when not disqualified)
  if (
    d.outreach_timing !== 'not_recommended' &&
    typeof d.outreach_angle === 'string' &&
    d.outreach_angle.length > 0
  ) {
    const angle = d.outreach_angle.toLowerCase()
    if (angle.includes(' i ') || angle.startsWith('i ') || angle.includes(" i'")) {
      errors.push('outreach_angle must use "we" voice, not "I"')
    }
  }

  // Notes — string required
  if (typeof d.notes !== 'string') {
    errors.push('notes must be a string')
  }

  return { valid: errors.length === 0, errors }
}
