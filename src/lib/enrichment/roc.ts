/**
 * Arizona Registrar of Contractors (ROC) license lookup.
 * Only for trades businesses (home_services, contractor_trades).
 * Returns license status, classification, complaint history.
 *
 * Note: Government HTML scraping — build with graceful failure.
 */

export interface RocEnrichment {
  license_number: string | null
  classification: string | null
  status: string | null
  business_name: string
  complaint_count: number | null
}

const ROC_SEARCH_URL = 'https://roc.az.gov/contractor-search'

/**
 * Search Arizona ROC for a contractor license by business name.
 * Returns first matching result, or null.
 */
export async function lookupRoc(businessName: string): Promise<RocEnrichment | null> {
  const params = new URLSearchParams({
    company_name: businessName.replace(/\b(llc|inc|corp|ltd)\b\.?/gi, '').trim(),
  })

  const response = await fetch(`${ROC_SEARCH_URL}?${params.toString()}`, {
    headers: { 'User-Agent': 'Mozilla/5.0 (compatible)' },
    redirect: 'follow',
    signal: AbortSignal.timeout(10000),
  })

  if (!response.ok) return null

  const html = await response.text()

  // Extract license data from ROC results page
  const licenseMatch = html.match(/License\s*#?\s*:?\s*(\w+)/i)
  const classMatch = html.match(/Classification[^:]*:\s*([^<\n]+)/i)
  const statusMatch = html.match(/Status[^:]*:\s*([^<\n]+)/i)
  const nameMatch = html.match(/Business\s*Name[^:]*:\s*([^<\n]+)/i)
  const complaintMatch = html.match(/Complaints?\s*:?\s*(\d+)/i)

  if (!licenseMatch && !nameMatch) return null

  return {
    license_number: licenseMatch?.[1]?.trim() ?? null,
    classification: classMatch?.[1]?.trim() ?? null,
    status: statusMatch?.[1]?.trim() ?? null,
    business_name: nameMatch?.[1]?.trim() ?? businessName,
    complaint_count: complaintMatch ? parseInt(complaintMatch[1]) : null,
  }
}
