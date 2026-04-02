/**
 * Claude review scoring caller.
 * Uses batch scoring (5-10 businesses per call) for cost efficiency.
 */

import {
  REVIEW_SCORING_SYSTEM_PROMPT,
  buildReviewScoringUserPrompt,
  validateReviewScoring,
} from '../../../src/lead-gen/prompts/review-scoring-prompt.js'
import type {
  ReviewScoring,
  BusinessReviewInput,
} from '../../../src/lead-gen/prompts/review-scoring-prompt.js'
import type { BusinessWithReviews } from './outscraper.js'

export type { ReviewScoring }

interface AnthropicResponse {
  content: Array<{ type: string; text: string }>
  stop_reason: string
}

/**
 * Score a single business's reviews using Claude.
 */
export async function scoreReviews(
  business: BusinessWithReviews,
  apiKey: string
): Promise<ReviewScoring | null> {
  const input: BusinessReviewInput = {
    business_name: business.name,
    place_id: business.place_id,
    category: business.category,
    area: business.area,
    overall_rating: business.rating,
    total_review_count: business.total_reviews,
    reviews: business.reviews.map((r) => ({
      author: r.author,
      rating: r.rating,
      text: r.text,
      date: r.date,
    })),
  }

  const userPrompt = buildReviewScoringUserPrompt(input)

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
      system: REVIEW_SCORING_SYSTEM_PROMPT,
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

  // Strip markdown code fences if present
  const cleaned = text.replace(/^```(?:json)?\s*\n?/i, '').replace(/\n?```\s*$/i, '')

  let parsed: unknown
  try {
    parsed = JSON.parse(cleaned)
  } catch {
    console.error(`Failed to parse Claude response: ${text.slice(0, 200)}`)
    return null
  }

  const validation = validateReviewScoring(parsed)
  if (!validation.valid) {
    console.error(`Validation failed: ${validation.errors.join(', ')}`)
    return null
  }

  return parsed as ReviewScoring
}
