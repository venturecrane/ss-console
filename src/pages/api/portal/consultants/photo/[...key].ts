import type { APIRoute } from 'astro'
import { listEngagements } from '../../../../../lib/db/engagements'
import { getPortalClient } from '../../../../../lib/portal/session'
import { env } from 'cloudflare:workers'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * GET /api/portal/consultants/photo/:key
 *
 * Streams a consultant photo from the CONSULTANT_PHOTOS R2 bucket.
 *
 * This route is the fallback when CONSULTANT_PHOTOS_PUBLIC_BASE is not
 * configured (bucket is private). The preferred delivery mode is a public
 * bucket served directly via `pub-*.r2.dev` or a custom R2 domain — that
 * avoids the Worker roundtrip and lets Cloudflare's edge cache serve
 * photos directly.
 *
 * Security:
 * - Resolves portal identity through the Clerk bridge
 * - Keys are tenant-scoped (`{orgId}/engagements/{engagementId}/...`)
 * - The engagement id in the key must belong to the resolved client
 * - Path traversal is rejected
 */

const CONTENT_TYPES: Record<string, string> = {
  webp: 'image/webp',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
}

function getContentType(key: string, objectContentType?: string): string {
  if (objectContentType) return objectContentType
  const ext = key.substring(key.lastIndexOf('.') + 1).toLowerCase()
  return CONTENT_TYPES[ext] ?? 'application/octet-stream'
}

function isInvalidKey(key: string): boolean {
  return key.includes('..') || key.includes('//')
}

export const GET: APIRoute = async ({ locals, params }) => {
  const key = params.key
  if (!key) {
    return errorResponse(400, 'validation_failed', 'Key required.')
  }

  const portalData = await getPortalClient(env.DB, locals)
  if (!portalData) {
    return errorResponse(401, 'unauthorized')
  }
  if (!portalData.client) {
    return errorResponse(403, 'forbidden', 'Forbidden.')
  }

  const engagementPrefix = `${portalData.user.org_id}/engagements/`
  if (!key.startsWith(engagementPrefix) || isInvalidKey(key)) {
    return errorResponse(403, 'forbidden', 'Forbidden.')
  }

  const engagements = await listEngagements(env.DB, portalData.user.org_id, portalData.client.id)
  const isClientEngagementPhoto = engagements.some((engagement) =>
    key.startsWith(`${engagementPrefix}${engagement.id}/`)
  )
  if (!isClientEngagementPhoto) {
    return errorResponse(403, 'forbidden', 'Forbidden.')
  }

  const object = await env.CONSULTANT_PHOTOS.get(key)
  if (!object) {
    return errorResponse(404, 'not_found', 'Not found.')
  }

  return new Response(object.body, {
    headers: {
      'Content-Type': getContentType(key, object.httpMetadata?.contentType),
      'Cache-Control': 'private, max-age=3600',
    },
  })
}
