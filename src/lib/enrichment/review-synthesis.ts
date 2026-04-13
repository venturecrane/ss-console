/**
 * Cross-platform review synthesis using Claude Sonnet.
 * Reads existing signal and enrichment context, produces unified analysis.
 */

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const MODEL = 'claude-sonnet-4-20250514'
const MAX_TOKENS = 1024

export interface ReviewSynthesis {
  unified_rating: number | null
  total_reviews_across_platforms: number
  sentiment_trend: 'improving' | 'stable' | 'declining' | 'insufficient_data'
  top_themes: string[]
  operational_problems: Array<{ problem: string; confidence: string; evidence: string }>
  customer_sentiment: string
}

export async function synthesizeReviews(
  contextEntries: string,
  anthropicKey: string
): Promise<ReviewSynthesis | null> {
  try {
    const response = await fetch(ANTHROPIC_API_URL, {
      method: 'POST',
      headers: {
        'x-api-key': anthropicKey,
        'anthropic-version': ANTHROPIC_VERSION,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: MAX_TOKENS,
        system: `Synthesize all review data for this business across platforms. Map operational issues to these 5 solution areas: process_design, tool_systems, data_visibility, customer_pipeline, team_operations. Return ONLY valid JSON:
{
  "unified_rating": "number 1-5 or null",
  "total_reviews_across_platforms": "number",
  "sentiment_trend": "improving | stable | declining | insufficient_data",
  "top_themes": ["array of 3-5 recurring themes"],
  "operational_problems": [{"problem": "problem_id", "confidence": "high|medium|low", "evidence": "brief quote or pattern"}],
  "customer_sentiment": "1-2 sentence overall assessment"
}`,
        messages: [
          {
            role: 'user',
            content: `All available review and enrichment data:\n\n${contextEntries}`,
          },
        ],
      }),
    })

    if (!response.ok) return null

    const result = (await response.json()) as {
      content?: Array<{ type: string; text?: string }>
    }
    let text = result?.content?.find((b) => b.type === 'text')?.text?.trim()
    if (!text) return null
    if (text.startsWith('```')) text = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '')

    return JSON.parse(text)
  } catch {
    return null
  }
}
