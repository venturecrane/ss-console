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

/**
 * Fetch all permits from all sources for the past 7 days.
 */
export async function fetchAllPermits(): Promise<PermitRecord[]> {
  const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)
  const results: PermitRecord[] = []

  const fetchers = [
    { name: 'Phoenix Permits', fn: () => fetchPhoenixPermits(since) },
    { name: 'Scottsdale Licenses', fn: () => fetchScottsdaleLicenses(since) },
    { name: 'Scottsdale Permits', fn: () => fetchScottsdalePermits(since) },
    { name: 'Mesa Permits', fn: () => fetchMesaPermits(since) },
    { name: 'Tempe Permits', fn: () => fetchTempePermits(since) },
  ]

  for (const { name, fn } of fetchers) {
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
  const url = `https://maps.phoenix.gov/pub/rest/services/Public/Planning_Permit/MapServer/1/query?where=${where}&outFields=PER_NUM,PERMIT_NAME,STREET_FULL_NAME,PER_ISSUE_DATE,PER_TYPE_DESC,SCOPE_DESC&f=json&resultRecordCount=100&orderByFields=PER_ISSUE_DATE DESC`

  const response = await fetch(url)
  if (!response.ok) return []

  const data = (await response.json()) as ArcGISResponse
  return (data.features ?? [])
    .filter((f) => f.attributes.PERMIT_NAME)
    .map((f) => ({
      business_name: String(f.attributes.PERMIT_NAME ?? ''),
      entity_type: 'Commercial Permit',
      address: String(f.attributes.STREET_FULL_NAME ?? ''),
      filing_date: epochToDate(f.attributes.PER_ISSUE_DATE),
      source: 'phoenix_permit' as const,
      permit_type:
        f.attributes.SCOPE_DESC != null
          ? String(f.attributes.SCOPE_DESC)
          : f.attributes.PER_TYPE_DESC != null
            ? String(f.attributes.PER_TYPE_DESC)
            : undefined,
      permit_number: f.attributes.PER_NUM != null ? String(f.attributes.PER_NUM) : undefined,
    }))
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
  return (data.features ?? [])
    .filter((f) => f.attributes.Company)
    .map((f) => ({
      business_name: String(f.attributes.Company ?? ''),
      entity_type: 'Business License',
      address: [f.attributes.ServAddrComp, f.attributes.ServCityStateZipComp]
        .filter(Boolean)
        .map(String)
        .join(', '),
      filing_date: epochToDate(f.attributes.BusinessStartDate),
      source: 'scottsdale_license' as const,
      permit_number: f.attributes.AcctNum != null ? String(f.attributes.AcctNum) : undefined,
    }))
}

// ---------------------------------------------------------------------------
// Scottsdale — ArcGIS REST (building permits)
// ---------------------------------------------------------------------------

async function fetchScottsdalePermits(since: Date): Promise<PermitRecord[]> {
  const timestamp = since.toISOString().split('T')[0]
  const where = encodeURIComponent(
    `(permit_type_desc LIKE '%TENANT%' OR permit_type_desc LIKE '%COMMERCIAL%') AND issuance_date > timestamp '${timestamp}'`
  )
  const url = `https://maps.scottsdaleaz.gov/arcgis/rest/services/My_Neighborhood/MapServer/1/query?where=${where}&outFields=permit_number,permit_type_desc,address,issuance_date,status_description&f=json&resultRecordCount=100&orderByFields=issuance_date DESC`

  const response = await fetch(url)
  if (!response.ok) return []

  const data = (await response.json()) as ArcGISResponse
  return (data.features ?? [])
    .filter((f) => f.attributes.address)
    .map((f) => ({
      business_name: String(f.attributes.address ?? ''), // Scottsdale permits don't have business names
      entity_type: 'Commercial Permit',
      address: String(f.attributes.address ?? ''),
      filing_date: epochToDate(f.attributes.issuance_date),
      source: 'scottsdale_permit' as const,
      permit_type:
        f.attributes.permit_type_desc != null ? String(f.attributes.permit_type_desc) : undefined,
      permit_number: String(f.attributes.permit_number ?? ''),
    }))
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

  return rows
    .filter((r) => r.application_name || r.applicant)
    .map((r) => ({
      business_name: r.application_name || r.applicant || '',
      entity_type: 'Commercial Permit',
      address: r.property_address ?? '',
      filing_date: r.issued_date?.split('T')[0] ?? '',
      source: 'mesa_permit' as const,
      permit_type: r.description_of_work ?? r.type_of_work,
      permit_number: r.permit_number,
    }))
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
  return (data.features ?? [])
    .filter((f) => f.attributes.ProjectName || f.attributes.Description)
    .map((f) => ({
      business_name: String(f.attributes.ProjectName ?? f.attributes.Description ?? ''),
      entity_type: 'Commercial Permit',
      address: [f.attributes.OriginalAddress1, f.attributes.OriginalCity]
        .filter(Boolean)
        .map(String)
        .join(', '),
      filing_date: epochToDate(f.attributes.IssuedDateDtm),
      source: 'tempe_permit' as const,
      permit_type: f.attributes.Type != null ? String(f.attributes.Type) : undefined,
      permit_number: f.attributes.PermitNum != null ? String(f.attributes.PermitNum) : undefined,
    }))
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
