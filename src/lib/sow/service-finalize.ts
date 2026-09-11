/**
 * SOW signature completion and outbox processing.
 * Extracted from service.ts to keep that file under the 500-line ceiling.
 * All exports here are re-exported from service.ts for backward compatibility.
 */

import type { Quote } from '../db/quotes'
import {
  getSOWRevision,
  getSignatureRequestByProviderRequestId,
  type SignatureRequest,
} from './store'
import { getSowRevisionSignedKey, uploadSignedSowRevisionPdf } from '../storage/r2'
import { getSignedPdf } from '../signwell/client'
import type { SignWellWebhookPayload } from '../signwell/types'
import { normalizeEmail } from '../identity/email'

import {
  okResponse,
  unknownDocumentResponse,
  processOutboxJobsForSignatureRequest,
} from './outbox-jobs'

// prettier-ignore
interface SignerSnapshot { contactId: string; name: string; email: string; title: string | null }
// prettier-ignore
interface FinalizeCtx {
  db: D1Database; orgId: string; requestId: string; entityId: string; quoteId: string
  engagementId: string; serviceId: string; invoiceId: string; depositAmount: number; signer: SignerSnapshot
  now: string; signedKey: string; revisionId: string; totalHours: number
}

function buildOutboxStmts(ctx: FinalizeCtx): D1PreparedStatement[] {
  // prettier-ignore
  const { db, orgId, requestId, entityId, quoteId, engagementId, invoiceId, depositAmount, signer, now } = ctx
  const normalizedEmail = normalizeEmail(signer.email)
  return [
    db
      .prepare(
        `INSERT INTO outbox_jobs (id, org_id, signature_request_id, type, status, dedupe_key, payload_json, available_at, created_at, updated_at) VALUES (?, ?, ?, 'send_sow_signed_email', 'pending', ?, ?, ?, ?, ?)`
      )
      .bind(
        crypto.randomUUID(),
        orgId,
        requestId,
        `signature-email:${requestId}`,
        JSON.stringify({ signature_request_id: requestId, entity_id: entityId, quote_id: quoteId }),
        now,
        now,
        now
      ),
    db
      .prepare(
        `INSERT INTO outbox_jobs (id, org_id, signature_request_id, type, status, dedupe_key, payload_json, available_at, created_at, updated_at) VALUES (?, ?, ?, 'send_deposit_invoice', 'pending', ?, ?, ?, ?, ?)`
      )
      .bind(
        crypto.randomUUID(),
        orgId,
        requestId,
        `deposit-invoice:${requestId}`,
        JSON.stringify({
          signature_request_id: requestId,
          entity_id: entityId,
          quote_id: quoteId,
          engagement_id: engagementId,
          invoice_id: invoiceId,
          amount: depositAmount,
        }),
        now,
        now,
        now
      ),
    db
      .prepare(
        `INSERT INTO outbox_jobs (id, org_id, signature_request_id, type, status, dedupe_key, payload_json, available_at, created_at, updated_at) VALUES (?, ?, ?, 'send_portal_invitation', 'pending', ?, ?, ?, ?, ?)`
      )
      .bind(
        crypto.randomUUID(),
        orgId,
        requestId,
        `portal-invitation:${requestId}`,
        JSON.stringify({
          signature_request_id: requestId,
          entity_id: entityId,
          user_email: normalizedEmail,
          user_name: signer.name,
        }),
        now,
        now,
        now
      ),
  ]
}

// prettier-ignore
type CoreStmtIds = { contextEntryId: string; clientUserId: string; stageChangeContent: string; stageChangeMetadata: string; normalizedEmail: string }

