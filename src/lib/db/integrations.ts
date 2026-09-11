/**
 * Integration data access layer.
 *
 * Manages third-party OAuth credentials stored in the `integrations` table.
 * Google Calendar is the only provider in v1; the schema is generic for
 * future providers (Gmail, HubSpot, etc.).
 *
 * Refresh tokens are stored encrypted (AES-GCM) — see `src/lib/security/encryption.ts`.
 * All queries are parameterized to prevent SQL injection.
 */

import { decrypt } from '../security/encryption.js'

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
  status: 'active' | 'revoked' | 'error'
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface UpsertIntegrationData {
  org_id: string
  provider: string
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
 * Get an integration by provider. By default returns only active integrations
 * (for operational use). Pass `includeInactive: true` for health monitoring
 * to surface error/revoked states on the dashboard.
 */
export async function getIntegration(
  db: D1Database,
  orgId: string,
  provider: string,
  opts?: { includeInactive?: boolean }
): Promise<Integration | null> {
  const statusFilter = opts?.includeInactive ? '' : "AND status = 'active'"
  return (
    (await db
      .prepare(
        `SELECT * FROM integrations
         WHERE org_id = ? AND provider = ? ${statusFilter}
         ORDER BY updated_at DESC LIMIT 1`
      )
      .bind(orgId, provider)
      .first<Integration>()) ?? null
  )
}

// ---------------------------------------------------------------------------
// Upsert
// ---------------------------------------------------------------------------

/**
 * Insert or update an integration record. Uses the UNIQUE(org_id, provider,
 * account_email) constraint to handle reconnects gracefully.
 */
export async function upsertIntegration(
  db: D1Database,
  data: UpsertIntegrationData
): Promise<Integration> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO integrations (
        id, org_id, provider, account_email, account_id, calendar_id,
        scopes, refresh_token_ciphertext, access_token, access_expires_at,
        status, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))
      ON CONFLICT(org_id, provider, account_email) DO UPDATE SET
        account_id = excluded.account_id,
        scopes = excluded.scopes,
        refresh_token_ciphertext = excluded.refresh_token_ciphertext,
        access_token = excluded.access_token,
        access_expires_at = excluded.access_expires_at,
        status = 'active',
        last_error = NULL,
        updated_at = datetime('now')`
    )
    .bind(
      id,
      data.org_id,
      data.provider,
      data.account_email,
      data.account_id ?? null,
      data.calendar_id ?? 'primary',
      data.scopes,
      data.refresh_token_ciphertext,
      data.access_token ?? null,
      data.access_expires_at ?? null
    )
    .run()

  const integration = await getIntegration(db, data.org_id, data.provider)
  if (!integration) throw new Error('Failed to retrieve upserted integration')
  return integration
}

// ---------------------------------------------------------------------------
// Update cached access token
// ---------------------------------------------------------------------------

async function updateAccessToken(
  db: D1Database,
  integrationId: string,
  accessToken: string,
  expiresAt: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE integrations SET
        access_token = ?, access_expires_at = ?, updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(accessToken, expiresAt, integrationId)
    .run()
}

// ---------------------------------------------------------------------------
// Update status (revoke / error)
// ---------------------------------------------------------------------------

async function updateIntegrationStatus(
  db: D1Database,
  integrationId: string,
  status: 'active' | 'revoked' | 'error',
  lastError?: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE integrations SET
        status = ?, last_error = ?, updated_at = datetime('now')
      WHERE id = ?`
    )
    .bind(status, lastError ?? null, integrationId)
    .run()
}

// ---------------------------------------------------------------------------
// Delete (hard delete for disconnect)
// ---------------------------------------------------------------------------

export async function deleteIntegration(
  db: D1Database,
  orgId: string,
  integrationId: string
): Promise<void> {
  await db
    .prepare('DELETE FROM integrations WHERE id = ? AND org_id = ?')
    .bind(integrationId, orgId)
    .run()
}

// ---------------------------------------------------------------------------
// Google access token orchestration (decrypt + refresh + cache)
// ---------------------------------------------------------------------------

export interface GoogleTokenEnv {
  GOOGLE_CLIENT_ID?: string
  GOOGLE_CLIENT_SECRET?: string
  BOOKING_ENCRYPTION_KEY?: string
}

/** Mark an integration as revoked if the response indicates invalid_grant. */
async function maybeMarkRevoked(
  db: D1Database,
  integrationId: string,
  status: number,
  body: string
): Promise<void> {
  if (status !== 400 && status !== 401) return
  try {
    const parsed = JSON.parse(body) as { error?: string }
    if (parsed.error === 'invalid_grant') {
      await updateIntegrationStatus(db, integrationId, 'revoked', 'Refresh token revoked by Google')
    }
  } catch {
    // JSON parse failed — leave status as-is
  }
}

/**
 * Get a valid Google access token for the integration. Refreshes if the
 * cached token is expired or about to expire (within 5 minutes).
 *
 * Returns the access token string, or null if refresh fails (caller should
 * treat as integration unavailable and return email fallback).
 */
export async function getGoogleAccessToken(
  db: D1Database,
  integration: Integration,
  env: GoogleTokenEnv
): Promise<string | null> {
  // Check if cached access token is still valid (>5 min remaining)
  if (integration.access_token && integration.access_expires_at) {
    const expiresAt = new Date(integration.access_expires_at).getTime()
    const bufferMs = 5 * 60_000
    if (Date.now() + bufferMs < expiresAt) {
      return integration.access_token
    }
  }

  // Need to refresh — validate env vars
  if (!env.BOOKING_ENCRYPTION_KEY || !env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
    console.error('[integrations] Missing Google OAuth env vars for token refresh')
    return null
  }

  // Decrypt the refresh token
  let refreshToken: string
  try {
    refreshToken = await decrypt(env.BOOKING_ENCRYPTION_KEY, integration.refresh_token_ciphertext)
  } catch (err) {
    console.error('[integrations] Failed to decrypt refresh token:', err)
    await updateIntegrationStatus(db, integration.id, 'error', 'Failed to decrypt refresh token')
    return null
  }

  // Call Google's token endpoint
  try {
    const response = await fetch('https://oauth2.googleapis.com/token', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: env.GOOGLE_CLIENT_ID,
        client_secret: env.GOOGLE_CLIENT_SECRET,
        refresh_token: refreshToken,
        grant_type: 'refresh_token',
      }),
    })

    if (!response.ok) {
      const body = await response.text()
      // Status and size only: the upstream body can echo the request (client
      // id, a token fragment) and the Worker log is not a place for it.
      console.error(
        `[integrations] Google token refresh failed: ${response.status} (${body.length} bytes)`
      )
      await maybeMarkRevoked(db, integration.id, response.status, body)
      return null
    }

    const data: { access_token: string; expires_in: number } = await response.json()

    const expiresAt = new Date(Date.now() + data.expires_in * 1000).toISOString()

    // Cache the new access token in the DB
    await updateAccessToken(db, integration.id, data.access_token, expiresAt)

    return data.access_token
  } catch (err) {
    console.error('[integrations] Google token refresh network error:', err)
    return null
  }
}
