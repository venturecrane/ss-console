/**
 * Deep website analysis using Claude Sonnet for comprehensive business intelligence.
 * Fetches homepage + discoverable subpages, extracts detailed profile.
 */

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const MODEL = 'claude-sonnet-4-20250514'
const MAX_TOKENS = 2048

const DEEP_ANALYSIS_PROMPT = `You are analyzing a small business website for comprehensive intelligence. Extract ALL available information. Return ONLY valid JSON:

{
  "owner_profile": {
    "name": "string or null",
    "title": "string or null",
    "background": "string or null — bio, education, career history mentioned"
  },
  "team": {
    "size_estimate": "number or null",
    "named_employees": ["array of {name, role} objects"],
    "departments_visible": ["array of department names"]
  },
  "business_profile": {
    "founding_year": "number or null",
    "services": ["array of services"],
    "service_areas": ["array of geographic areas served"],
    "certifications": ["array of certifications/licenses mentioned"],
    "awards": ["array of awards/recognition"],
    "partnerships": ["array of partner/affiliate mentions"]
  },
  "customer_signals": {
    "testimonials_count": "number",
    "case_studies_visible": "boolean",
    "portfolio_visible": "boolean",
    "pricing_visible": "boolean"
  },
  "digital_maturity": {
    "score": "1-10 integer",
    "reasoning": "1 sentence explanation",
    "online_booking": "boolean",
    "chat_widget": "boolean",
    "blog_active": "boolean — true if blog has posts within last 6 months",
    "ssl": "boolean",
    "mobile_friendly": "boolean"
  },
  "contact_info": {
    "email": "string or null",
    "phone": "string or null",
    "address": "string or null",
    "social_media": {"facebook": "url or null", "instagram": "url or null", "linkedin": "url or null"}
  }
}`

export interface DeepWebsiteAnalysis {
  owner_profile: { name: string | null; title: string | null; background: string | null }
  team: {
    size_estimate: number | null
    named_employees: Array<{ name: string; role: string }>
    departments_visible: string[]
  }
  business_profile: {
    founding_year: number | null
    services: string[]
    service_areas: string[]
    certifications: string[]
    awards: string[]
    partnerships: string[]
  }
  customer_signals: {
    testimonials_count: number
    case_studies_visible: boolean
    portfolio_visible: boolean
    pricing_visible: boolean
  }
  digital_maturity: {
    score: number
    reasoning: string
    online_booking: boolean
    chat_widget: boolean
    blog_active: boolean
    ssl: boolean
    mobile_friendly: boolean
  }
  contact_info: {
    email: string | null
    phone: string | null
    address: string | null
    social_media: { facebook: string | null; instagram: string | null; linkedin: string | null }
  }
  pages_analyzed: string[]
}

export async function deepWebsiteAnalysis(
  websiteUrl: string,
  anthropicKey: string
): Promise<DeepWebsiteAnalysis | null> {
  const baseUrl = websiteUrl.startsWith('http') ? websiteUrl : `https://${websiteUrl}`
  const pages: { url: string; html: string }[] = []

  // Fetch homepage first
  const homepage = await safeFetch(baseUrl)
  if (!homepage) return null
  pages.push({ url: baseUrl, html: homepage })

  // Discover and fetch subpages
  const subpaths = [
    '/about',
    '/about-us',
    '/our-team',
    '/team',
    '/staff',
    '/services',
    '/contact',
    '/contact-us',
    '/careers',
    '/jobs',
    '/testimonials',
    '/reviews',
    '/blog',
    '/portfolio',
    '/gallery',
  ]

  for (const path of subpaths) {
    if (pages.length >= 8) break // Cap at 8 pages to manage tokens
    const html = await safeFetch(`${baseUrl}${path}`)
    if (html && html.length > 500) {
      pages.push({ url: `${baseUrl}${path}`, html })
    }
  }

  // Clean and combine
  const combined = pages.map((p) => `=== ${p.url} ===\n${cleanHtml(p.html)}`).join('\n\n')
  const truncated = combined.slice(0, 50_000) // Larger budget for Sonnet

  // No outer try/catch: errors propagate to the instrumentation wrapper in
  // src/lib/enrichment/index.ts which classifies them (parse_error,
  // fetch_failed, etc.) and persists a failure row in enrichment_runs.
  // Returning null here means "API ran cleanly but had no useful data"
  // (recorded as `no_data`); throws mean "something broke" (recorded as
  // `failed` with classified kind).
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
      system: DEEP_ANALYSIS_PROMPT,
      messages: [{ role: 'user', content: `Analyze this business website:\n\n${truncated}` }],
    }),
  })

  if (!response.ok) return null

  const result = (await response.json()) as {
    content?: Array<{ type: string; text?: string }>
  }
  let text = result?.content?.find((b) => b.type === 'text')?.text?.trim()
  if (!text) return null
  if (text.startsWith('```')) text = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '')

  const parsed = JSON.parse(text)
  return { ...parsed, pages_analyzed: pages.map((p) => p.url) }
}

async function safeFetch(url: string): Promise<string | null> {
  // Per-page best-effort within deep_website. Returning null for one page
  // is normal (404 on /careers, etc.) and must not poison the whole module.
  try {
    const response = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (compatible; SMDBot/1.0)' },
      redirect: 'follow',
      signal: AbortSignal.timeout(5000),
    })
    if (!response.ok) return null
    const ct = response.headers.get('content-type') ?? ''
    if (!ct.includes('text/html')) return null
    return await response.text()
  } catch {
    return null
  }
}

function cleanHtml(html: string): string {
  return html
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}
