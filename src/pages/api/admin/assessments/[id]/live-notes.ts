import type { APIRoute } from 'astro'
import { getAssessment, updateAssessment } from '../../../../../lib/db/assessments'

/**
 * PUT /api/admin/assessments/:id/live-notes
 *
 * Auto-saves live notes for an assessment during the call.
 * Accepts JSON body: { live_notes: string }
 *
 * Protected by auth middleware (requires admin role).
 */
export const PUT: APIRoute = async ({ request, locals, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return jsonResponse(401, { error: 'Unauthorized' })
  }

  const assessmentId = params.id
  if (!assessmentId) {
    return jsonResponse(400, { error: 'Assessment ID required' })
  }

  const env = locals.runtime.env

  try {
    const body = (await request.json()) as Record<string, unknown>
    const liveNotes = body.live_notes

    if (typeof liveNotes !== 'string') {
      return jsonResponse(400, { error: 'live_notes must be a string' })
    }

    const existing = await getAssessment(env.DB, session.orgId, assessmentId)
    if (!existing) {
      return jsonResponse(404, { error: 'Assessment not found' })
    }

    await updateAssessment(env.DB, session.orgId, assessmentId, {
      live_notes: liveNotes,
    })

    return jsonResponse(200, { ok: true })
  } catch (err) {
    console.error('[api/admin/assessments/[id]/live-notes] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
