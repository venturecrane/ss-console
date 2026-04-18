import type { APIRoute } from 'astro'
import { appendContext, listContext, type ContextType } from '../../../../../lib/db/context'
import { getEntity } from '../../../../../lib/db/entities'
import { env } from 'cloudflare:workers'

/**
 * GET /api/admin/entities/[id]/context
 * List all context entries for an entity (chronological).
 *
 * POST /api/admin/entities/[id]/context
 * Append a new context entry (captain note, observation, etc.).
 */

export const GET: APIRoute = async ({ params, locals }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return jsonResponse(401, { error: 'Unauthorized' })
  }

  const entityId = params.id
  if (!entityId) {
    return jsonResponse(400, { error: 'Missing entity ID' })
  }

  try {
    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return jsonResponse(404, { error: 'Entity not found' })
    }

    const entries = await listContext(env.DB, entityId)
    return jsonResponse(200, { entity_id: entityId, entries })
  } catch (err) {
    console.error('[api/admin/entities/context] GET Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

export const POST: APIRoute = async ({ params, request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const entityId = params.id
  if (!entityId) {
    return redirect('/admin/entities?error=missing', 302)
  }

  try {
    const formData = await request.formData()
    const content = formData.get('content')
    const type = (formData.get('type') as ContextType) || 'note'

    if (!content || typeof content !== 'string' || !content.trim()) {
      return redirect(`/admin/entities/${entityId}?error=empty_content`, 302)
    }

    await appendContext(env.DB, session.orgId, {
      entity_id: entityId,
      type,
      content: content.trim(),
      source: 'captain',
    })

    return redirect(`/admin/entities/${entityId}?note_added=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/context] POST Error:', err)
    return redirect(`/admin/entities/${entityId}?error=server`, 302)
  }
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
