/**
 * Open data API clients for Phoenix metro area permit/license data.
 *
 * Sources:
 * 1. Phoenix — ArcGIS REST (planning permits)
 * 2. Scottsdale — ArcGIS REST (business licenses + building permits)
 * 3. Mesa — Socrata SODA API (commercial building permits)
 * 4. Tempe — ArcGIS FeatureServer (building permits)
 *
 * All free, no authentication required.
 */

export interface PermitRecord {
  business_name: string
  entity_type: string
  address: string
  filing_date: string
  source:
    | 'phoenix_permit'
    | 'scottsdale_permit'
    | 'scottsdale_license'
    | 'mesa_permit'
    | 'tempe_permit'
  permit_type?: string
  permit_number?: string
}

export type SodaCity = 'phoenix' | 'scottsdale_licenses' | 'scottsdale_permits' | 'mesa' | 'tempe'

// ---------------------------------------------------------------------------
// Business-name resolution
// ---------------------------------------------------------------------------
//
// Each SODA source exposes a different set of fields, and historically each
// fetcher had its own ad-hoc rule for picking the "name" — which is how
// Phoenix ended up shipping PERMIT_NAME ("Sprkler Sys Mod No Hard Lid"),
// Scottsdale Permits shipped the street address, and Tempe fell back to a
// Description field. resolveBusinessName() centralizes the rules.
//
// Return null to skip the record (no entity will be created). This is the
// correct behavior when a source structurally lacks a business-name field —
// inventing one from a description field corrupts the signal stream.
//
// The isLikelyBusinessName guard is a last-resort filter — applied even
// when the primary field is populated — to catch cases where the source
// field happens to contain description-like text (e.g., a contractor
// filling in the wrong field).

// Trade-verb prefixes that signal a permit description rather than a business name.
// Applied with word-boundary semantics — "REMODEL CONSTRUCTION LLC" survives
// because the LLC suffix overrides the prefix match.
const TRADE_VERB_PREFIXES = [
  'REPIPE',
  'REPLACE',
  'REMODEL',
  'REROOF',
  'REROOFING',
  'RE-ROOF',
  'BUILDOUT',
  'BUILD OUT',
  'ADD ',
  'ADDITION',
  'DEMO',
  'DEMOLITION',
  'TENANT IMPROVEMENT',
  'WTR ',
  'WATER ',
  'SWMP',
]

// Description keywords — if present in an all-caps name with no entity
// suffix, the name is almost certainly a permit description, not a business.
// e.g. "ITULE COOLER EXPANSION", "LORRY COMMON SUPPORTS", "FIRE ALARM DIALER".
const DESCRIPTION_KEYWORDS = [
  'EXPANSION',
  'INSTALLATION',
  'SYSTEM',
  'REPLACEMENT',
  'MODIFICATION',
  'SUPPORTS',
  'DIALER',
  'SPRINKLER',
  'ALARM',
  'INSPECTION',
  'PHOTOVOLTAIC',
  'BATTERY',
]

/**
 * Heuristic: looks like a permit description, not a business name.
 * Rejected names will cause the record to be skipped.
 */
export function isLikelyBusinessName(name: string): boolean {
  const trimmed = name.trim()
  if (trimmed.length < 3) return false
  const upper = trimmed.toUpperCase()
  const hasEntitySuffix =
    /\b(LLC|L\.L\.C\.|INC|INC\.|CORP|CORPORATION|CO\.?|LLP|LTD|PLLC|PC|P\.C\.|LP|GROUP|HOLDINGS|COMPANY|ENTERPRISES|SERVICES|SOLUTIONS|CONSTRUCTION|CONTRACTING|ELECTRIC|PLUMBING|MECHANICAL|REALTY|ASSOCIATES|PARTNERS|&)\b/i.test(
      trimmed
    )
  // OWNER literal — placeholder field used by some permit systems.
  if (upper === 'OWNER') return false
  // Trade-verb prefix check (regardless of case).
  for (const prefix of TRADE_VERB_PREFIXES) {
    if (upper.startsWith(prefix) && !hasEntitySuffix) return false
  }
  // Description-keyword check for all-caps inputs without entity suffix.
  if (upper === trimmed && !hasEntitySuffix) {
    for (const kw of DESCRIPTION_KEYWORDS) {
      if (upper.includes(kw)) return false
    }
  }
  return true
}

