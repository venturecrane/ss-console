import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../../../lib/db/engagements'
import { streamDocument } from '../../../../../../lib/storage/r2'

export const GET: APIRoute = async ({ locals, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }
  const engagementId = params.id
  const keyPath = params.key
  if (!engagementId || !keyPath) {
    return new Response(JSON.stringify({ error: 'Missing parameters' }), {
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
    const fullKey = `${session.orgId}/engagements/${engagementId}/docs/${keyPath}`
    const obj = await streamDocument(env.STORAGE, fullKey)
    if (!obj) {
      return new Response(JSON.stringify({ error: 'File not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
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
    return new Response(JSON.stringify({ error: 'Internal server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
