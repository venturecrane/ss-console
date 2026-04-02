/**
 * Outreach draft generation via Claude API.
 *
 * Reads assembled entity context and generates a personalized outreach
 * email draft. Appended as a context entry of type 'outreach_draft'.
 *
 * Voice rules (from Decision #20 and CLAUDE.md Tone & Positioning Standard):
 * - Always "we" / "our team" — never "I" or "the consultant"
 * - Collaborative, not diagnostic — we work alongside the owner
 * - Objectives over problems — frame around where they're trying to go
 * - No pricing, no fixed timeframes
 * - No "systems" language — use "solution"
 * - Reference specific evidence from the signals
 */

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const MODEL = 'claude-sonnet-4-20250514'
const MAX_TOKENS = 1024

const OUTREACH_SYSTEM_PROMPT = `You are writing a short outreach email for SMD Services, a consulting team that helps Phoenix-area small businesses (10–25 employees) get their operations running smoothly.

## Voice Rules (STRICT — violations will be rejected)
- Always "we" / "our team" — NEVER "I" or "the consultant"
- Collaborative tone: "work alongside you", "figure out together"
- Frame around business objectives, not just problems
- No dollar amounts, no pricing, no hourly rates
- No fixed timeframes ("2-week sprint", "60-minute call")
- Use "solution" not "systems" in positioning
- Never judge the owner — gaps are normal growth pains
- Reference SPECIFIC evidence from the signals (job posting details, review patterns, permit filings)

## Email Structure
1. Opening line that shows you know something specific about their business (from the signals)
2. 1-2 sentences connecting their situation to what we do — collaborative, not salesy
3. Soft CTA: suggest a conversation, no pressure

## Constraints
- Maximum 100 words
- Subject line included (prefix with "Subject: ")
- No bullet points, no headers — just a natural email
- Sign off as "— The SMD Services team"

Output ONLY the email text. No commentary, no markdown fences.`

/**
 * Generate an outreach email draft from entity context.
 *
 * @param apiKey - Anthropic API key
 * @param entityName - Business name
 * @param assembledContext - Formatted context from assembleEntityContext()
 * @returns The generated outreach email draft
 */
export async function generateOutreachDraft(
  apiKey: string,
  entityName: string,
  assembledContext: string
): Promise<string> {
  const userPrompt = `Generate an outreach email for this business:

Business: ${entityName}

## Everything we know about them:

${assembledContext}

Write the outreach email now.`

  const response = await fetch(ANTHROPIC_API_URL, {
    method: 'POST',
    headers: {
      'x-api-key': apiKey,
      'anthropic-version': ANTHROPIC_VERSION,
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: MAX_TOKENS,
      system: OUTREACH_SYSTEM_PROMPT,
      messages: [{ role: 'user', content: userPrompt }],
    }),
  })

  if (!response.ok) {
    const body = await response.text().catch(() => '<unreadable>')
    throw new Error(`Claude API returned ${response.status}: ${body.slice(0, 200)}`)
  }

  const result = (await response.json()) as {
    content?: Array<{ type: string; text?: string }>
  }

  const textBlock = result?.content?.find((block) => block.type === 'text')
  if (!textBlock?.text) {
    throw new Error('Claude API returned empty content for outreach draft')
  }

  return textBlock.text.trim()
}
