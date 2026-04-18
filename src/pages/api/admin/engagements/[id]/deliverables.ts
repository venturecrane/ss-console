import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../../lib/db/engagements'
import { listDocuments } from '../../../../../lib/storage/r2'
import { env } from 'cloudflare:workers'

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
  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return new Response(JSON.stringify({ error: 'Engagement not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    const formData = await request.formData()
    const file = formData.get('file')
    if (!file || !(file instanceof File)) {
      return new Response(JSON.stringify({ error: 'File required' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, '_')
    const key = `${session.orgId}/engagements/${engagementId}/docs/${safeName}`
    const arrayBuffer = await file.arrayBuffer()
    await env.STORAGE.put(key, arrayBuffer, {
      httpMetadata: { contentType: file.type || 'application/octet-stream' },
      customMetadata: { originalName: file.name, uploadedAt: new Date().toISOString() },
    })
    return new Response(JSON.stringify({ key, name: safeName }), {
      status: 201,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/deliverables] Upload error:', err)
    return new Response(JSON.stringify({ error: 'Internal server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}

export const GET: APIRoute = async ({ locals, params }) => {
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
  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return new Response(JSON.stringify({ error: 'Engagement not found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    const prefix = `${session.orgId}/engagements/${engagementId}/docs/`
    const objects = await listDocuments(env.STORAGE, prefix)
    const files = objects.map((obj) => ({
      key: obj.key,
      name: obj.key.split('/').pop() ?? obj.key,
      size: obj.size,
      uploaded: obj.uploaded.toISOString(),
    }))
    return new Response(JSON.stringify({ files }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  } catch (err) {
    console.error('[api/admin/engagements/[id]/deliverables] List error:', err)
    return new Response(JSON.stringify({ error: 'Internal server error' }), {
      status: 500,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
