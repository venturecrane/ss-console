import { jsonResponse, errorResponse } from '../../../../../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { getMeeting, updateMeeting } from '../../../../../../../lib/db/meetings'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../../../lib/auth/admin-session'

/**
 * PUT /api/admin/entities/:id/meetings/:meetingId/live-notes
 *
 * Auto-saves live notes for a meeting during the call. Accepts JSON body:
 * { live_notes: string }.
 *
 * Protected by auth middleware (requires admin role).
 */
export const PUT: APIRoute = async ({ request, locals, params }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const entityId = params.id
  const meetingId = params.meetingId
  if (!entityId || !meetingId) {
    return errorResponse(400, 'validation_failed', 'Entity ID and meeting ID required.')
  }

  try {
    const body: { live_notes?: unknown } = await request.json()
    const liveNotes = body.live_notes

    if (typeof liveNotes !== 'string') {
      return errorResponse(400, 'validation_failed', 'live_notes must be a string.')
    }

    const existing = await getMeeting(env.DB, session.orgId, meetingId)
    if (!existing) {
      return errorResponse(404, 'not_found', 'Meeting not found.')
    }
    if (existing.entity_id !== entityId) {
      return errorResponse(404, 'forbidden', 'Meeting does not belong to this entity.')
    }

    await updateMeeting(env.DB, session.orgId, meetingId, {
      live_notes: liveNotes,
    })

    return jsonResponse(200, { ok: true })
  } catch (err) {
    console.error('[api/admin/entities/[id]/meetings/[meetingId]/live-notes] Error:', err)
    return errorResponse(500, 'internal_error')
  }
}
