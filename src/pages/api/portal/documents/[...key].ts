import type { APIRoute } from 'astro'
import { streamDocument } from '../../../../lib/storage/r2'
import { listEngagements } from '../../../../lib/db/engagements'
import { getOperatorAgreementForKey } from '../../../../lib/portal/agreement-documents'
import { getPortalClient } from '../../../../lib/portal/session'
import { env } from 'cloudflare:workers'
import { errorResponse } from '../../../../lib/api/helpers'

/**
 * GET /api/portal/documents/:key
 *
 * Streams a document from R2 for portal clients.
 * Uses a catch-all route to capture the full R2 key path.
 *
 * Security:
 * - Requires valid client session (middleware ensures role=client)
 * - Resolves entity via getPortalClient() (users.entity_id)
 * - Verifies the R2 key belongs to this client's org/engagement
 * - Executed Operator agreements (ss#2641) authorize on their D1 row rather
 *   than a prefix guess, and additionally require a principal/compliance
 *   role, matching the Compliance surface that links them
 * - Prevents path traversal by checking key prefix
 *
 * Content-Disposition:
 * - PDFs: inline (view in browser)
 * - Everything else: attachment (download)
 */

const CONTENT_TYPES: Record<string, string> = {
  '.pdf': 'application/pdf',
  '.doc': 'application/msword',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xls': 'application/vnd.ms-excel',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.csv': 'text/csv',
  '.txt': 'text/plain',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
}

function getContentType(key: string): string {
  const ext = key.substring(key.lastIndexOf('.')).toLowerCase()
  return CONTENT_TYPES[ext] ?? 'application/octet-stream'
}

export const GET: APIRoute = async ({ locals, params }) => {
  const key = params.key
  if (!key) {
    return errorResponse(400, 'Document key required')
  }

  // Resolve client entity via Clerk identity bridge
  const portalData = await getPortalClient(env.DB, locals)
  if (!portalData) {
    return errorResponse(401, 'Unauthorized')
  }
  if (!portalData.client) {
    return errorResponse(403, 'Forbidden')
  }

  // Path traversal protection: key must be scoped to this org.
  // Two conventions exist in R2:
  //   - Engagement docs:   `{orgId}/engagements/{id}/...`
  //   - SOW revisions:     `orgs/{orgId}/quotes/{qid}/sow/...` (see getSowRevisionSignedKey)
  const orgPrefix = `${portalData.user.org_id}/`
  const orgsScopedPrefix = `orgs/${portalData.user.org_id}/`
  if (!key.startsWith(orgPrefix) && !key.startsWith(orgsScopedPrefix)) {
    return errorResponse(403, 'Forbidden')
  }

  // Reject path traversal attempts
  if (key.includes('..') || key.includes('//')) {
    return errorResponse(403, 'Forbidden')
  }

  // Executed Operator agreements: authorized by their own row, not by prefix
  // shape. The row names the owning entity, so a key that is not this client's
  // cannot pass, and the role check mirrors the Compliance page that links it.
  const agreement = await getOperatorAgreementForKey(env.DB, {
    key,
    userId: portalData.user.id,
    orgId: portalData.user.org_id,
    entityId: portalData.client.id,
  })
  if (agreement.kind === 'allowed') {
    return streamKey(key)
  }
  if (agreement.kind === 'forbidden') {
    return errorResponse(403, 'Forbidden')
  }

  // Verify the key belongs to this client's engagement
  const engagements = await listEngagements(env.DB, portalData.user.org_id, portalData.client.id)
  const engagementIds = engagements.map((e) => e.id)
  const quoteIds = engagements.map((e) => e.quote_id)

  // Check if key matches engagement docs path or SOW PDF path
  const isEngagementDoc = engagementIds.some((id) =>
    key.startsWith(`${portalData.user.org_id}/engagements/${id}/`)
  )
  const isQuoteDoc = quoteIds.some(
    (qid) =>
      key.startsWith(`${portalData.user.org_id}/quotes/${qid}/`) ||
      key.startsWith(`orgs/${portalData.user.org_id}/quotes/${qid}/`)
  )

  if (!isEngagementDoc && !isQuoteDoc) {
    return errorResponse(403, 'Forbidden')
  }

  return streamKey(key)
}

/** Stream an already-AUTHORIZED key. Every caller must have proven the key
 * belongs to the signed-in client first; this function performs no checks. */
async function streamKey(key: string): Promise<Response> {
  const object = await streamDocument(env.STORAGE, key)
  if (!object) {
    return errorResponse(404, 'Not found')
  }

  const contentType = getContentType(key)
  const filename = key.split('/').pop() ?? 'document'
  const isPdf = contentType === 'application/pdf'
  const disposition = isPdf
    ? `inline; filename="${filename}"`
    : `attachment; filename="${filename}"`

  return new Response(object.body, {
    headers: {
      'Content-Type': contentType,
      'Content-Disposition': disposition,
      'Cache-Control': 'private, max-age=3600',
    },
  })
}
