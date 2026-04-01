/**
 * SODA API client for Phoenix, Scottsdale, and Chandler open data portals.
 *
 * Fetches commercial permits and business filings from free public APIs.
 * No API key required.
 */

export interface PermitRecord {
  business_name: string
  entity_type: string
  address: string
  filing_date: string
  source: 'phoenix_permit' | 'scottsdale_permit' | 'chandler_permit'
  permit_type?: string
  permit_number?: string
}

interface SodaRow {
  [key: string]: string | undefined
}

/** SODA API endpoints and field mappings per city. */
const CITY_SOURCES: Array<{
  name: string
  source: PermitRecord['source']
  url: string
  nameField: string
  addressField: string
  dateField: string
  typeField?: string
  numberField?: string
}> = [
  {
    name: 'Phoenix',
    source: 'phoenix_permit',
    url: 'https://www.phoenixopendata.com/resource/awks-67k4.json',
    nameField: 'owner_name',
    addressField: 'original_address',
    dateField: 'applied_date',
    typeField: 'type_desc',
    numberField: 'permit_num',
  },
  {
    name: 'Scottsdale',
    source: 'scottsdale_permit',
    url: 'https://data.scottsdaleaz.gov/resource/dpa5-2s5p.json',
    nameField: 'applicant_name',
    addressField: 'address',
    dateField: 'issue_date',
    typeField: 'permit_type',
    numberField: 'permit_number',
  },
  {
    name: 'Chandler',
    source: 'chandler_permit',
    url: 'https://data.chandleraz.gov/resource/rinb-g7xn.json',
    nameField: 'applicant',
    addressField: 'location',
    dateField: 'issue_date',
    typeField: 'description',
    numberField: 'permit_no',
  },
]

/**
 * Fetch recent commercial permits from a city's SODA API.
 * Looks back 7 days from the given date.
 */
async function fetchCityPermits(
  city: (typeof CITY_SOURCES)[number],
  since: string
): Promise<PermitRecord[]> {
  const params = new URLSearchParams({
    $where: `${city.dateField} > '${since}'`,
    $limit: '200',
    $order: `${city.dateField} DESC`,
  })

  const response = await fetch(`${city.url}?${params.toString()}`)

  if (!response.ok) {
    console.error(`SODA ${city.name}: ${response.status}`)
    return []
  }

  const rows = (await response.json()) as SodaRow[]

  return rows
    .filter((row) => row[city.nameField] && row[city.addressField])
    .map((row) => ({
      business_name: row[city.nameField]!,
      entity_type: 'Commercial Permit',
      address: row[city.addressField]!,
      filing_date: row[city.dateField] ?? new Date().toISOString().split('T')[0],
      source: city.source,
      permit_type: city.typeField ? row[city.typeField] : undefined,
      permit_number: city.numberField ? row[city.numberField] : undefined,
    }))
}

/**
 * Fetch permits from all three cities for the past 7 days.
 */
export async function fetchAllPermits(): Promise<PermitRecord[]> {
  const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0]
  const results: PermitRecord[] = []

  for (const city of CITY_SOURCES) {
    try {
      const permits = await fetchCityPermits(city, since)
      results.push(...permits)
      console.log(`SODA ${city.name}: ${permits.length} permits`)
    } catch (err) {
      console.error(`SODA ${city.name} error: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return results
}
