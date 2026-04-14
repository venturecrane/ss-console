import type { APIRoute } from 'astro'
import { getEngagement, updateEngagement } from '../../../../../lib/db/engagements'

/**
 * Consultant photo upload endpoint.
 *
 * Image processing happens client-side (canvas crop + WebP encode) before
 * upload — see the admin engagement detail page. `sharp` is not
 * Workers-compatible, and a WASM image pipeline (@cf-wasm/photon) adds
 * meaningful cold-start cost for a workflow that needs one photo per
 * consultant. The server trusts that the client produced a WebP/JPEG/PNG
 * under 5 MB and validates only those two invariants before persisting
 * bytes to R2 and the public URL to `engagements.consultant_photo_url`.
 *
 * When `CONSULTANT_PHOTOS_PUBLIC_BASE` is set, the stored URL is the direct
 * public R2 URL. When unset, we fall back to the in-app streaming route at
 * `/api/portal/consultants/photo/[...key]`.
 */

const MAX_BYTES = 5 * 1024 * 1024 // 5 MB
const ACCEPTED_TYPES = new Set(['image/webp', 'image/jpeg', 'image/png'])

function extensionFor(mime: string): string {
  if (mime === 'image/webp') return 'webp'
  if (mime === 'image/png') return 'png'
  return 'jpg'
}

export const POST: APIRoute = async ({ request, locals, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const engagementId = params.id
  if (!engagementId) {
    return new Response(JSON.stringify({ error: 'Engagement ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return new Response(JSON.stringify({ error: 'Engagement not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const formData = await request.formData()
    const file = formData.get('photo')

    if (!file || !(file instanceof File)) {
      return new Response(JSON.stringify({ error: 'Photo file required' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    if (!ACCEPTED_TYPES.has(file.type)) {
      return new Response(
        JSON.stringify({
          error: `Unsupported image type: ${file.type || 'unknown'}. Expected WebP, JPEG, or PNG.`,
        }),
        { status: 415, headers: { 'Content-Type': 'application/json' } }
      )
    }

    if (file.size > MAX_BYTES) {
      return new Response(
        JSON.stringify({
          error: `Photo exceeds 5 MB limit (received ${(file.size / (1024 * 1024)).toFixed(2)} MB)`,
        }),
        { status: 413, headers: { 'Content-Type': 'application/json' } }
      )
    }

    if (file.size === 0) {
      return new Response(JSON.stringify({ error: 'Photo file is empty' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const ext = extensionFor(file.type)
    const key = `${session.orgId}/engagements/${engagementId}/${Date.now()}.${ext}`
    const bytes = await file.arrayBuffer()

    await env.CONSULTANT_PHOTOS.put(key, bytes, {
      httpMetadata: { contentType: file.type },
      customMetadata: {
        engagementId,
        orgId: session.orgId,
        uploadedAt: new Date().toISOString(),
        uploadedBy: session.userId,
      },
    })

    const publicBase = env.CONSULTANT_PHOTOS_PUBLIC_BASE?.replace(/\/$/, '')
    const photoUrl = publicBase ? `${publicBase}/${key}` : `/api/portal/consultants/photo/${key}`

    await updateEngagement(env.DB, session.orgId, engagementId, {
      consultant_photo_url: photoUrl,
    })

    return new Response(JSON.stringify({ key, url: photoUrl }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/consultant-photo] Upload error:', err)
    return new Response(JSON.stringify({ error: 'Internal server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}

export const DELETE: APIRoute = async ({ locals, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const engagementId = params.id
  if (!engagementId) {
    return new Response(JSON.stringify({ error: 'Engagement ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return new Response(JSON.stringify({ error: 'Engagement not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }

    const currentUrl = engagement.consultant_photo_url
    const publicBase = env.CONSULTANT_PHOTOS_PUBLIC_BASE?.replace(/\/$/, '')
    const streamPrefix = '/api/portal/consultants/photo/'

    // Only attempt R2 delete on URLs we control. External URLs (if a user ever
    // pastes one manually) are simply dereferenced by clearing the column.
    let key: string | null = null
    if (currentUrl) {
      if (publicBase && currentUrl.startsWith(`${publicBase}/`)) {
        key = currentUrl.slice(publicBase.length + 1)
      } else if (currentUrl.startsWith(streamPrefix)) {
        key = currentUrl.slice(streamPrefix.length)
      }
    }

    if (key && key.startsWith(`${session.orgId}/`)) {
      await env.CONSULTANT_PHOTOS.delete(key)
    }

    await updateEngagement(env.DB, session.orgId, engagementId, {
      consultant_photo_url: null,
    })

    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/consultant-photo] Delete error:', err)
    return new Response(JSON.stringify({ error: 'Internal server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
