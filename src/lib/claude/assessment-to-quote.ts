/**
 * Assessment-to-quote line item generation via Claude API.
 *
 * Takes an assessment extraction (problems, notes, duration) plus assembled
 * entity context and generates realistic quote line items with hour estimates.
 *
 * Business rules:
 * - Target 3-6 line items per assessment
 * - Hours should be appropriate for business size (from entity context)
 * - Each line item maps to a specific problem area identified in the assessment
 * - Returns empty array on any failure (caller handles gracefully)
 */

import type { LineItem } from '../db/quotes.js'

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const MODEL = 'claude-sonnet-4-20250514'
const MAX_TOKENS = 2048

export interface AssessmentExtraction {
  problems: string[]
  other_problem?: string
  disqualified: boolean
  disqualify_reason?: string
  duration_minutes: number
  notes: string
}

const SYSTEM_PROMPT = `You generate quote line items for SMD Services operations cleanup engagements. Each line item represents a discrete deliverable for a Phoenix-based growing business (750K to 5M annual revenue).

## What you produce

A JSON array of line items. Each line item has:
- "problem": The solution area being addressed (e.g., "Process Design", "Customer Pipeline")
- "description": A specific, actionable deliverable description. What we will actually do. Not vague consulting-speak.
- "estimated_hours": Realistic hours for that deliverable, given the business size and complexity.

## Guidelines

- Generate 3-6 line items based on the problems identified in the assessment.
- Each problem checked should map to at least one line item. Related problems can share a line item.
- Descriptions should be concrete: "Document 5 core workflows and create SOPs" not "Improve processes."
- Hour estimates should reflect a real engagement. Engagement complexity tiers:
  - Low: 20-30 total hours
  - Medium: 30-45 total hours
  - High: 45-60+ total hours
- Most individual line items are 4-16 hours.
- Consider the business vertical, team size, revenue range, and tools already in use when sizing.
- If "Other" problems are noted, create line items that address them specifically.
- Internal rate is 175 per hour. Do not include pricing in the output.

## Solution area mapping

- process_design: Process Design — workflow documentation, delegation frameworks, decision trees, SOP creation
- tool_systems: Tools & Systems — tool selection, configuration, migration, integration, workflow automation
- data_visibility: Data & Visibility — bookkeeping cleanup, reporting dashboards, pricing review, KPI setup
- customer_pipeline: Customer Pipeline — CRM setup, follow-up automation, pipeline visibility, retention workflows
- team_operations: Team Operations — onboarding docs, role clarity, feedback loops, training materials, accountability

## Output format

Return ONLY a valid JSON array. No markdown fences, no commentary, no explanation.

Example:
[{"problem":"Process Design","description":"Document 5 core operational workflows as step-by-step SOPs with decision trees for common exceptions","estimated_hours":12},{"problem":"Customer Pipeline","description":"Configure CRM with custom pipeline stages, import existing contacts, and set up automated follow-up sequences","estimated_hours":10}]`

/**
 * Generate quote line items from assessment extraction data using Claude.
 *
 * @param extraction - The assessment extraction (problems, notes, etc.)
 * @param entityContext - Assembled context string from assembleEntityContext()
 * @param apiKey - Anthropic API key (falls back to env if not provided)
 * @returns Array of LineItem objects, or empty array on failure
 */
export async function generateQuoteLineItems(
  extraction: AssessmentExtraction,
  entityContext: string,
  apiKey?: string
): Promise<LineItem[]> {
  const key = apiKey
  if (!key) {
    console.error('[assessment-to-quote] No API key provided')
    return []
  }

  try {
    const userPrompt = buildUserPrompt(extraction, entityContext)

    const response = await fetch(ANTHROPIC_API_URL, {
      method: 'POST',
      headers: {
        'x-api-key': key,
        'anthropic-version': ANTHROPIC_VERSION,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        system: SYSTEM_PROMPT,
        messages: [{ role: 'user', content: userPrompt }],
      }),
    })

    if (!response.ok) {
      const body = await response.text().catch(() => '<unreadable>')
      console.error(
        `[assessment-to-quote] Claude API returned ${response.status}: ${body.slice(0, 200)}`
      )
      return []
    }

    const result = (await response.json()) as {
      content?: Array<{ type: string; text?: string }>
    }

    const textBlock = result?.content?.find((block) => block.type === 'text')
    if (!textBlock?.text) {
      console.error('[assessment-to-quote] Claude API returned empty content')
      return []
    }

    return parseLineItems(textBlock.text)
  } catch (err) {
    console.error('[assessment-to-quote] Error generating line items:', err)
    return []
  }
}

function buildUserPrompt(extraction: AssessmentExtraction, entityContext: string): string {
  const parts: string[] = []

  parts.push('## Problems identified in assessment')
  if (extraction.problems.length > 0) {
    parts.push(extraction.problems.map((p) => `- ${p}`).join('\n'))
  } else {
    parts.push('No specific problems checked.')
  }

  if (extraction.other_problem) {
    parts.push(`\nOther problem noted: ${extraction.other_problem}`)
  }

  parts.push(`\n## Assessment duration: ${extraction.duration_minutes} minutes`)

  if (extraction.notes) {
    parts.push(`\n## Consultant notes from the call\n${extraction.notes}`)
  }

  if (entityContext) {
    parts.push(`\n## Business context\n${entityContext}`)
  }

  parts.push(
    '\nGenerate quote line items based on the above. Return ONLY a JSON array, no other text.'
  )

  return parts.join('\n')
}

function parseLineItems(text: string): LineItem[] {
  const trimmed = text.trim()

  // Strip markdown fences if Claude includes them despite instructions
  const cleaned = trimmed.replace(/^```(?:json)?\s*\n?/, '').replace(/\n?```\s*$/, '')

  const parsed: unknown = JSON.parse(cleaned)

  if (!Array.isArray(parsed)) {
    console.error('[assessment-to-quote] Parsed response is not an array')
    return []
  }

  const items: LineItem[] = []
  for (const item of parsed) {
    if (
      typeof item === 'object' &&
      item !== null &&
      typeof (item as Record<string, unknown>).problem === 'string' &&
      typeof (item as Record<string, unknown>).description === 'string' &&
      typeof (item as Record<string, unknown>).estimated_hours === 'number'
    ) {
      items.push({
        problem: (item as Record<string, unknown>).problem as string,
        description: (item as Record<string, unknown>).description as string,
        estimated_hours: (item as Record<string, unknown>).estimated_hours as number,
      })
    }
  }

  if (items.length === 0) {
    console.error('[assessment-to-quote] No valid line items in parsed response')
  }

  return items
}
