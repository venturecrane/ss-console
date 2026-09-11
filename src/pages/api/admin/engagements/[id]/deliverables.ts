import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../../lib/db/engagements'
import { getEngagementDocumentKey, listDocuments } from '../../../../../lib/storage/r2'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse, jsonResponse } from '../../../../../lib/api/helpers'

export const POST: APIRoute = async ({ request, locals, params }) => {
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
    const formData = await request.formData()
    const file = formData.get('file')
    if (!file || !(file instanceof File)) {
      return errorResponse(400, 'validation_failed', 'File required.')
    }
    // Collision-resistant key (ss#2315): two deliverables whose names
    // sanitized to the same string used to write the same key, and the
    // second silently removed the first from the client's document list.
    const key = await getEngagementDocumentKey(session.orgId, engagementId, file.name)
    const safeName = key.split('/').pop() ?? file.name
    const arrayBuffer = await file.arrayBuffer()
    await env.STORAGE.put(key, arrayBuffer, {
      httpMetadata: { contentType: file.type || 'application/octet-stream' },
      customMetadata: { originalName: file.name, uploadedAt: new Date().toISOString() },
    })
    return jsonResponse(201, { key, name: safeName })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/deliverables] Upload error:', err)
    return errorResponse(500, 'internal_error')
  }
}

export const GET: APIRoute = async ({ locals, params }) => {
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
    const prefix = `${session.orgId}/engagements/${engagementId}/docs/`
    const objects = await listDocuments(env.STORAGE, prefix)
    const files = objects.map((obj) => ({
      key: obj.key,
      name: obj.key.split('/').pop() ?? obj.key,
      size: obj.size,
      uploaded: obj.uploaded.toISOString(),
    }))
    return jsonResponse(200, { files })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/deliverables] List error:', err)
    return errorResponse(500, 'internal_error')
  }
}
