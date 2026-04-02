import type { APIRoute } from 'astro'
import { transitionStage } from '../../../../../lib/db/entities'

/**
 * POST /api/admin/entities/[id]/promote
 *
 * One-click promote: signal → prospect.
 * Records stage change, schedules follow-ups (Phase 5: generates outreach draft).
 */
export const POST: APIRoute = async ({ params, locals, redirect }) => {
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
    const env = locals.runtime.env
    await transitionStage(env.DB, session.orgId, entityId, 'prospect', 'Promoted from signal.')

    return redirect(`/admin/entities/${entityId}?promoted=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/promote] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities?error=${encodeURIComponent(message)}`, 302)
  }
}
