/**
 * Claude qualification caller for new business filings/permits.
 */

import {
  SYSTEM_PROMPT,
  buildUserPrompt,
  validate,
} from '../../../src/lead-gen/prompts/new-business-prompt.js'
import type {
  NewBusinessQualification,
  NewBusinessInput,
} from '../../../src/lead-gen/prompts/new-business-prompt.js'
import type { PermitRecord } from './soda.js'

export type { NewBusinessQualification }

interface AnthropicResponse {
  content: Array<{ type: string; text: string }>
  stop_reason: string
}

/**
 * Qualify a new business filing using Claude.
 */
export async function qualifyNewBusiness(
  permit: PermitRecord,
  apiKey: string
): Promise<NewBusinessQualification | null> {
  const input: NewBusinessInput = {
    business_name: permit.business_name,
    entity_type: permit.entity_type,
    address: permit.address,
    filing_date: permit.filing_date,
    source: permit.source,
    permit_type: permit.permit_type,
  }

  const userPrompt = buildUserPrompt(input)

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5',
      max_tokens: 1024,
      temperature: 0.2,
      system: SYSTEM_PROMPT,
      messages: [{ role: 'user', content: userPrompt }],
    }),
  })

  if (!response.ok) {
    const text = await response.text()
    console.error(`Anthropic API ${response.status}: ${text.slice(0, 200)}`)
    return null
  }

  const data = (await response.json()) as AnthropicResponse
  const text = data.content?.[0]?.text
  if (!text) return null

  // Strip markdown code fences if present (Haiku sometimes wraps JSON)
  const cleaned = text.replace(/^```(?:json)?\s*\n?/i, '').replace(/\n?```\s*$/i, '')

  let parsed: unknown
  try {
    parsed = JSON.parse(cleaned)
  } catch {
    console.error(`Failed to parse Claude response: ${text.slice(0, 200)}`)
    return null
  }

  const validation = validate(parsed)
  if (!validation.valid) {
    console.error(`Validation failed: ${validation.errors.join(', ')}`)
    return null
  }

  return parsed as NewBusinessQualification
}