export interface ResolvedName {
  name: string
  /** Was this a contractor's name (vs. the business at the location)? */
  role: 'business' | 'contractor' | 'unknown'
}

/**
 * Resolve a business name from a SODA source row. Returns null when no
 * usable name exists — skip the record rather than invent one.
 */
export function resolveBusinessName(
  source: SodaCity,
  row: Record<string, unknown>
): ResolvedName | null {
  switch (source) {
    case 'phoenix': {
      // PERMIT_NAME is a description field ("Sprkler Sys Mod..."). Never use.
      // PROFESS_NAME is the contractor's company — real business name, but
      // it's the contractor, not the business occupying the space.
      const raw = typeof row.PROFESS_NAME === 'string' ? row.PROFESS_NAME.trim() : ''
      if (!raw || !isLikelyBusinessName(raw)) return null
      return { name: raw, role: 'contractor' }
    }
    case 'scottsdale_licenses': {
      // `Company` is the licensee — the actual business.
      const raw = typeof row.Company === 'string' ? row.Company.trim() : ''
      if (!raw || !isLikelyBusinessName(raw)) return null
      return { name: raw, role: 'business' }
    }
    case 'scottsdale_permits': {
      // No business-name field in this schema. Permits here have only
      // an `address`. Skip entirely.
      return null
    }
    case 'mesa': {
      const app = typeof row.application_name === 'string' ? row.application_name.trim() : ''
      const applicant = typeof row.applicant === 'string' ? row.applicant.trim() : ''
      const raw = app || applicant
      if (!raw || !isLikelyBusinessName(raw)) return null
      return { name: raw, role: app ? 'unknown' : 'contractor' }
    }
    case 'tempe': {
      // ProjectName only — Description is a work description, never a name.
      const raw = typeof row.ProjectName === 'string' ? row.ProjectName.trim() : ''
      if (!raw || !isLikelyBusinessName(raw)) return null
      return { name: raw, role: 'unknown' }
    }
  }
}

/**
 * Fetch all permits from all sources for the past 7 days.
 * `enabledCities` (if provided) filters which feeds run — skipped feeds
 * are not fetched, keeping API quota usage proportional to what's on.
 */
