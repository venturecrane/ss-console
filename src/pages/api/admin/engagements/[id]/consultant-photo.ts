import type { APIContext, APIRoute } from 'astro'
import { getEngagement, updateEngagement } from '../../../../../lib/db/engagements'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse, jsonResponse } from '../../../../../lib/api/helpers'

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

function validatePhotoFile(file: FormDataEntryValue | null): Response | File {
  if (!file || !(file instanceof File))
    return errorResponse(400, 'validation_failed', 'A photo file is required.')
  if (!ACCEPTED_TYPES.has(file.type)) {
    return errorResponse(
      415,
      'validation_failed',
      `Unsupported image type: ${file.type || 'unknown'}. Expected WebP, JPEG, or PNG.`
    )
  }
  if (file.size > MAX_BYTES) {
    return errorResponse(
      413,
      'validation_failed',
      `Photo exceeds 5 MB limit (received ${(file.size / (1024 * 1024)).toFixed(2)} MB)`
    )
  }
  if (file.size === 0) return errorResponse(400, 'validation_failed', 'The photo file is empty.')
  return file
}

async function handlePost({ request, locals, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const engagementId = params.id
  if (!engagementId) {
    return errorResponse(400, 'validation_failed', 'Engagement ID required.')
  }

  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) return errorResponse(404, 'not_found', 'Engagement not found.')

    const formData = await request.formData()
    const fileOrError = validatePhotoFile(formData.get('photo'))
    if (fileOrError instanceof Response) return fileOrError
    const file = fileOrError

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
    await updateEngagement(env.DB, session.orgId, engagementId, { consultant_photo_url: photoUrl })

    return jsonResponse(201, { key, url: photoUrl })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/consultant-photo] Upload error:', err)
    return errorResponse(500, 'internal_error')
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)

async function handleDelete({ locals, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const engagementId = params.id
  if (!engagementId) {
    return errorResponse(400, 'validation_failed', 'Engagement ID required.')
  }

  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return errorResponse(404, 'not_found', 'Engagement not found.')
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

    return jsonResponse(200, { ok: true })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/consultant-photo] Delete error:', err)
    return errorResponse(500, 'internal_error')
  }
}

export const DELETE: APIRoute = (ctx) => handleDelete(ctx)
