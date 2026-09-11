/**
 * SOW lifecycle orchestration.
 *
 * Wave 1 deliberately stays concrete:
 * - SOW revisions only
 * - SignWell only
 * - explicit send authorization
 */

import { buildAppUrl } from '../config/app-url'
import type { Quote } from '../db/quotes'
import { getMissingAuthoredContent } from '../db/quote-content'
import type { SOWTemplateProps } from '../pdf/sow-template'
import {
  createSOWSendAuthorization,
  createSignatureRequest,
  getLatestDownloadableSOWRevisionForQuote,
  getLatestRenderableSOWRevisionForQuote,
  getLatestSOWRevisionForQuote,
  getOpenSignatureRequestForQuote,
  getSOWRevision,
  getLatestSignatureRequestForQuote,
  supersedeRenderedSOWRevisionsForQuote,
  type SOWRevision,
  type SignatureRequest,
} from './store'
import { getPdf, getSowRevisionUnsignedKey, uploadSowRevisionPdf } from '../storage/r2'
import { createSignatureRequest as createSignWellRequest } from '../signwell/client'
import type { SignWellCreateDocumentRequest } from '../signwell/types'
export { finalizeCompletedSOWSignature } from './service-finalize'

export interface SOWState {
  latestRevision: SOWRevision | null
  latestSignatureRequest: SignatureRequest | null
  openSignatureRequest: SignatureRequest | null
  downloadableRevision: SOWRevision | null
}

export interface SendSignerSnapshot {
  contactId: string
  name: string
  email: string
  title: string | null
}

export async function getSOWStateForQuote(
  db: D1Database,
  orgId: string,
  quoteId: string
): Promise<SOWState> {
  const [latestRevision, latestSignatureRequest, openSignatureRequest, downloadableRevision] =
    await Promise.all([
      getLatestSOWRevisionForQuote(db, orgId, quoteId),
      getLatestSignatureRequestForQuote(db, orgId, quoteId),
      getOpenSignatureRequestForQuote(db, orgId, quoteId),
      getLatestDownloadableSOWRevisionForQuote(db, orgId, quoteId),
    ])

  return {
    latestRevision,
    latestSignatureRequest,
    openSignatureRequest,
    downloadableRevision,
  }
}

