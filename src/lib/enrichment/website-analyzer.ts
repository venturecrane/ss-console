/**
 * Website analyzer — fetch, clean, extract with Claude Haiku, detect tech stack.
 */

import { detectTechStack, type TechStackResult } from './tech-stack.js'

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const MODEL = 'claude-haiku-4-5-20251001'
const MAX_TOKENS = 1024

const EXTRACTION_PROMPT = `You are analyzing a small business website. Extract the following information from the HTML content. Return ONLY valid JSON, no commentary.

{
  "owner_name": "string or null — owner/founder name from About page, team page, or footer",
  "team_size": "number or null — employee count from team page, 'our team of X', or staff listings",
  "founding_year": "number or null — from 'established', 'since', 'founded in', copyright year",
  "contact_email": "string or null — business email from contact page or footer (not personal)",
  "services": "string[] — brief list of main services offered",
  "quality": "string — 'modern' or 'dated' based on overall site design indicators"
}`

export interface WebsiteEnrichment {
  owner_name: string | null
  team_size: number | null
  founding_year: number | null
  contact_email: string | null
  services: string[]
  quality: string
  tech_stack: TechStackResult
  pages_analyzed: string[]
}

/**
 * Analyze a business website. Fetches homepage (+ /about, /team if they exist),
 * extracts structured data with Claude Haiku, and detects technology stack.
 */
export async function analyzeWebsite(
  websiteUrl: string,
  anthropicKey: string
): Promise<WebsiteEnrichment | null> {
  // Normalize URL
  const baseUrl = websiteUrl.startsWith('http') ? websiteUrl : `https://${websiteUrl}`

  // Fetch pages — homepage always, /about and /team best-effort
  const pages: { url: string; html: string }[] = []

  const homepage = await fetchPage(baseUrl)
  if (!homepage) return null
  pages.push({ url: baseUrl, html: homepage })

  // Try common sub-pages
  for (const path of ['/about', '/about-us', '/team', '/our-team', '/contact']) {
    const subpage = await fetchPage(`${baseUrl}${path}`)
    if (subpage && subpage.length > 500) {
      pages.push({ url: `${baseUrl}${path}`, html: subpage })
    }
  }

  // Clean and combine HTML
  const combinedHtml = pages.map((p) => `--- Page: ${p.url} ---\n${cleanHtml(p.html)}`).join('\n\n')

  // Truncate to ~30KB to stay within reasonable token limits for Haiku
  const truncated = combinedHtml.slice(0, 30_000)

  // Tech stack detection (regex, no AI)
  const techStack = detectTechStack(pages.map((p) => p.html).join('\n'))

  // Claude Haiku extraction
  const extraction = await extractWithHaiku(truncated, anthropicKey)

  return {
    ...(extraction ?? {
      owner_name: null,
      team_size: null,
      founding_year: null,
      contact_email: null,
      services: [],
      quality: 'unknown',
    }),
    tech_stack: techStack,
    pages_analyzed: pages.map((p) => p.url),
  }
}

async function fetchPage(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; SMDBot/1.0)' },
      redirect: 'follow',
      signal: AbortSignal.timeout(5000),
    })
    if (!response.ok) return null
    const contentType = response.headers.get('content-type') ?? ''
    if (!contentType.includes('text/html')) return null
    return await response.text()
  } catch {
    return null
  }
}

function cleanHtml(html: string): string {
  return html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<nav[^>]*>[\s\S]*?<\/nav>/gi, '')
    .replace(/<footer[^>]*>[\s\S]*?<\/footer>/gi, '[FOOTER]') // Keep footer tag for contact info
    .replace(/<header[^>]*>[\s\S]*?<\/header>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

async function extractWithHaiku(
  htmlText: string,
  apiKey: string
): Promise<Omit<WebsiteEnrichment, 'tech_stack' | 'pages_analyzed'> | null> {
  try {
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
        system: EXTRACTION_PROMPT,
        messages: [
          { role: 'user', content: `Analyze this business website content:\n\n${htmlText}` },
        ],
      }),
    })

    if (!response.ok) return null

    const result = (await response.json()) as {
      content?: Array<{ type: string; text?: string }>
    }

    const text = result?.content?.find((b) => b.type === 'text')?.text?.trim()
    if (!text) return null

    // Strip code fences if present
    let jsonText = text
    if (jsonText.startsWith('```')) {
      jsonText = jsonText.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '')
    }

    const parsed = JSON.parse(jsonText)
    return {
      owner_name: typeof parsed.owner_name === 'string' ? parsed.owner_name : null,
      team_size: typeof parsed.team_size === 'number' ? parsed.team_size : null,
      founding_year: typeof parsed.founding_year === 'number' ? parsed.founding_year : null,
      contact_email: typeof parsed.contact_email === 'string' ? parsed.contact_email : null,
      services: Array.isArray(parsed.services) ? parsed.services : [],
      quality: typeof parsed.quality === 'string' ? parsed.quality : 'unknown',
    }
  } catch {
    return null
  }
}
