import type { APIRoute } from 'astro'
import { streamDocument } from '../../../../lib/storage/r2'
import { listEngagements } from '../../../../lib/db/engagements'
import { getPortalClient } from '../../../../lib/portal/session'
import { env } from 'cloudflare:workers'

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
  const session = locals.session
  if (!session || session.role !== 'client') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const key = params.key
  if (!key) {
    return new Response(JSON.stringify({ error: 'Document key required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Resolve client entity from session
  const portalData = await getPortalClient(env.DB, session.userId, session.orgId)
  if (!portalData) {
    return new Response(JSON.stringify({ error: 'Forbidden' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Path traversal protection: key must be scoped to this org.
  // Two conventions exist in R2:
  //   - Engagement docs:   `{orgId}/engagements/{id}/...`
  //   - SOW revisions:     `orgs/{orgId}/quotes/{qid}/sow/...` (see getSowRevisionSignedKey)
  const orgPrefix = `${session.orgId}/`
  const orgsScopedPrefix = `orgs/${session.orgId}/`
  if (!key.startsWith(orgPrefix) && !key.startsWith(orgsScopedPrefix)) {
    return new Response(JSON.stringify({ error: 'Forbidden' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Reject path traversal attempts
  if (key.includes('..') || key.includes('//')) {
    return new Response(JSON.stringify({ error: 'Forbidden' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Verify the key belongs to this client's engagement
  const engagements = await listEngagements(env.DB, session.orgId, portalData.client.id)
  const engagementIds = engagements.map((e) => e.id)
  const quoteIds = engagements.map((e) => e.quote_id)

  // Check if key matches engagement docs path or SOW PDF path
  const isEngagementDoc = engagementIds.some((id) =>
    key.startsWith(`${session.orgId}/engagements/${id}/`)
  )
  const isQuoteDoc = quoteIds.some(
    (qid) =>
      key.startsWith(`${session.orgId}/quotes/${qid}/`) ||
      key.startsWith(`orgs/${session.orgId}/quotes/${qid}/`)
  )

  if (!isEngagementDoc && !isQuoteDoc) {
    return new Response(JSON.stringify({ error: 'Forbidden' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Stream the document from R2
  const object = await streamDocument(env.STORAGE, key)
  if (!object) {
    return new Response(JSON.stringify({ error: 'Not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
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
