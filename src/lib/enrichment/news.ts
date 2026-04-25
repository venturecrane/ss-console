/**
 * News/press search via SerpAPI Google Search + Claude Haiku extraction.
 */

const ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
const ANTHROPIC_VERSION = '2023-06-01'
const HAIKU_MODEL = 'claude-haiku-4-5-20251001'

export interface NewsEnrichment {
  mentions: Array<{
    title: string
    source: string
    snippet: string
  }>
  summary: string
}

export async function searchNews(
  entityName: string,
  area: string | null,
  serpApiKey: string,
  anthropicKey: string
): Promise<NewsEnrichment | null> {
  const query = `"${entityName}" ${area ?? 'Phoenix AZ'}`

  // SerpAPI Google Search
  const params = new URLSearchParams({
    engine: 'google',
    q: query,
    location: 'Phoenix, Arizona, United States',
    num: '5',
    api_key: serpApiKey,
  })

  const response = await fetch(`https://serpapi.com/search?${params.toString()}`)
  if (!response.ok) return null

  const data = (await response.json()) as {
    organic_results?: Array<{
      title?: string
      source?: string
      snippet?: string
      link?: string
    }>
  }

  const results = data.organic_results ?? []
  if (results.length === 0) return null

  const mentions = results
    .filter((r) => r.title && r.snippet)
    .map((r) => ({
      title: r.title!,
      source: r.source ?? new URL(r.link ?? '').hostname,
      snippet: r.snippet!,
    }))

  if (mentions.length === 0) return null

  // Claude Haiku to summarize relevance
  const summaryResponse = await fetch(ANTHROPIC_API_URL, {
    method: 'POST',
    headers: {
      'x-api-key': anthropicKey,
      'anthropic-version': ANTHROPIC_VERSION,
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model: HAIKU_MODEL,
      max_tokens: 256,
      messages: [
        {
          role: 'user',
          content: `Summarize what these search results reveal about "${entityName}" in 1-2 sentences. Focus on awards, community involvement, growth signals, or reputation. If results are irrelevant (wrong business, ads), say "No relevant mentions found."\n\n${mentions.map((m) => `${m.title}: ${m.snippet}`).join('\n')}`,
        },
      ],
    }),
  })

  let summary = 'Search results found but not summarized.'
  if (summaryResponse.ok) {
    const summaryResult = (await summaryResponse.json()) as {
      content?: Array<{ type: string; text?: string }>
    }
    summary = summaryResult?.content?.find((b) => b.type === 'text')?.text?.trim() ?? summary
  }

  return { mentions, summary }
}