export async function createSOWRevisionForQuote(args: {
  db: D1Database
  storage: R2Bucket
  orgId: string
  quote: Quote
  actorId: string
  templateProps: SOWTemplateProps
}): Promise<SOWRevision> {
  const { db, storage, orgId, quote, actorId, templateProps } = args
  const renderedAt = new Date().toISOString()
  const sowNumber = await generateNextSowNumber(db, orgId, renderedAt)
  const resolvedTemplateProps: SOWTemplateProps = {
    ...templateProps,
    document: {
      ...templateProps.document,
      sowNumber,
    },
  }
  const { renderSow } = await import('../pdf/render')
  const pdf = await renderSow(resolvedTemplateProps)
  const checksum = await sha256Hex(pdf)
  const revisionId = crypto.randomUUID()
  const unsignedStorageKey = getSowRevisionUnsignedKey(orgId, quote.id, revisionId)

  await uploadSowRevisionPdf(storage, unsignedStorageKey, pdf, {
    quoteId: quote.id,
    revisionId,
    renderedAt,
  })

  await db
    .prepare(
      `INSERT INTO sow_revisions (
        id, org_id, quote_id, quote_version, sow_number, status,
        unsigned_storage_key, checksum_sha256, rendered_by, rendered_at,
        metadata_json, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, 'rendered', ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      revisionId,
      orgId,
      quote.id,
      quote.version,
      sowNumber,
      unsignedStorageKey,
      checksum,
      actorId,
      renderedAt,
      JSON.stringify({ template: 'sow', quoteVersion: quote.version }),
      renderedAt,
      renderedAt
    )
    .run()

  await supersedeRenderedSOWRevisionsForQuote(db, orgId, quote.id, revisionId, renderedAt)

  const revision = await getSOWRevision(db, orgId, revisionId)
  if (!revision) {
    throw new Error('Failed to retrieve created SOW revision')
  }
  return revision
}

async function validateSendPreconditions(
  db: D1Database,
  orgId: string,
  quote: Quote,
  storage: R2Bucket
): Promise<{ revision: SOWRevision; pdfBase64: string }> {
  const openRequest = await getOpenSignatureRequestForQuote(db, orgId, quote.id)
  if (openRequest) throw new Error('SOW already sent for signature.')

  // Send-gating: a draft quote must have authored schedule + deliverables
  // before it can be sent for signature. Without these the proposal page
  // would render an empty "How we'll work" / deliverables surface or (pre-#377)
  // synthesize fabricated commitments. Mirrors the guard in updateQuoteStatus.
  if (quote.status === 'draft') {
    const missing = getMissingAuthoredContent(quote)
    if (missing.length > 0) {
      throw new Error(
        `Cannot send quote for signature: missing authored client-facing content (${missing.join(', ')}). Author the schedule and deliverables in the quote builder before sending.`
      )
    }
  }

  const revision = await getLatestRenderableSOWRevisionForQuote(db, orgId, quote.id)
  if (!revision) throw new Error('Generate a SOW PDF first.')
  if (revision.quote_version !== quote.version)
    throw new Error('The latest SOW revision is stale. Re-generate the PDF before sending.')

  const pdfObject = await getPdf(storage, revision.unsigned_storage_key)
  if (!pdfObject) throw new Error(`SOW PDF not found in storage: ${revision.unsigned_storage_key}`)

  const pdfBuffer = await pdfObject.arrayBuffer()
  return { revision, pdfBase64: uint8ArrayToBase64(new Uint8Array(pdfBuffer)) }
}

// Field placement is handled by SignWell text tags embedded in the PDF
// template (see src/lib/pdf/sow-template.tsx — {{s:1}} / {{d:1}} markers).
// We do NOT send a fields[] array; SignWell parses the tags and places
// the fields when text_tags is true. This keeps field positions and
// template layout in lockstep by construction.
function buildSignWellRequest(args: {
  entityName: string
  pdfBase64: string
  signer: SendSignerSnapshot
  callbackUrl: string
  customRequesterName: string
}): SignWellCreateDocumentRequest {
  const { entityName, pdfBase64, signer, callbackUrl, customRequesterName } = args
  return {
    name: `SOW — ${entityName}`,
    files: [{ file_base64: pdfBase64, name: 'sow.pdf' }],
    recipients: [{ id: crypto.randomUUID(), name: signer.name, email: signer.email }],
    callback_url: callbackUrl,
    text_tags: true,
    draft: false,
    custom_requester_name: customRequesterName,
    subject: `SOW for Signature — ${entityName}`,
    message: `Hi ${signer.name}, please review and sign the attached Statement of Work. If you have any questions, reply directly to this email.`,
  }
}

async function recordSentSignatureRequest(args: {
  db: D1Database
  orgId: string
  quote: Quote
  revisionId: string
  authorizationId: string
  providerRequestId: string
  signerSnapshot: string
  signRequest: SignWellCreateDocumentRequest
}): Promise<SignatureRequest> {
  const {
    db,
    orgId,
    quote,
    revisionId,
    authorizationId,
    providerRequestId,
    signerSnapshot,
    signRequest,
  } = args
  const sentAt = new Date().toISOString()
  const expiresAt = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString()
  const request = await createSignatureRequest(db, {
    org_id: orgId,
    quote_id: quote.id,
    sow_revision_id: revisionId,
    send_authorization_id: authorizationId,
    provider_request_id: providerRequestId,
    status: 'sent',
    signer_snapshot_json: signerSnapshot,
    provider_payload_json: JSON.stringify(signRequest),
    sent_at: sentAt,
  })
  await db.batch([
    db
      .prepare(
        `UPDATE sow_revisions SET status = 'sent', updated_at = ? WHERE id = ? AND org_id = ?`
      )
      .bind(sentAt, revisionId, orgId),
    db
      .prepare(
        `UPDATE quotes SET status = CASE WHEN status = 'draft' THEN 'sent' ELSE status END, sent_at = CASE WHEN sent_at IS NULL THEN ? ELSE sent_at END, expires_at = CASE WHEN expires_at IS NULL THEN ? ELSE expires_at END, updated_at = ? WHERE id = ? AND org_id = ?`
      )
      .bind(sentAt, expiresAt, sentAt, quote.id, orgId),
  ])
  return request
}

export async function authorizeAndSendSOW(args: {
  db: D1Database
  storage: R2Bucket
  apiKey: string
  orgId: string
  actorId: string
  quote: Quote
  entityName: string
  signer: SendSignerSnapshot
  customRequesterName?: string
  callbackBaseEnv: Cloudflare.Env
}): Promise<SignatureRequest> {
  const { db, storage, apiKey, orgId, actorId, quote, entityName, signer, callbackBaseEnv } = args
  const customRequesterName = args.customRequesterName ?? 'SMD Services'
  const { revision, pdfBase64 } = await validateSendPreconditions(db, orgId, quote, storage)
  const now = new Date().toISOString()
  const signerSnapshot = JSON.stringify({
    contactId: signer.contactId,
    name: signer.name,
    email: signer.email,
    title: signer.title,
  })

  const authorization = await createSOWSendAuthorization(db, {
    org_id: orgId,
    quote_id: quote.id,
    sow_revision_id: revision.id,
    signer_contact_id: signer.contactId,
    signer_snapshot_json: signerSnapshot,
    checksum_sha256: revision.checksum_sha256,
    authorized_by: actorId,
    authorized_at: now,
  })

  const callbackUrl = buildAppUrl(callbackBaseEnv, '/api/webhooks/signwell')
  const signRequest = buildSignWellRequest({
    entityName,
    pdfBase64,
    signer,
    callbackUrl,
    customRequesterName,
  })

  try {
    const signwellDoc = await createSignWellRequest(apiKey, signRequest)
    return recordSentSignatureRequest({
      db,
      orgId,
      quote,
      revisionId: revision.id,
      authorizationId: authorization.id,
      providerRequestId: signwellDoc.id,
      signerSnapshot,
      signRequest,
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    await createSignatureRequest(db, {
      org_id: orgId,
      quote_id: quote.id,
      sow_revision_id: revision.id,
      send_authorization_id: authorization.id,
      status: 'send_failed',
      signer_snapshot_json: signerSnapshot,
      provider_payload_json: JSON.stringify(signRequest),
      failure_reason: message,
    })
    throw err
  }
}

async function generateNextSowNumber(
  db: D1Database,
  orgId: string,
  renderedAt: string
): Promise<string> {
  const yearMonth = renderedAt.slice(0, 4) + renderedAt.slice(5, 7)
  for (let attempt = 0; attempt < 5; attempt++) {
    const row = await db
      .prepare(
        `SELECT COUNT(*) AS count
         FROM sow_revisions
         WHERE org_id = ? AND substr(rendered_at, 1, 7) = ?`
      )
      .bind(orgId, `${renderedAt.slice(0, 7)}`)
      .first<{ count: number }>()
    const next = String((row?.count ?? 0) + 1 + attempt).padStart(3, '0')
    const candidate = `SOW-${yearMonth}-${next}`
    const existing = await db
      .prepare('SELECT 1 FROM sow_revisions WHERE org_id = ? AND sow_number = ? LIMIT 1')
      .bind(orgId, candidate)
      .first()
    if (!existing) return candidate
  }
  throw new Error('Failed to generate unique SOW number')
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes.slice())
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

function uint8ArrayToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}
