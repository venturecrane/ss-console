/**
 * SerpAPI client for Google Jobs search.
 *
 * SerpAPI plan: $50/mo for 5,000 searches. At 8 queries/day = 240/mo.
 */

export interface SerpApiJob {
  title: string
  company_name: string
  location: string
  description: string
  job_id: string
  apply_options?: Array<{ title: string; link: string }>
  company_url?: string
}

interface SerpApiResponse {
  jobs_results?: SerpApiJob[]
  error?: string
}

/**
 * Search Google Jobs via SerpAPI for a given query term.
 * Returns the jobs_results array, or empty on error.
 */
export async function searchJobs(query: string, apiKey: string): Promise<SerpApiJob[]> {
  const params = new URLSearchParams({
    engine: 'google_jobs',
    q: query,
    location: 'Phoenix, Arizona, United States',
    chips: 'date_posted:week',
    api_key: apiKey,
  })

  const url = `https://serpapi.com/search?${params.toString()}`

  const response = await fetch(url)

  if (response.status === 401) {
    throw new Error('SerpAPI: 401 Unauthorized — API key may be expired')
  }

  if (response.status === 429) {
    // Retry once after 1 second
    await new Promise((r) => setTimeout(r, 1000))
    const retry = await fetch(url)
    if (!retry.ok) {
      throw new Error(`SerpAPI: ${retry.status} on retry for query "${query}"`)
    }
    const data = (await retry.json()) as SerpApiResponse
    return data.jobs_results ?? []
  }

  if (!response.ok) {
    console.error(`SerpAPI: ${response.status} for query "${query}", skipping`)
    return []
  }

  const data = (await response.json()) as SerpApiResponse
  return data.jobs_results ?? []
}

// Query list moved to generator_config. Defaults live in
// src/lib/generators/types.ts and are merged at worker invocation.
