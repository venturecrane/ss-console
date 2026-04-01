import type { APIRoute } from 'astro'
import { updateTriageStatus } from '../../../../../lib/db/lead-signals'

/**
 * POST /api/admin/leads/[id]/dismiss
 *
 * Dismiss a lead signal with optional notes.
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ params, request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const signalId = params.id
  if (!signalId) {
    return redirect('/admin/leads?error=missing', 302)
  }

  try {
    const formData = await request.formData()
    const notes = formData.get('notes')
    const notesStr = notes && typeof notes === 'string' && notes.trim() ? notes.trim() : null

    const env = locals.runtime.env
    await updateTriageStatus(env.DB, session.orgId, signalId, 'dismissed', notesStr)

    return redirect('/admin/leads?dismissed=1', 302)
  } catch (err) {
    console.error('[api/admin/leads/dismiss] Error:', err)
    return redirect('/admin/leads?error=server', 302)
  }
}
