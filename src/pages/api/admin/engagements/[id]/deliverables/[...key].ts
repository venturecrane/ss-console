import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../../../lib/db/engagements'
import { streamDocument } from '../../../../../../lib/storage/r2'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../../lib/api/helpers'

export const GET: APIRoute = async ({ locals, params }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth
  const engagementId = params.id
  const keyPath = params.key
  if (!engagementId || !keyPath) {
    return errorResponse(400, 'validation_failed', 'Missing parameters.')
  }
  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return errorResponse(404, 'not_found', 'Engagement not found.')
    }
    const fullKey = `${session.orgId}/engagements/${engagementId}/docs/${keyPath}`
    const obj = await streamDocument(env.STORAGE, fullKey)
    if (!obj) {
      return errorResponse(404, 'not_found', 'File not found.')
    }
    const filename = keyPath.split('/').pop() ?? 'download'
    const contentType = obj.httpMetadata?.contentType ?? 'application/octet-stream'
    return new Response(obj.body, {
      status: 200,
      headers: {
        'Content-Type': contentType,
        'Content-Disposition': `attachment; filename="${filename}"`,
      },
    })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/deliverables/[...key]] Stream error:', err)
    return errorResponse(500, 'internal_error')
  }
}
