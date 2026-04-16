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
import { getMissingAuthoredContent } from '../db/quotes'
import type { SOWTemplateProps } from '../pdf/sow-template'
import {
  createSOWSendAuthorization,
  createSignatureRequest,
  getLatestDownloadableSOWRevisionForQuote,
  getLatestRenderableSOWRevisionForQuote,
  getLatestSOWRevisionForQuote,
  getOpenSignatureRequestForQuote,
  getSOWRevision,
  getSignatureRequestByProviderRequestId,
  getLatestSignatureRequestForQuote,
  listOutboxJobsForSignatureRequest,
  supersedeRenderedSOWRevisionsForQuote,
  type OutboxJob,
  type SOWRevision,
  type SignatureRequest,
} from './store'
import {
  getPdf,
  getSowRevisionSignedKey,
  getSowRevisionUnsignedKey,
  uploadSignedSowRevisionPdf,
  uploadSowRevisionPdf,
} from '../storage/r2'
import { createSignatureRequest as createSignWellRequest, getSignedPdf } from '../signwell/client'
import type { SignWellCreateDocumentRequest, SignWellWebhookPayload } from '../signwell/types'
import { sendEmail } from '../email/resend'
import { portalWelcomeEmailHtml } from '../email/templates'
import { createStripeInvoice, sendStripeInvoice } from '../stripe/client'

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
  callbackBaseEnv: CfEnv
}): Promise<SignatureRequest> {
  const {
    db,
    storage,
    apiKey,
    orgId,
    actorId,
    quote,
    entityName,
    signer,
    customRequesterName = 'SMD Services',
    callbackBaseEnv,
  } = args

  const openRequest = await getOpenSignatureRequestForQuote(db, orgId, quote.id)
  if (openRequest) {
    throw new Error('SOW already sent for signature.')
  }

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
  if (!revision) {
    throw new Error('Generate a SOW PDF first.')
  }

  if (revision.quote_version !== quote.version) {
    throw new Error('The latest SOW revision is stale. Re-generate the PDF before sending.')
  }

  const pdfObject = await getPdf(storage, revision.unsigned_storage_key)
  if (!pdfObject) {
    throw new Error(`SOW PDF not found in storage: ${revision.unsigned_storage_key}`)
  }

  const pdfBuffer = await pdfObject.arrayBuffer()
  const pdfBase64 = uint8ArrayToBase64(new Uint8Array(pdfBuffer))
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

  const signerId = crypto.randomUUID()
  const callbackUrl = buildAppUrl(callbackBaseEnv, '/api/webhooks/signwell')
  // Field placement is handled by SignWell text tags embedded in the PDF
  // template (see src/lib/pdf/sow-template.tsx — {{s:1}} / {{d:1}} markers).
  // We do NOT send a fields[] array; SignWell parses the tags and places
  // the fields when text_tags is true. This keeps field positions and
  // template layout in lockstep by construction.
  const signRequest: SignWellCreateDocumentRequest = {
    name: `SOW — ${entityName}`,
    files: [{ file_base64: pdfBase64, name: 'sow.pdf' }],
    recipients: [
      {
        id: signerId,
        name: signer.name,
        email: signer.email,
      },
    ],
    callback_url: callbackUrl,
    text_tags: true,
    draft: false,
    custom_requester_name: customRequesterName,
    subject: `SOW for Signature — ${entityName}`,
    message: `Hi ${signer.name}, please review and sign the attached Statement of Work. If you have any questions, reply directly to this email.`,
  }

  try {
    const signwellDoc = await createSignWellRequest(apiKey, signRequest)

    const sentAt = new Date().toISOString()
    const expiresAt = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString()
    const request = await createSignatureRequest(db, {
      org_id: orgId,
      quote_id: quote.id,
      sow_revision_id: revision.id,
      send_authorization_id: authorization.id,
      provider_request_id: signwellDoc.id,
      status: 'sent',
      signer_snapshot_json: signerSnapshot,
      provider_payload_json: JSON.stringify(signRequest),
      sent_at: sentAt,
    })

    await db.batch([
      db
        .prepare(
          `UPDATE sow_revisions
           SET status = 'sent', updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(sentAt, revision.id, orgId),
      db
        .prepare(
          `UPDATE quotes
           SET status = CASE WHEN status = 'draft' THEN 'sent' ELSE status END,
               sent_at = CASE WHEN sent_at IS NULL THEN ? ELSE sent_at END,
               expires_at = CASE WHEN expires_at IS NULL THEN ? ELSE expires_at END,
               updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(sentAt, expiresAt, sentAt, quote.id, orgId),
    ])

    return request
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

  if (!request) {
    return unknownDocumentResponse(providerRequestId)
  }

  if (request.status === 'completed') {
    await processOutboxJobsForSignatureRequest(db, request, resendApiKey, stripeApiKey, appBaseUrl)
    return okResponse()
  }

  const revision = await getSOWRevision(db, request.org_id, request.sow_revision_id)
  if (!revision) {
    throw new Error(`Missing SOW revision for signature request ${request.id}`)
  }

  const quote = await db
    .prepare('SELECT * FROM quotes WHERE id = ? AND org_id = ?')
    .bind(request.quote_id, request.org_id)
    .first<Quote>()
  if (!quote) {
    throw new Error(`Missing quote for signature request ${request.id}`)
  }

  const now = new Date().toISOString()
  if (request.status === 'sent') {
    const claimResult = await db
      .prepare(
        `UPDATE signature_requests
         SET status = 'completed_pending_artifact', completed_at = COALESCE(completed_at, ?),
             webhook_last_at = ?, updated_at = ?
         WHERE id = ? AND org_id = ? AND status = 'sent'`
      )
      .bind(now, now, now, request.id, request.org_id)
      .run()

    if ((claimResult.meta?.changes ?? 0) === 0) {
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
      return okResponse()
    }
  } else if (request.status !== 'completed_pending_artifact') {
    return okResponse()
  }

  const signedKey =
    revision.signed_storage_key ?? getSowRevisionSignedKey(request.org_id, quote.id, revision.id)
  try {
    const signedPdf = await getSignedPdf(apiKey, providerRequestId)
    await uploadSignedSowRevisionPdf(storage, signedKey, signedPdf, {
      quoteId: quote.id,
      revisionId: revision.id,
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
  const engagementId = crypto.randomUUID()
  const invoiceId = crypto.randomUUID()
  const contextEntryId = crypto.randomUUID()
  const milestoneIds = lineItems.map(() => crypto.randomUUID())
  const stageChangeContent = 'Stage: proposing -> engaged. SOW signed via SignWell.'
  const stageChangeMetadata = JSON.stringify({
    from: 'proposing',
    to: 'engaged',
    reason: 'SOW signed via SignWell',
    quote_id: quote.id,
    engagement_id: engagementId,
    signature_request_id: request.id,
  })

  // Parse signer identity for portal user provisioning
  const signerSnapshot = JSON.parse(request.signer_snapshot_json) as {
    contactId: string
    name: string
    email: string
    title: string | null
  }
  const clientUserId = crypto.randomUUID()
  const normalizedSignerEmail = signerSnapshot.email.toLowerCase().trim()

  const milestoneStmts = lineItems.map((item, i) =>
    db
      .prepare(
        `INSERT INTO milestones (id, engagement_id, name, description, status, payment_trigger, sort_order, created_at)
         VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)`
      )
      .bind(
        milestoneIds[i],
        engagementId,
        item.problem,
        item.description,
        i === lineItems.length - 1 ? 1 : 0,
        i,
        now
      )
  )

  try {
    await db.batch([
      db
        .prepare(
          `UPDATE signature_requests
           SET status = 'completed', signed_storage_key = ?, completed_at = COALESCE(completed_at, ?),
               webhook_last_at = ?, updated_at = ?
           WHERE id = ? AND org_id = ? AND status = 'completed_pending_artifact'`
        )
        .bind(signedKey, now, now, now, request.id, request.org_id),
      db
        .prepare(
          `UPDATE sow_revisions
           SET status = 'signed', signed_storage_key = ?, signed_at = ?, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(signedKey, now, now, revision.id, request.org_id),
      db
        .prepare(
          `UPDATE quotes
           SET status = 'accepted', accepted_at = ?, updated_at = ?
           WHERE id = ? AND org_id = ? AND status = 'sent'`
        )
        .bind(now, now, quote.id, request.org_id),
      db
        .prepare(
          `UPDATE entities
           SET stage = 'engaged', stage_changed_at = ?, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(now, now, quote.entity_id, request.org_id),
      db
        .prepare(
          `INSERT INTO engagements (id, org_id, entity_id, quote_id, status, estimated_hours, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?)`
        )
        .bind(engagementId, request.org_id, quote.entity_id, quote.id, quote.total_hours, now, now),
      db
        .prepare(
          `INSERT INTO invoices (id, org_id, engagement_id, entity_id, type, amount, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'deposit', ?, 'draft', ?, ?)`
        )
        .bind(
          invoiceId,
          request.org_id,
          engagementId,
          quote.entity_id,
          quote.deposit_amount,
          now,
          now
        ),
      ...milestoneStmts,
      db
        .prepare(
          `INSERT INTO context (id, entity_id, org_id, type, content, source, content_size, metadata, created_at)
           VALUES (?, ?, ?, 'stage_change', ?, 'signwell-webhook', ?, ?, ?)`
        )
        .bind(
          contextEntryId,
          quote.entity_id,
          request.org_id,
          stageChangeContent,
          stageChangeContent.length,
          stageChangeMetadata,
          now
        ),
      db
        .prepare(
          `INSERT INTO outbox_jobs (
            id, org_id, signature_request_id, type, status, dedupe_key, payload_json, available_at, created_at, updated_at
          ) VALUES (?, ?, ?, 'send_sow_signed_email', 'pending', ?, ?, ?, ?, ?)`
        )
        .bind(
          crypto.randomUUID(),
          request.org_id,
          request.id,
          `signature-email:${request.id}`,
          JSON.stringify({
            signature_request_id: request.id,
            entity_id: quote.entity_id,
            quote_id: quote.id,
          }),
          now,
          now,
          now
        ),
      db
        .prepare(
          `INSERT INTO outbox_jobs (
            id, org_id, signature_request_id, type, status, dedupe_key, payload_json, available_at, created_at, updated_at
          ) VALUES (?, ?, ?, 'send_deposit_invoice', 'pending', ?, ?, ?, ?, ?)`
        )
        .bind(
          crypto.randomUUID(),
          request.org_id,
          request.id,
          `deposit-invoice:${request.id}`,
          JSON.stringify({
            signature_request_id: request.id,
            entity_id: quote.entity_id,
            quote_id: quote.id,
            engagement_id: engagementId,
            invoice_id: invoiceId,
            amount: quote.deposit_amount,
          }),
          now,
          now,
          now
        ),
      // Provision portal client user (idempotent — UPSERT backfills entity_id if NULL)
      db
        .prepare(
          `INSERT INTO users (id, org_id, email, name, role, entity_id, created_at)
           VALUES (?, ?, ?, ?, 'client', ?, ?)
           ON CONFLICT(org_id, email) DO UPDATE SET
             entity_id = COALESCE(users.entity_id, excluded.entity_id)`
        )
        .bind(
          clientUserId,
          request.org_id,
          normalizedSignerEmail,
          signerSnapshot.name,
          quote.entity_id,
          now
        ),
      // Queue portal welcome email
      db
        .prepare(
          `INSERT INTO outbox_jobs (
            id, org_id, signature_request_id, type, status, dedupe_key, payload_json, available_at, created_at, updated_at
          ) VALUES (?, ?, ?, 'send_portal_invitation', 'pending', ?, ?, ?, ?, ?)`
        )
        .bind(
          crypto.randomUUID(),
          request.org_id,
          request.id,
          `portal-invitation:${request.id}`,
          JSON.stringify({
            signature_request_id: request.id,
            entity_id: quote.entity_id,
            user_email: normalizedSignerEmail,
            user_name: signerSnapshot.name,
          }),
          now,
          now,
          now
        ),
    ])
  } catch (err) {
    console.error('[sow/finalize] Finalization batch failed:', err)
    return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }

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

async function processOutboxJobsForSignatureRequest(
  db: D1Database,
  request: SignatureRequest,
  resendApiKey: string | undefined,
  stripeApiKey: string | undefined,
  appBaseUrl: string | undefined
): Promise<void> {
  const jobs = await listOutboxJobsForSignatureRequest(db, request.org_id, request.id)
  for (const job of jobs) {
    if (job.status === 'completed') continue

    try {
      await db
        .prepare(
          `UPDATE outbox_jobs
           SET status = 'processing', attempt_count = attempt_count + 1, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(new Date().toISOString(), job.id, request.org_id)
        .run()

      if (job.type === 'send_sow_signed_email') {
        await handleSignedEmailJob(db, request.org_id, resendApiKey, job)
      } else if (job.type === 'send_deposit_invoice') {
        await handleDepositInvoiceJob(db, request.org_id, stripeApiKey, job)
      } else if (job.type === 'send_portal_invitation') {
        await handlePortalInvitationJob(db, request.org_id, resendApiKey, appBaseUrl, job)
      }

      await db
        .prepare(
          `UPDATE outbox_jobs
           SET status = 'completed', last_error = NULL, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(new Date().toISOString(), job.id, request.org_id)
        .run()
    } catch (err) {
      console.error('[sow/outbox] Job failed:', job.type, err)
      await db
        .prepare(
          `UPDATE outbox_jobs
           SET status = 'failed', last_error = ?, updated_at = ?
           WHERE id = ? AND org_id = ?`
        )
        .bind(
          err instanceof Error ? err.message : String(err),
          new Date().toISOString(),
          job.id,
          request.org_id
        )
        .run()
    }
  }
}

async function handleSignedEmailJob(
  db: D1Database,
  orgId: string,
  resendApiKey: string | undefined,
  job: OutboxJob
): Promise<void> {
  const payload = JSON.parse(job.payload_json) as { entity_id: string }
  const contact = await db
    .prepare(
      'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
    )
    .bind(orgId, payload.entity_id)
    .first<{ email: string }>()

  if (!contact?.email) {
    return
  }

  const entity = await db
    .prepare('SELECT name FROM entities WHERE id = ? AND org_id = ?')
    .bind(payload.entity_id, orgId)
    .first<{ name: string }>()

  await sendEmail(resendApiKey, {
    to: contact.email,
    subject: 'SOW Signed - Next Steps',
    html: signatureConfirmationEmailHtml(entity?.name ?? 'there'),
  })
}

async function handlePortalInvitationJob(
  _db: D1Database,
  _orgId: string,
  resendApiKey: string | undefined,
  appBaseUrl: string | undefined,
  job: OutboxJob
): Promise<void> {
  const payload = JSON.parse(job.payload_json) as {
    user_email: string
    user_name: string
  }

  if (!resendApiKey) {
    console.log('[sow/outbox] Portal invitation skipped (no RESEND_API_KEY)')
    return
  }

  // Build portal login URL with email pre-filled.
  // Uses PORTAL_BASE_URL convention: portal.<domain> or falls back to appBaseUrl + /auth/portal-login
  const portalLoginUrl = appBaseUrl
    ? `${appBaseUrl.replace('://', '://portal.')}`
    : 'https://portal.smd.services'
  const loginUrlWithEmail = `${portalLoginUrl}?email=${encodeURIComponent(payload.user_email)}`

  await sendEmail(resendApiKey, {
    to: payload.user_email,
    subject: 'Your SMD Services portal is ready',
    html: portalWelcomeEmailHtml(payload.user_name, loginUrlWithEmail),
  })
}

async function handleDepositInvoiceJob(
  db: D1Database,
  orgId: string,
  stripeApiKey: string | undefined,
  job: OutboxJob
): Promise<void> {
  const payload = JSON.parse(job.payload_json) as {
    entity_id: string
    engagement_id: string
    invoice_id: string
    amount: number
  }

  if (!stripeApiKey) {
    return
  }

  const contact = await db
    .prepare(
      'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
    )
    .bind(orgId, payload.entity_id)
    .first<{ email: string }>()

  if (!contact?.email) {
    return
  }

  const amountCents = Math.round((payload.amount ?? 0) * 100)
  const stripeResult = await createStripeInvoice(stripeApiKey, {
    customer_email: contact.email,
    description: 'Deposit - Operations Cleanup Engagement',
    line_items: [
      {
        amount: amountCents,
        currency: 'usd',
        description: 'Deposit (50% of project price)',
        quantity: 1,
      },
    ],
    days_until_due: 3,
    metadata: {
      invoice_id: payload.invoice_id,
      engagement_id: payload.engagement_id,
    },
  })

  const sentResult = await sendStripeInvoice(stripeApiKey, stripeResult.id)
  await db
    .prepare(
      `UPDATE invoices
       SET stripe_invoice_id = ?, stripe_hosted_url = ?, status = 'sent', sent_at = ?, updated_at = ?
       WHERE id = ? AND org_id = ?`
    )
    .bind(
      sentResult.id,
      sentResult.hosted_invoice_url,
      new Date().toISOString(),
      new Date().toISOString(),
      payload.invoice_id,
      orgId
    )
    .run()
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

function signatureConfirmationEmailHtml(businessName: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi ${businessName},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your Statement of Work has been signed successfully. We're excited to get started working together.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Our team will be in touch shortly with next steps, including the deposit invoice and scheduling details.
      </p>
    </div>
  </div>
</body>
</html>`
}

function okResponse(): Response {
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function unknownDocumentResponse(documentId: string): Response {
  console.log(`[signwell-handler] Unknown SignWell document: ${documentId}`)
  return okResponse()
}
