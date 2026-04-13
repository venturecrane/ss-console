/**
 * SOW revision lifecycle storage helpers.
 *
 * Wave 1 keeps these primitives SOW-specific on purpose. They model:
 * - immutable rendered SOW revisions
 * - explicit send authorization
 * - provider request tracking
 * - outbox jobs for post-acceptance integrations
 */

export type SOWRevisionStatus = 'rendered' | 'sent' | 'superseded' | 'signed'

export interface SOWRevision {
  id: string
  org_id: string
  quote_id: string
  quote_version: number
  sow_number: string
  status: SOWRevisionStatus
  unsigned_storage_key: string
  signed_storage_key: string | null
  checksum_sha256: string
  rendered_by: string
  rendered_at: string
  signed_at: string | null
  superseded_at: string | null
  metadata_json: string | null
  created_at: string
  updated_at: string
}

export interface CreateSOWRevisionData {
  org_id: string
  quote_id: string
  quote_version: number
  sow_number: string
  unsigned_storage_key: string
  checksum_sha256: string
  rendered_by: string
  rendered_at: string
  metadata_json?: string | null
}

export interface SOWSendAuthorization {
  id: string
  org_id: string
  quote_id: string
  sow_revision_id: string
  signer_contact_id: string
  signer_snapshot_json: string
  checksum_sha256: string
  authorized_by: string
  authorized_at: string
  created_at: string
}

export interface CreateSOWSendAuthorizationData {
  org_id: string
  quote_id: string
  sow_revision_id: string
  signer_contact_id: string
  signer_snapshot_json: string
  checksum_sha256: string
  authorized_by: string
  authorized_at: string
}

export type SignatureRequestStatus =
  | 'send_failed'
  | 'sent'
  | 'completed_pending_artifact'
  | 'completed'
  | 'declined'
  | 'expired'

export interface SignatureRequest {
  id: string
  org_id: string
  quote_id: string
  sow_revision_id: string
  send_authorization_id: string
  provider: 'signwell'
  provider_request_id: string | null
  status: SignatureRequestStatus
  signer_snapshot_json: string
  provider_payload_json: string
  signed_storage_key: string | null
  sent_at: string | null
  completed_at: string | null
  declined_at: string | null
  expired_at: string | null
  webhook_last_at: string | null
  failure_reason: string | null
  created_at: string
  updated_at: string
}

export interface CreateSignatureRequestData {
  org_id: string
  quote_id: string
  sow_revision_id: string
  send_authorization_id: string
  provider_request_id?: string | null
  status: SignatureRequestStatus
  signer_snapshot_json: string
  provider_payload_json: string
  sent_at?: string | null
  completed_at?: string | null
  declined_at?: string | null
  expired_at?: string | null
  webhook_last_at?: string | null
  failure_reason?: string | null
}

export type OutboxJobType = 'send_sow_signed_email' | 'send_deposit_invoice'
export type OutboxJobStatus = 'pending' | 'processing' | 'completed' | 'failed'

export interface OutboxJob {
  id: string
  org_id: string
  signature_request_id: string
  type: OutboxJobType
  status: OutboxJobStatus
  dedupe_key: string
  payload_json: string
  attempt_count: number
  available_at: string
  last_error: string | null
  created_at: string
  updated_at: string
}

export interface CreateOutboxJobData {
  org_id: string
  signature_request_id: string
  type: OutboxJobType
  dedupe_key: string
  payload_json: string
  available_at: string
}

export async function getSOWRevision(
  db: D1Database,
  orgId: string,
  revisionId: string
): Promise<SOWRevision | null> {
  return (
    (await db
      .prepare('SELECT * FROM sow_revisions WHERE id = ? AND org_id = ?')
      .bind(revisionId, orgId)
      .first<SOWRevision>()) ?? null
  )
}

export async function getLatestSOWRevisionForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<SOWRevision | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM sow_revisions
         WHERE org_id = ? AND quote_id = ?
         ORDER BY created_at DESC
         LIMIT 1`
      )
      .bind(orgId, quoteId)
      .first<SOWRevision>()) ?? null
  )
}

export async function getLatestRenderableSOWRevisionForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<SOWRevision | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM sow_revisions
         WHERE org_id = ? AND quote_id = ? AND status = 'rendered'
         ORDER BY created_at DESC
         LIMIT 1`
      )
      .bind(orgId, quoteId)
      .first<SOWRevision>()) ?? null
  )
}

export async function getLatestDownloadableSOWRevisionForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<SOWRevision | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM sow_revisions
         WHERE org_id = ? AND quote_id = ? AND status IN ('signed', 'sent', 'rendered')
         ORDER BY
           CASE status
             WHEN 'signed' THEN 0
             WHEN 'sent' THEN 1
             ELSE 2
           END,
           created_at DESC
         LIMIT 1`
      )
      .bind(orgId, quoteId)
      .first<SOWRevision>()) ?? null
  )
}

export async function createSOWRevision(
  db: D1Database,
  data: CreateSOWRevisionData
): Promise<SOWRevision> {
  const id = crypto.randomUUID()
  const now = data.rendered_at

  await db
    .prepare(
      `INSERT INTO sow_revisions (
        id, org_id, quote_id, quote_version, sow_number, status,
        unsigned_storage_key, checksum_sha256, rendered_by, rendered_at,
        metadata_json, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, 'rendered', ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.org_id,
      data.quote_id,
      data.quote_version,
      data.sow_number,
      data.unsigned_storage_key,
      data.checksum_sha256,
      data.rendered_by,
      data.rendered_at,
      data.metadata_json ?? null,
      now,
      now
    )
    .run()

  const revision = await getSOWRevision(db, data.org_id, id)
  if (!revision) {
    throw new Error('Failed to retrieve created SOW revision')
  }
  return revision
}

export async function supersedeRenderedSOWRevisionsForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string,
  keepRevisionId: string,
  supersededAt: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE sow_revisions
       SET status = 'superseded', superseded_at = ?, updated_at = ?
       WHERE org_id = ? AND quote_id = ? AND id != ? AND status = 'rendered'`
    )
    .bind(supersededAt, supersededAt, orgId, quoteId, keepRevisionId)
    .run()
}

export async function createSOWSendAuthorization(
  db: D1Database,
  data: CreateSOWSendAuthorizationData
): Promise<SOWSendAuthorization> {
  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO sow_send_authorizations (
        id, org_id, quote_id, sow_revision_id, signer_contact_id,
        signer_snapshot_json, checksum_sha256, authorized_by, authorized_at, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.org_id,
      data.quote_id,
      data.sow_revision_id,
      data.signer_contact_id,
      data.signer_snapshot_json,
      data.checksum_sha256,
      data.authorized_by,
      data.authorized_at,
      data.authorized_at
    )
    .run()

  const row = await db
    .prepare('SELECT * FROM sow_send_authorizations WHERE id = ? AND org_id = ?')
    .bind(id, data.org_id)
    .first<SOWSendAuthorization>()

  if (!row) {
    throw new Error('Failed to retrieve created SOW send authorization')
  }
  return row
}

export async function createSignatureRequest(
  db: D1Database,
  data: CreateSignatureRequestData
): Promise<SignatureRequest> {
  const id = crypto.randomUUID()
  const now = data.sent_at ?? data.completed_at ?? data.webhook_last_at ?? new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO signature_requests (
        id, org_id, quote_id, sow_revision_id, send_authorization_id, provider,
        provider_request_id, status, signer_snapshot_json, provider_payload_json,
        sent_at, completed_at, declined_at, expired_at, webhook_last_at, failure_reason,
        created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, 'signwell', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.org_id,
      data.quote_id,
      data.sow_revision_id,
      data.send_authorization_id,
      data.provider_request_id ?? null,
      data.status,
      data.signer_snapshot_json,
      data.provider_payload_json,
      data.sent_at ?? null,
      data.completed_at ?? null,
      data.declined_at ?? null,
      data.expired_at ?? null,
      data.webhook_last_at ?? null,
      data.failure_reason ?? null,
      now,
      now
    )
    .run()

  const request = await db
    .prepare('SELECT * FROM signature_requests WHERE id = ? AND org_id = ?')
    .bind(id, data.org_id)
    .first<SignatureRequest>()

  if (!request) {
    throw new Error('Failed to retrieve created signature request')
  }
  return request
}

export async function getSignatureRequestByProviderRequestId(
  db: D1Database,
  provider: 'signwell',
  providerRequestId: string
): Promise<SignatureRequest | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM signature_requests
         WHERE provider = ? AND provider_request_id = ?
         LIMIT 1`
      )
      .bind(provider, providerRequestId)
      .first<SignatureRequest>()) ?? null
  )
}

export async function getLatestSignatureRequestForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<SignatureRequest | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM signature_requests
         WHERE org_id = ? AND quote_id = ?
         ORDER BY created_at DESC
         LIMIT 1`
      )
      .bind(orgId, quoteId)
      .first<SignatureRequest>()) ?? null
  )
}

export async function getOpenSignatureRequestForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<SignatureRequest | null> {
  return (
    (await db
      .prepare(
        `SELECT * FROM signature_requests
         WHERE org_id = ? AND quote_id = ?
           AND status = 'sent'
         ORDER BY created_at DESC
         LIMIT 1`
      )
      .bind(orgId, quoteId)
      .first<SignatureRequest>()) ?? null
  )
}

export async function createOutboxJob(
  db: D1Database,
  data: CreateOutboxJobData
): Promise<OutboxJob> {
  const id = crypto.randomUUID()
  const now = new Date().toISOString()

  await db
    .prepare(
      `INSERT INTO outbox_jobs (
        id, org_id, signature_request_id, type, status, dedupe_key,
        payload_json, available_at, created_at, updated_at
      ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      data.org_id,
      data.signature_request_id,
      data.type,
      data.dedupe_key,
      data.payload_json,
      data.available_at,
      now,
      now
    )
    .run()

  const job = await db
    .prepare('SELECT * FROM outbox_jobs WHERE id = ? AND org_id = ?')
    .bind(id, data.org_id)
    .first<OutboxJob>()

  if (!job) {
    throw new Error('Failed to retrieve created outbox job')
  }
  return job
}

export async function listOutboxJobsForSignatureRequest(
  db: D1Database,
  orgId: string,
  signatureRequestId: string
): Promise<OutboxJob[]> {
  const result = await db
    .prepare(
      `SELECT * FROM outbox_jobs
       WHERE org_id = ? AND signature_request_id = ?
       ORDER BY created_at ASC`
    )
    .bind(orgId, signatureRequestId)
    .all<OutboxJob>()
  return result.results
}

export async function isQuoteAcceptanceReady(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT 1
       FROM signature_requests
       WHERE org_id = ? AND quote_id = ? AND status = 'completed' AND signed_storage_key IS NOT NULL
       LIMIT 1`
    )
    .bind(orgId, quoteId)
    .first<{ 1: number }>()
  return !!row
}
