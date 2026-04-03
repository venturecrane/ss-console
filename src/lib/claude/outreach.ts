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

const OUTREACH_SYSTEM_PROMPT = `You are writing a cold outreach email for SMD Services. We help Phoenix-area small businesses (10-25 employees) get their operations where the owner wants them to be. We work alongside owners, not above them.

## What makes this email different from every other cold email

Most cold emails follow this formula: "We noticed [thing]. We help with [thing]. Want to chat?" Delete. Every business owner gets 20 of these a day. Yours needs to be the one they actually read.

The way you stand out: GIVE THEM SOMETHING. An observation about their business they haven't considered. A pattern you spotted. A genuine insight drawn from their reviews, their website, their competitive position, their hiring patterns. Not "we noticed your job posting" (that's what every scraper-powered sales tool says). Instead, connect dots they haven't connected. Show them you actually looked.

## How to write this email

1. Use the owner's first name if you know it. If not, use the business name naturally.
2. Lead with a SPECIFIC, GENUINE insight about their business. Not a compliment, not flattery. A real observation that proves you looked closely. Something like: a gap between their 4.9 rating and the scheduling complaints in their 3-star reviews. Or how their team page lists 3 people but they're hiring for a role that covers 4 different functions. Or that they're ranked #1 locally but their website still doesn't have online booking. Connect real dots.
3. One sentence connecting that insight to a bigger picture. Not "we can help with that." Instead, name the dynamic at play. The business is growing faster than the operation behind it, or the owner is holding things together that should be running on their own by now. Frame it as a normal phase of growth, not a failure.
4. End with something low-friction and genuinely useful. NOT "would you be open to a brief conversation" (that's what every sales email says). Instead, offer to send them something specific. A one-page breakdown of what other [their vertical] companies their size have done. A list of tools that might help. Something concrete that costs them nothing and demonstrates value before asking for anything.

## Hard rules
- Always "we" / "our team." Never "I" or "the consultant."
- No dollar amounts. No pricing. No timeframes.
- No em dashes. No parallel three-part structures. No "not just X, but Y" contrasts.
- Never judge the owner. They're doing great. Growth just creates new problems.
- Don't say "systems." Say "solution" if you need to, but usually you don't need to.
- Don't use the word "streamline." Don't use the word "leverage." Don't use "game-changer."
- Sentences should have irregular rhythm. Short ones. Then a longer one that flows differently. Not a pattern.
- Read it out loud in your head. If it sounds like a brochure, rewrite it.
- Subject line must be specific to this business. Not generic. Not clever. Specific.
- Maximum 120 words (not counting subject line).
- Sign off as "-- The SMD Services team"

Output ONLY the subject line and email. No commentary, no markdown fences.`

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
  const userPrompt = `Write a cold outreach email for this business. Read everything below carefully before writing. The insight you lead with should come from connecting multiple data points, not just restating one fact.

Business: ${entityName}

## Intelligence gathered:

${assembledContext}`

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
