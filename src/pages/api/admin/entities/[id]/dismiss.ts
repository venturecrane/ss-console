import type { APIRoute } from 'astro'
import { transitionStage } from '../../../../../lib/db/entities'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/dismiss
 *
 * Dismiss a signal entity (stage → lost with reason).
 */
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
    const reason = formData.get('reason')
    const reasonStr =
      reason && typeof reason === 'string' && reason.trim()
        ? reason.trim()
        : 'Dismissed from inbox.'

    await transitionStage(env.DB, session.orgId, entityId, 'lost', reasonStr)

    return redirect('/admin/entities?dismissed=1', 302)
  } catch (err) {
    console.error('[api/admin/entities/dismiss] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
