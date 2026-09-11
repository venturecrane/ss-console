import { jsonResponse, errorResponse } from '../../../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { getAssessment, updateAssessment } from '../../../../../lib/db/assessments'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

/**
 * PUT /api/admin/assessments/:id/live-notes
 *
 * Auto-saves live notes for an assessment during the call.
 * Accepts JSON body: { live_notes: string }
 *
 * Protected by auth middleware (requires admin role).
 */
export const PUT: APIRoute = async ({ request, locals, params }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const assessmentId = params.id
  if (!assessmentId) {
    return errorResponse(400, 'validation_failed', 'Assessment ID required.')
  }

  try {
    const body: { live_notes?: unknown } = await request.json()
    const liveNotes = body.live_notes

    if (typeof liveNotes !== 'string') {
      return errorResponse(400, 'validation_failed', 'live_notes must be a string.')
    }

    const existing = await getAssessment(env.DB, session.orgId, assessmentId)
    if (!existing) {
      return errorResponse(404, 'not_found', 'Assessment not found.')
    }

    await updateAssessment(env.DB, session.orgId, assessmentId, {
      live_notes: liveNotes,
    })

    return jsonResponse(200, { ok: true })
  } catch (err) {
    console.error('[api/admin/assessments/[id]/live-notes] Error:', err)
    return errorResponse(500, 'internal_error')
  }
}
