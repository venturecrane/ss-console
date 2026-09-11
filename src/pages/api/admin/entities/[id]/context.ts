import { jsonResponse, errorResponse } from '../../../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { appendContext, listContext, type ContextType } from '../../../../../lib/db/context'
import { getEntity } from '../../../../../lib/db/entities'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

/**
 * GET /api/admin/entities/[id]/context
 * List all context entries for an entity (chronological).
 *
 * POST /api/admin/entities/[id]/context
 * Append a new context entry (captain note, observation, etc.).
 */

export const GET: APIRoute = async ({ params, locals }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const entityId = params.id
  if (!entityId) {
    return errorResponse(400, 'validation_failed', 'Missing entity ID.')
  }

  try {
    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return errorResponse(404, 'not_found', 'Entity not found.')
    }

    const entries = await listContext(env.DB, entityId)
    return jsonResponse(200, { entity_id: entityId, entries })
  } catch (err) {
    console.error('[api/admin/entities/context] GET Error:', err)
    return errorResponse(500, 'internal_error')
  }
}

export const POST: APIRoute = async ({ params, request, locals, redirect }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

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
