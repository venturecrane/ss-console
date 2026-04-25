/**
 * Arizona Corporation Commission (ACC) filing lookup.
 * Searches eCorp for business entity registration.
 * Returns registered agent (often the owner), filing date, entity type.
 *
 * Note: This scrapes a government HTML page. Build with graceful failure.
 */

export interface AccEnrichment {
  entity_name: string
  entity_type: string | null
  filing_date: string | null
  status: string | null
  registered_agent: string | null
}

const ACC_SEARCH_URL = 'https://ecorp.azcc.gov/EntitySearch/Index'

/**
 * Search ACC for a business entity by name.
 * Returns the first matching result, or null if not found.
 *
 * Note: ACC may rate-limit or require CAPTCHA. Failures are expected
 * and handled gracefully.
 */
export async function lookupAcc(businessName: string): Promise<AccEnrichment | null> {
  // ACC uses a form POST for search
  const searchParams = new URLSearchParams({
    BusinessName: businessName.replace(/\b(llc|inc|corp|ltd)\b\.?/gi, '').trim(),
    SearchType: 'Contains',
  })

  const response = await fetch(ACC_SEARCH_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent': 'Mozilla/5.0 (compatible)',
    },
    body: searchParams.toString(),
    redirect: 'follow',
    signal: AbortSignal.timeout(10000),
  })

  if (!response.ok) return null

  const html = await response.text()

  // Extract first result from the table
  // ACC renders results in a table with class "table table-striped"
  const nameMatch = html.match(/EntityName[^>]*>([^<]+)</i)
  const typeMatch = html.match(/EntityType[^>]*>([^<]+)</i)
  const dateMatch = html.match(/ApprovalDate[^>]*>([^<]+)</i)
  const statusMatch = html.match(/EntityStatus[^>]*>([^<]+)</i)
  const agentMatch =
    html.match(/StatutoryAgent[^>]*>([^<]+)</i) ?? html.match(/RegisteredAgent[^>]*>([^<]+)</i)

  if (!nameMatch) return null

  return {
    entity_name: nameMatch[1].trim(),
    entity_type: typeMatch?.[1]?.trim() ?? null,
    filing_date: dateMatch?.[1]?.trim() ?? null,
    status: statusMatch?.[1]?.trim() ?? null,
    registered_agent: agentMatch?.[1]?.trim() ?? null,
  }
}