export async function fetchAllPermits(enabledCities?: SodaCity[]): Promise<PermitRecord[]> {
  const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
  const results: PermitRecord[] = []
  const allCities: SodaCity[] = [
    'phoenix',
    'scottsdale_licenses',
    'scottsdale_permits',
    'mesa',
    'tempe',
  ]
  const active = new Set<SodaCity>(enabledCities ?? allCities)

  const fetchers: Array<{ city: SodaCity; name: string; fn: () => Promise<PermitRecord[]> }> = [
    { city: 'phoenix', name: 'Phoenix Permits', fn: () => fetchPhoenixPermits(since) },
    {
      city: 'scottsdale_licenses',
      name: 'Scottsdale Licenses',
      fn: () => fetchScottsdaleLicenses(since),
    },
    {
      city: 'scottsdale_permits',
      name: 'Scottsdale Permits',
      fn: () => fetchScottsdalePermits(since),
    },
    { city: 'mesa', name: 'Mesa Permits', fn: () => fetchMesaPermits(since) },
    { city: 'tempe', name: 'Tempe Permits', fn: () => fetchTempePermits(since) },
  ]

  for (const { city, name, fn } of fetchers) {
    if (!active.has(city)) {
      console.log(`${name}: skipped (disabled in config)`)
      continue
    }
    try {
      const records = await fn()
      results.push(...records)
      console.log(`${name}: ${records.length} records`)
    } catch (err) {
      console.error(`${name} error: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  return results
}

// ---------------------------------------------------------------------------
// Phoenix — ArcGIS REST (planning permits)
// ---------------------------------------------------------------------------

async function fetchPhoenixPermits(since: Date): Promise<PermitRecord[]> {
  const timestamp = since.toISOString().split('T')[0]
  const where = encodeURIComponent(
    `(SCOPE_DESC LIKE '%COMM%' OR SCOPE_DESC LIKE '%TENANT%' OR PER_TYPE_DESC LIKE '%COMM%') AND PER_ISSUE_DATE > timestamp '${timestamp}'`
  )
  const url = `https://maps.phoenix.gov/pub/rest/services/Public/Planning_Permit/MapServer/1/query?where=${where}&outFields=PER_NUM,PERMIT_NAME,PROFESS_NAME,STREET_FULL_NAME,PER_ISSUE_DATE,PER_TYPE_DESC,SCOPE_DESC&f=json&resultRecordCount=100&orderByFields=PER_ISSUE_DATE DESC`

  const response = await fetch(url)
  if (!response.ok) return []

  const data = (await response.json()) as ArcGISResponse
  const records: PermitRecord[] = []
  let skipped = 0
  for (const f of data.features ?? []) {
    const resolved = resolveBusinessName('phoenix', f.attributes)
    if (!resolved) {
      skipped++
      continue
    }
    records.push({
      business_name: resolved.name,
      entity_type: 'Commercial Permit',
      address: String(f.attributes.STREET_FULL_NAME ?? ''),
      filing_date: epochToDate(f.attributes.PER_ISSUE_DATE),
      source: 'phoenix_permit',
      permit_type:
        f.attributes.SCOPE_DESC != null
          ? String(f.attributes.SCOPE_DESC)
          : f.attributes.PER_TYPE_DESC != null
            ? String(f.attributes.PER_TYPE_DESC)
            : undefined,
      permit_number: f.attributes.PER_NUM != null ? String(f.attributes.PER_NUM) : undefined,
    })
  }
  if (skipped > 0) console.log(`Phoenix: skipped ${skipped} records (no business name)`)
  return records
}

// ---------------------------------------------------------------------------
// Scottsdale — ArcGIS REST (business licenses)
// ---------------------------------------------------------------------------

async function fetchScottsdaleLicenses(since: Date): Promise<PermitRecord[]> {
  const timestamp = since.toISOString().split('T')[0]
  const where = encodeURIComponent(
    `BusinessStartDate > timestamp '${timestamp}' AND AcctStatus='Active'`
  )
  const url = `https://maps.scottsdaleaz.gov/arcgis/rest/services/OpenData_Tabular/MapServer/6/query?where=${where}&outFields=Company,ServAddrComp,ServCityStateZipComp,BusinessStartDate,AcctNum&f=json&resultRecordCount=100&orderByFields=BusinessStartDate DESC`

  const response = await fetch(url)
  if (!response.ok) return []

  const data = (await response.json()) as ArcGISResponse
  const records: PermitRecord[] = []
  let skipped = 0
  for (const f of data.features ?? []) {
    const resolved = resolveBusinessName('scottsdale_licenses', f.attributes)
    if (!resolved) {
      skipped++
      continue
    }
    records.push({
      business_name: resolved.name,
      entity_type: 'Business License',
      address: [f.attributes.ServAddrComp, f.attributes.ServCityStateZipComp]
        .filter(Boolean)
        .map(String)
        .join(', '),
      filing_date: epochToDate(f.attributes.BusinessStartDate),
      source: 'scottsdale_license',
      permit_number: f.attributes.AcctNum != null ? String(f.attributes.AcctNum) : undefined,
    })
  }
  if (skipped > 0) console.log(`Scottsdale Licenses: skipped ${skipped} records (no business name)`)
  return records
}

// ---------------------------------------------------------------------------
// Scottsdale — ArcGIS REST (building permits)
// ---------------------------------------------------------------------------

async function fetchScottsdalePermits(_since: Date): Promise<PermitRecord[]> {
  // Scottsdale building permits have no business-name field — only a street
  // address. Inventing a name from the address is a CLAUDE.md Pattern B
  // violation. resolveBusinessName('scottsdale_permits', ...) always returns
  // null, so we skip the API call entirely.
  return []
}

// ---------------------------------------------------------------------------
// Mesa — Socrata SODA API (commercial building permits)
// ---------------------------------------------------------------------------

async function fetchMesaPermits(since: Date): Promise<PermitRecord[]> {
  const dateStr = since.toISOString().split('T')[0]
  const url = `https://data.mesaaz.gov/resource/dzpk-hxfb.json?$where=permit_type='COM' AND issued_date>'${dateStr}'&$order=issued_date DESC&$limit=100`

  const response = await fetch(url)
  if (!response.ok) return []

  const rows = (await response.json()) as Array<{
    permit_number?: string
    application_name?: string
    applicant?: string
    property_address?: string
    issued_date?: string
    description_of_work?: string
    type_of_work?: string
  }>

  const records: PermitRecord[] = []
  let skipped = 0
  for (const r of rows) {
    const resolved = resolveBusinessName('mesa', r as unknown as Record<string, unknown>)
    if (!resolved) {
      skipped++
      continue
    }
    records.push({
      business_name: resolved.name,
      entity_type: 'Commercial Permit',
      address: r.property_address ?? '',
      filing_date: r.issued_date?.split('T')[0] ?? '',
      source: 'mesa_permit',
      permit_type: r.description_of_work ?? r.type_of_work,
      permit_number: r.permit_number,
    })
  }
  if (skipped > 0) console.log(`Mesa: skipped ${skipped} records (no business name)`)
  return records
}

// ---------------------------------------------------------------------------
// Tempe — ArcGIS FeatureServer (building permits)
// ---------------------------------------------------------------------------

async function fetchTempePermits(since: Date): Promise<PermitRecord[]> {
  const timestamp = since.toISOString().split('T')[0]
  const where = encodeURIComponent(
    `Type LIKE '%Commercial%' AND IssuedDateDtm > timestamp '${timestamp}'`
  )
  const url = `https://services.arcgis.com/lQySeXwbBg53XWDi/arcgis/rest/services/building_permits/FeatureServer/0/query?where=${where}&outFields=PermitNum,ProjectName,Description,Type,IssuedDateDtm,OriginalAddress1,OriginalCity,EstProjectCost&f=json&resultRecordCount=100&orderByFields=IssuedDateDtm DESC`

  const response = await fetch(url)
  if (!response.ok) return []

  const data = (await response.json()) as ArcGISResponse
  const records: PermitRecord[] = []
  let skipped = 0
  for (const f of data.features ?? []) {
    const resolved = resolveBusinessName('tempe', f.attributes)
    if (!resolved) {
      skipped++
      continue
    }
    records.push({
      business_name: resolved.name,
      entity_type: 'Commercial Permit',
      address: [f.attributes.OriginalAddress1, f.attributes.OriginalCity]
        .filter(Boolean)
        .map(String)
        .join(', '),
      filing_date: epochToDate(f.attributes.IssuedDateDtm),
      source: 'tempe_permit',
      permit_type: f.attributes.Type != null ? String(f.attributes.Type) : undefined,
      permit_number: f.attributes.PermitNum != null ? String(f.attributes.PermitNum) : undefined,
    })
  }
  if (skipped > 0) console.log(`Tempe: skipped ${skipped} records (no business name)`)
  return records
}

// ---------------------------------------------------------------------------
// Shared types and helpers
// ---------------------------------------------------------------------------

interface ArcGISResponse {
  features?: Array<{ attributes: Record<string, string | number | null> }>
}

function epochToDate(epoch: string | number | null): string {
  if (!epoch) return new Date().toISOString().split('T')[0]
  const ms = typeof epoch === 'string' ? parseInt(epoch, 10) : epoch
  return new Date(ms).toISOString().split('T')[0]
}
