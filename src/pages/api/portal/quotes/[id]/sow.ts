import type { APIRoute } from 'astro'
import { getPortalClient } from '../../../../../lib/portal/session'
import { getQuoteForEntity } from '../../../../../lib/db/quotes'
import { getPdf } from '../../../../../lib/storage/r2'
import { getSOWStateForQuote } from '../../../../../lib/sow/service'
import { env } from 'cloudflare:workers'

/**
 * GET /api/portal/quotes/:id/sow
 *
 * Streams the SOW PDF from R2 for the authenticated portal client.
 *
 * Protected by auth middleware (requires client role).
 * Scoped to client_id (not org_id) — portal access pattern.
 */
export const GET: APIRoute = async ({ locals, params }) => {
  const session = locals.session
  if (!session || session.role !== 'client') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const quoteId = params.id
  if (!quoteId) {
    return new Response(JSON.stringify({ error: 'Quote ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Resolve client from session
  const portalData = await getPortalClient(env.DB, session.userId, session.orgId)
  if (!portalData) {
    return new Response(JSON.stringify({ error: 'Client not found' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Get quote scoped to this client
  const quote = await getQuoteForEntity(env.DB, session.orgId, portalData.client.id, quoteId)
  if (!quote) {
    return new Response(JSON.stringify({ error: 'Quote not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const sowState = await getSOWStateForQuote(env.DB, session.orgId, quote.id)
  const revision = sowState.downloadableRevision

  if (!revision) {
    return new Response(JSON.stringify({ error: 'SOW not available' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  // Stream PDF from R2
  const key = revision.signed_storage_key ?? revision.unsigned_storage_key
  const object = await getPdf(env.STORAGE, key)
  if (!object) {
    return new Response(JSON.stringify({ error: 'SOW file not found in storage' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  return new Response(object.body, {
    headers: {
      'Content-Type': 'application/pdf',
      'Content-Disposition': `attachment; filename="SMD-Services-SOW.pdf"`,
    },
  })
}
