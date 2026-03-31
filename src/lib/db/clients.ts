/**
 * Client data access layer.
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID() (ULID-like uniqueness for D1).
 */

export interface Client {
  id: string
  org_id: string
  business_name: string
  vertical: string | null
  employee_count: number | null
  years_in_business: number | null
  source: string | null
  referred_by: string | null
  status: string
  notes: string | null
  created_at: string
  updated_at: string
}

export type ClientVertical =
  | 'home_services'
  | 'professional_services'
  | 'contractor_trades'
  | 'retail_salon'
  | 'restaurant'
  | 'other'

export type ClientStatus = 'prospect' | 'assessed' | 'quoted' | 'active' | 'completed' | 'dead'

export const CLIENT_VERTICALS: { value: ClientVertical; label: string }[] = [
  { value: 'home_services', label: 'Home Services' },
  { value: 'professional_services', label: 'Professional Services' },
  { value: 'contractor_trades', label: 'Contractor / Trades' },
  { value: 'retail_salon', label: 'Retail / Salon / Spa' },
  { value: 'restaurant', label: 'Restaurant / Food Service' },
  { value: 'other', label: 'Other' },
]

export const CLIENT_STATUSES: { value: ClientStatus; label: string }[] = [
  { value: 'prospect', label: 'Prospect' },
  { value: 'assessed', label: 'Assessed' },
  { value: 'quoted', label: 'Quoted' },
  { value: 'active', label: 'Active' },
  { value: 'completed', label: 'Completed' },
  { value: 'dead', label: 'Dead' },
]

export interface ClientFilters {
  status?: string
  vertical?: string
  source?: string
}

export interface CreateClientData {
  business_name: string
  vertical?: string | null
  employee_count?: number | null
  years_in_business?: number | null
  source: string
  referred_by?: string | null
  status?: string
  notes?: string | null
}

export interface UpdateClientData {
  business_name?: string
  vertical?: string | null
  employee_count?: number | null
  years_in_business?: number | null
  source?: string
  referred_by?: string | null
  status?: string
  notes?: string | null
}

/**
 * List clients for an organization with optional filters.
 */
export async function listClients(
  db: D1Database,
  orgId: string,
  filters?: ClientFilters
): Promise<Client[]> {
  const conditions: string[] = ['org_id = ?']
  const params: (string | number)[] = [orgId]

  if (filters?.status) {
    conditions.push('status = ?')
    params.push(filters.status)
  }

  if (filters?.vertical) {
    conditions.push('vertical = ?')
    params.push(filters.vertical)
  }

  if (filters?.source) {
    conditions.push('source = ?')
    params.push(filters.source)
  }

  const where = conditions.join(' AND ')
  const sql = `SELECT * FROM clients WHERE ${where} ORDER BY updated_at DESC`

  const result = await db
    .prepare(sql)
    .bind(...params)
    .all<Client>()
  return result.results
}

/**
 * Get a single client by ID, scoped to an organization.
 */
export async function getClient(
  db: D1Database,
  orgId: string,
  clientId: string
): Promise<Client | null> {
  const result = await db
    .prepare('SELECT * FROM clients WHERE id = ? AND org_id = ?')
    .bind(clientId, orgId)
    .first<Client>()

  return result ?? null
}

/**
 * Create a new client. Returns the created client record.
 */
export async function createClient(
  db: D1Database,
  orgId: string,
  data: CreateClientData
): Promise<Client> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO clients (id, org_id, business_name, vertical, employee_count, years_in_business, source, referred_by, status, notes, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      orgId,
      data.business_name,
      data.vertical ?? null,
      data.employee_count ?? null,
      data.years_in_business ?? null,
      data.source,
      data.referred_by ?? null,
      data.status ?? 'prospect',
      data.notes ?? null,
      now,
      now
    )
    .run()

  // Return the created record
  const client = await getClient(db, orgId, id)
  if (!client) {
    throw new Error('Failed to retrieve created client')
  }
  return client
}

/**
 * Update an existing client. Returns the updated client record.
 */
export async function updateClient(
  db: D1Database,
  orgId: string,
  clientId: string,
  data: UpdateClientData
): Promise<Client | null> {
  // Verify client exists and belongs to org
  const existing = await getClient(db, orgId, clientId)
  if (!existing) {
    return null
  }

  const fields: string[] = []
  const params: (string | number | null)[] = []

  if (data.business_name !== undefined) {
    fields.push('business_name = ?')
    params.push(data.business_name)
  }

  if (data.vertical !== undefined) {
    fields.push('vertical = ?')
    params.push(data.vertical)
  }

  if (data.employee_count !== undefined) {
    fields.push('employee_count = ?')
    params.push(data.employee_count)
  }

  if (data.years_in_business !== undefined) {
    fields.push('years_in_business = ?')
    params.push(data.years_in_business)
  }

  if (data.source !== undefined) {
    fields.push('source = ?')
    params.push(data.source)
  }

  if (data.referred_by !== undefined) {
    fields.push('referred_by = ?')
    params.push(data.referred_by)
  }

  if (data.status !== undefined) {
    fields.push('status = ?')
    params.push(data.status)
  }

  if (data.notes !== undefined) {
    fields.push('notes = ?')
    params.push(data.notes)
  }

  if (fields.length === 0) {
    return existing
  }

  fields.push("updated_at = datetime('now')")

  const sql = `UPDATE clients SET ${fields.join(', ')} WHERE id = ? AND org_id = ?`
  params.push(clientId, orgId)

  await db
    .prepare(sql)
    .bind(...params)
    .run()

  return getClient(db, orgId, clientId)
}

/**
 * Get distinct source values for an organization (for filter dropdowns).
 */
export async function listClientSources(db: D1Database, orgId: string): Promise<string[]> {
  const result = await db
    .prepare(
      'SELECT DISTINCT source FROM clients WHERE org_id = ? AND source IS NOT NULL ORDER BY source'
    )
    .bind(orgId)
    .all<{ source: string }>()

  return result.results.map((r) => r.source)
}
