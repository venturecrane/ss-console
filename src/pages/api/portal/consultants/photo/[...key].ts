import type { APIRoute } from 'astro'

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
 * - Protected by portal middleware (role='client' required)
 * - Keys are tenant-scoped (`{orgId}/engagements/{engagementId}/...`) and
 *   must match the caller's orgId — path traversal rejected
 */

const CONTENT_TYPES: Record<string, string> = {
  webp: 'image/webp',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
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
    return new Response(JSON.stringify({ error: 'Key required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  if (!key.startsWith(`${session.orgId}/engagements/`)) {
    return new Response(JSON.stringify({ error: 'Forbidden' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  if (key.includes('..') || key.includes('//')) {
    return new Response(JSON.stringify({ error: 'Forbidden' }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env
  const object = await env.CONSULTANT_PHOTOS.get(key)
  if (!object) {
    return new Response(JSON.stringify({ error: 'Not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const ext = key.substring(key.lastIndexOf('.') + 1).toLowerCase()
  const contentType =
    object.httpMetadata?.contentType ?? CONTENT_TYPES[ext] ?? 'application/octet-stream'

  return new Response(object.body, {
    headers: {
      'Content-Type': contentType,
      'Cache-Control': 'private, max-age=3600',
    },
  })
}