function buildCoreStmts(ctx: FinalizeCtx, ids: CoreStmtIds): D1PreparedStatement[] {
  // prettier-ignore
  const { db, orgId, requestId, entityId, quoteId, engagementId, serviceId, invoiceId, signer, signedKey, revisionId, totalHours, now } = ctx
  // prettier-ignore
  const { contextEntryId, clientUserId, stageChangeContent, stageChangeMetadata, normalizedEmail } = ids
  return [
    db
      .prepare(
        `UPDATE signature_requests SET status = 'completed', signed_storage_key = ?, completed_at = COALESCE(completed_at, ?), webhook_last_at = ?, updated_at = ? WHERE id = ? AND org_id = ? AND status = 'completed_pending_artifact'`
      )
      .bind(signedKey, now, now, now, requestId, orgId),
    db
      .prepare(
        `UPDATE sow_revisions SET status = 'signed', signed_storage_key = ?, signed_at = ?, updated_at = ? WHERE id = ? AND org_id = ?`
      )
      .bind(signedKey, now, now, revisionId, orgId),
    db
      .prepare(
        `UPDATE quotes SET status = 'accepted', accepted_at = ?, updated_at = ? WHERE id = ? AND org_id = ? AND status = 'sent'`
      )
      .bind(now, now, quoteId, orgId),
    db
      .prepare(
        `UPDATE entities SET stage = 'engaged', stage_changed_at = ?, updated_at = ? WHERE id = ? AND org_id = ?`
      )
      .bind(now, now, entityId, orgId),
    // ADR 0046: the commercial `service` spine row (parent of the engagement).
    // Born 'active' — acceptance is the trigger and the revenue line is live at
    // signature. Inlined here (not via createService) to stay in the atomic
    // batch. All ids are JS-side constants, so statement order is immaterial.
    db
      .prepare(
        `INSERT INTO services (id, org_id, entity_id, quote_id, type, cadence, status, started_at, created_at, updated_at) VALUES (?, ?, ?, ?, 'consulting', 'one_time', 'active', ?, ?, ?)`
      )
      .bind(serviceId, orgId, entityId, quoteId, now, now, now),
    db
      .prepare(
        `INSERT INTO engagements (id, org_id, entity_id, quote_id, service_id, status, estimated_hours, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'scheduled', ?, ?, ?)`
      )
      .bind(engagementId, orgId, entityId, quoteId, serviceId, totalHours, now, now),
    db
      .prepare(
        `INSERT INTO invoices (id, org_id, engagement_id, entity_id, type, amount, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'deposit', ?, 'draft', ?, ?)`
      )
      .bind(invoiceId, orgId, engagementId, entityId, ctx.depositAmount, now, now),
    db
      .prepare(
        `INSERT INTO context (id, entity_id, org_id, type, content, source, content_size, metadata, created_at) VALUES (?, ?, ?, 'stage_change', ?, 'signwell-webhook', ?, ?, ?)`
      )
      .bind(
        contextEntryId,
        entityId,
        orgId,
        stageChangeContent,
        stageChangeContent.length,
        stageChangeMetadata,
        now
      ),
    db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id, created_at) VALUES (?, ?, ?, ?, 'client', ?, ?) ON CONFLICT(org_id, email) DO UPDATE SET entity_id = COALESCE(users.entity_id, excluded.entity_id)`
      )
      .bind(clientUserId, orgId, normalizedEmail, signer.name, entityId, now),
  ]
}

function buildFinalizationBatch(
  ctx: FinalizeCtx,
  lineItems: Array<{ problem: string; description: string }>
): D1PreparedStatement[] {
  const { db, orgId, requestId, quoteId, engagementId, signer, now } = ctx
  const milestoneIds = lineItems.map(() => crypto.randomUUID())
  const clientUserId = crypto.randomUUID()
  const contextEntryId = crypto.randomUUID()
  const normalizedEmail = normalizeEmail(signer.email)
  const stageChangeContent = 'Stage: proposing -> engaged. SOW signed via SignWell.'
  const stageChangeMetadata = JSON.stringify({
    from: 'proposing',
    to: 'engaged',
    reason: 'SOW signed via SignWell',
    quote_id: quoteId,
    engagement_id: engagementId,
    signature_request_id: requestId,
  })
  const milestoneStmts = lineItems.map((item, i) =>
    db
      .prepare(
        `INSERT INTO milestones (id, engagement_id, org_id, name, description, status, payment_trigger, sort_order, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)`
      )
      .bind(
        milestoneIds[i],
        engagementId,
        orgId,
        item.problem,
        item.description,
        i === lineItems.length - 1 ? 1 : 0,
        i,
        now
      )
  )
  return [
    ...buildCoreStmts(ctx, {
      contextEntryId,
      clientUserId,
      stageChangeContent,
      stageChangeMetadata,
      normalizedEmail,
    }),
    ...milestoneStmts,
    ...buildOutboxStmts(ctx),
  ]
}

// prettier-ignore
interface ClaimArgs { db: D1Database; request: SignatureRequest; providerRequestId: string; now: string; resendApiKey: string | undefined; stripeApiKey: string | undefined; appBaseUrl: string | undefined }

/**
 * Claim the `sent` -> `completed_pending_artifact` transition.
 * Returns true if this invocation won the race, false if another already did.
 * Handles dedup by re-triggering outbox processing if the winner already completed.
 */
async function claimSignatureTransition(args: ClaimArgs): Promise<boolean> {
  const { db, request, providerRequestId, now, resendApiKey, stripeApiKey, appBaseUrl } = args
  const claimResult = await db
    .prepare(
      `UPDATE signature_requests
       SET status = 'completed_pending_artifact', completed_at = COALESCE(completed_at, ?),
           webhook_last_at = ?, updated_at = ?
       WHERE id = ? AND org_id = ? AND status = 'sent'`
    )
    .bind(now, now, now, request.id, request.org_id)
    .run()

  if ((claimResult.meta?.changes ?? 0) > 0) return true

  // Another invocation beat us; if it already completed, process outbox now.
  const latestRequest = await getSignatureRequestByProviderRequestId(
    db,
    'signwell',
    providerRequestId
  )
  if (latestRequest?.status === 'completed') {
    await processOutboxJobsForSignatureRequest(
      db,
      latestRequest,
      resendApiKey,
      stripeApiKey,
      appBaseUrl
    )
  }
  return false
}

async function persistAndFinalizeArtifact(args: {
  db: D1Database
  storage: R2Bucket
  apiKey: string
  request: SignatureRequest
  quote: Quote
  providerRequestId: string
  signedKey: string
  now: string
}): Promise<Response | null> {
  const { db, storage, apiKey, request, quote, providerRequestId, signedKey, now } = args
  try {
    const signedPdf = await getSignedPdf(apiKey, providerRequestId)
    await uploadSignedSowRevisionPdf(storage, signedKey, signedPdf, {
      quoteId: quote.id,
      revisionId: request.sow_revision_id,
      providerRequestId,
      signedAt: now,
    })
  } catch (err) {
    console.error('[sow/finalize] Failed to persist signed artifact:', err)
    return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const lineItems = JSON.parse(quote.line_items) as Array<{ problem: string; description: string }>
  const signerSnapshot = JSON.parse(request.signer_snapshot_json) as SignerSnapshot
  const ctx: FinalizeCtx = {
    db,
    orgId: request.org_id,
    requestId: request.id,
    entityId: quote.entity_id,
    quoteId: quote.id,
    engagementId: crypto.randomUUID(),
    // ADR 0046: the commercial `service` parent. 'svc_' prefix is the shared
    // invariant with the backfill (migrations 0069/0070).
    serviceId: `svc_${crypto.randomUUID()}`,
    invoiceId: crypto.randomUUID(),
    depositAmount: quote.deposit_amount ?? 0,
    signer: signerSnapshot,
    now,
    signedKey,
    revisionId: request.sow_revision_id,
    totalHours: quote.total_hours,
  }
  try {
    await db.batch(buildFinalizationBatch(ctx, lineItems))
  } catch (err) {
    console.error('[sow/finalize] Finalization batch failed:', err)
    return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  return null
}

// ---------------------------------------------------------------------------
// Exported function
// ---------------------------------------------------------------------------

export async function finalizeCompletedSOWSignature(args: {
  db: D1Database
  storage: R2Bucket
  apiKey: string
  resendApiKey: string | undefined
  stripeApiKey: string | undefined
  appBaseUrl: string | undefined
  payload: SignWellWebhookPayload
}): Promise<Response> {
  const { db, storage, apiKey, resendApiKey, stripeApiKey, appBaseUrl, payload } = args
  const providerRequestId = payload.data.object.id
  const request = await getSignatureRequestByProviderRequestId(db, 'signwell', providerRequestId)

  if (!request) return unknownDocumentResponse(providerRequestId)

  if (request.status === 'completed') {
    await processOutboxJobsForSignatureRequest(db, request, resendApiKey, stripeApiKey, appBaseUrl)
    return okResponse()
  }

  const revision = await getSOWRevision(db, request.org_id, request.sow_revision_id)
  if (!revision) throw new Error(`Missing SOW revision for signature request ${request.id}`)

  const quote = await db
    .prepare('SELECT * FROM quotes WHERE id = ? AND org_id = ?')
    .bind(request.quote_id, request.org_id)
    .first<Quote>()
  if (!quote) throw new Error(`Missing quote for signature request ${request.id}`)

  const now = new Date().toISOString()

  if (request.status === 'sent') {
    const claimed = await claimSignatureTransition({
      db,
      request,
      providerRequestId,
      now,
      resendApiKey,
      stripeApiKey,
      appBaseUrl,
    })
    if (!claimed) return okResponse()
  } else if (request.status !== 'completed_pending_artifact') {
    return okResponse()
  }

  const signedKey =
    revision.signed_storage_key ?? getSowRevisionSignedKey(request.org_id, quote.id, revision.id)
  const errorResponse = await persistAndFinalizeArtifact({
    db,
    storage,
    apiKey,
    request,
    quote,
    providerRequestId,
    signedKey,
    now,
  })
  if (errorResponse) return errorResponse

  const completedRequest = await getSignatureRequestByProviderRequestId(
    db,
    'signwell',
    providerRequestId
  )
  if (completedRequest) {
    await processOutboxJobsForSignatureRequest(
      db,
      completedRequest,
      resendApiKey,
      stripeApiKey,
      appBaseUrl
    )
  }
  return okResponse()
}
