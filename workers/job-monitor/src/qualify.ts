/**
 * Claude qualification caller.
 *
 * Direct fetch to the Anthropic Messages API — no SDK needed.
 * Imports the system prompt and validation from the shared lead-gen code.
 */

import {
  JOB_QUALIFICATION_SYSTEM_PROMPT,
  buildJobQualificationUserPrompt,
  validateJobQualification,
} from '../../../src/lead-gen/prompts/job-qualification-prompt.js'
import type {
  JobQualification,
  JobPostingInput,
} from '../../../src/lead-gen/prompts/job-qualification-prompt.js'
import type { SerpApiJob } from './serpapi.js'

export type { JobQualification }

interface AnthropicResponse {
  content: Array<{ type: string; text: string }>
  stop_reason: string
}

/**
 * Qualify a job posting using Claude.
 * Returns the qualification result if Claude produces valid JSON, null otherwise.
 */
export async function qualifyJob(
  job: SerpApiJob,
  apiKey: string
): Promise<JobQualification | null> {
  const input: JobPostingInput = {
    title: job.title,
    company: job.company_name,
    location: job.location,
    description: job.description,
    source: 'google_jobs',
    url: job.apply_options?.[0]?.link,
  }

  const userPrompt = buildJobQualificationUserPrompt(input)

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-sonnet-4-6',
      max_tokens: 1024,
      temperature: 0.2,
      system: JOB_QUALIFICATION_SYSTEM_PROMPT,
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
  if (!text) {
    console.error('Anthropic returned no text content')
    return null
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    console.error(`Failed to parse Claude response as JSON: ${text.slice(0, 200)}`)
    return null
  }

  const validation = validateJobQualification(parsed)
  if (!validation.valid) {
    console.error(`Validation failed: ${validation.errors.join(', ')}`)
    return null
  }

  return parsed as JobQualification
}

/**
 * Derive a numeric pain score (1-10) from qualification confidence and problem count.
 */
export function derivePainScore(q: JobQualification): number {
  const problemCount = q.problems_signaled.length
  if (q.confidence === 'high') return problemCount >= 3 ? 9 : 8
  if (q.confidence === 'medium') return problemCount >= 2 ? 7 : 6
  return 5 // low confidence
}
