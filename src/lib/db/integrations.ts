/**
 * Integration data access layer.
 *
 * Stores OAuth credentials for third-party services (Google Calendar in v1).
 * Refresh tokens are encrypted at rest via BOOKING_ENCRYPTION_KEY (AES-GCM).
 *
 * All queries are parameterized to prevent SQL injection.
 * Primary keys use crypto.randomUUID().
 * Dedup enforced via UNIQUE(org_id, provider, account_email).
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Integration {
  id: string
  org_id: string
  provider: string
  account_email: string
  account_id: string | null
  calendar_id: string
  scopes: string
  refresh_token_ciphertext: string
  access_token: string | null
  access_expires_at: string | null
  status: IntegrationStatus
  last_error: string | null
  created_at: string
  updated_at: string
}

export type IntegrationStatus = 'active' | 'revoked' | 'error'

export interface UpsertGoogleIntegrationInput {
  org_id: string
  account_email: string
  account_id?: string | null
  calendar_id?: string
  scopes: string
  refresh_token_ciphertext: string
  access_token?: string | null
  access_expires_at?: string | null
}

// ---------------------------------------------------------------------------
// Read
// ---------------------------------------------------------------------------

/**
 * Get the active Google Calendar integration for an org.
 * Returns null if no active integration exists.
 */
export async function getActiveGoogleIntegration(
  db: D1Database,
  orgId: string
): Promise<Integration | null> {
  const result = await db
    .prepare(
      `SELECT * FROM integrations
       WHERE org_id = ? AND provider = 'google_calendar' AND status = 'active'
       LIMIT 1`
    )
    .bind(orgId)
    .first<Integration>()

  return result ?? null
}

// ---------------------------------------------------------------------------
// Upsert
// ---------------------------------------------------------------------------

/**
 * Create or update a Google Calendar integration.
 * Uses the UNIQUE(org_id, provider, account_email) constraint to upsert.
 */
export async function upsertGoogleIntegration(
  db: D1Database,
  input: UpsertGoogleIntegrationInput
): Promise<Integration> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO integrations (
        id, org_id, provider, account_email, account_id, calendar_id,
        scopes, refresh_token_ciphertext, access_token, access_expires_at,
        status, created_at, updated_at
      ) VALUES (?, ?, 'google_calendar', ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
      ON CONFLICT(org_id, provider, account_email) DO UPDATE SET
        account_id = excluded.account_id,
        calendar_id = excluded.calendar_id,
        scopes = excluded.scopes,
        refresh_token_ciphertext = excluded.refresh_token_ciphertext,
        access_token = excluded.access_token,
        access_expires_at = excluded.access_expires_at,
        status = 'active',
        last_error = NULL,
        updated_at = excluded.updated_at`
    )
    .bind(
      id,
      input.org_id,
      input.account_email,
      input.account_id ?? null,
      input.calendar_id ?? 'primary',
      input.scopes,
      input.refresh_token_ciphertext,
      input.access_token ?? null,
      input.access_expires_at ?? null,
      now,
      now
    )
    .run()

  // Retrieve the row — may be the inserted or the updated one
  const integration = await db
    .prepare(
      `SELECT * FROM integrations
       WHERE org_id = ? AND provider = 'google_calendar' AND account_email = ?`
    )
    .bind(input.org_id, input.account_email)
    .first<Integration>()

  if (!integration) throw new Error('Failed to retrieve upserted integration')
  return integration
}

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------

/**
 * Update integration status (e.g. mark as revoked or error).
 */
export async function updateIntegrationStatus(
  db: D1Database,
  id: string,
  status: IntegrationStatus,
  lastError?: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE integrations
       SET status = ?, last_error = ?, updated_at = datetime('now')
       WHERE id = ?`
    )
    .bind(status, lastError ?? null, id)
    .run()
}

/**
 * Update the cached access token after a refresh grant.
 */
export async function updateAccessToken(
  db: D1Database,
  id: string,
  accessToken: string,
  expiresAt: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE integrations
       SET access_token = ?, access_expires_at = ?, updated_at = datetime('now')
       WHERE id = ?`
    )
    .bind(accessToken, expiresAt, id)
    .run()
}
