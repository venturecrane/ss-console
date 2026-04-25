/**
 * LinkedIn intelligence via Proxycurl API.
 * Looks up company by name, returns employee count, industry, specialties.
 * Low cost per lookup via Proxycurl.
 */

const PROXYCURL_API_URL = 'https://nubela.co/proxycurl/api/linkedin/company/resolve'

export interface LinkedInEnrichment {
  linkedin_url: string
  company_name: string
  employee_count: number | null
  industry: string | null
  description: string | null
}

export async function lookupLinkedIn(
  companyName: string,
  location: string | null,
  apiKey: string
): Promise<LinkedInEnrichment | null> {
  const params = new URLSearchParams({
    company_name: companyName,
    company_location: location ?? 'Phoenix, Arizona',
  })

  const response = await fetch(`${PROXYCURL_API_URL}?${params.toString()}`, {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal: AbortSignal.timeout(10000),
  })

  if (!response.ok) return null

  const data = (await response.json()) as {
    url?: string
    name?: string
    company_size?: number[]
    industry?: string
    description?: string
  }

  if (!data.url) return null

  return {
    linkedin_url: data.url,
    company_name: data.name ?? companyName,
    employee_count: data.company_size
      ? Math.round((data.company_size[0] + (data.company_size[1] ?? data.company_size[0])) / 2)
      : null,
    industry: data.industry ?? null,
    description: data.description ?? null,
  }
}
