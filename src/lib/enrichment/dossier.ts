/**
 * Intelligence brief (dossier) generation using Claude Sonnet.
 * Synthesizes all accumulated context into a structured 2-3 page assessment.
 */

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const MODEL = 'claude-sonnet-4-20250514'
const MAX_TOKENS = 4096

const DOSSIER_PROMPT = `You are generating a comprehensive intelligence brief for a consulting team (SMD Services) that sells operations cleanup engagements to Phoenix-area small businesses. Use "we" voice.

Generate a structured dossier in markdown format with these sections:

## Business Overview
- Name, vertical, location, estimated size, founding year
- Growth trajectory (growing/stable/declining, with evidence)
- Competitive position (vs. local peers)

## Owner / Decision-Maker Profile
- Name, role, management style (inferred from review responses, hiring patterns)
- Likely concerns and priorities
- Communication preference (responsive to digital, prefers phone, etc.)

## Technology & Operations Assessment
- Current tools detected (and what's missing)
- Digital maturity score with reasoning
- Key operational gaps mapped to the 6 universal SMB problems:
  1. Owner bottleneck
  2. Lead leakage
  3. Financial blindness
  4. Scheduling chaos
  5. Manual communication
  6. Employee retention

## Engagement Opportunity
- Top 2-3 problems we can address (with confidence level and evidence)
- Estimated engagement complexity (low/medium/high)
- Recommended approach and talking points for the assessment call
- Potential objections and responses

## Conversation Starters
- 3-4 specific, evidence-based opening lines for the assessment call
- Reference specific things we know about their business
- Collaborative tone — objectives over problems

Keep it concise but thorough. 800-1200 words.`

export async function generateDossier(
  assembledContext: string,
  entityName: string,
  anthropicKey: string
): Promise<string | null> {
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
      system: DOSSIER_PROMPT,
      messages: [
        {
          role: 'user',
          content: `Generate an intelligence brief for: ${entityName}\n\nAll available intelligence:\n\n${assembledContext}`,
        },
      ],
    }),
  })

  if (!response.ok) return null

  const result = (await response.json()) as {
    content?: Array<{ type: string; text?: string }>
  }
  return result?.content?.find((b) => b.type === 'text')?.text?.trim() ?? null
}
