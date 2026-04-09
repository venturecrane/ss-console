import type { APIRoute } from 'astro'
import { transitionStage, type EntityStage } from '../../../../../lib/db/entities'

/**
 * POST /api/admin/entities/[id]/stage
 *
 * Generic stage transition endpoint.
 * Validates against allowed transitions defined in entities.ts.
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
    const stage = formData.get('stage') as EntityStage | null
    const reason = formData.get('reason')
    const reasonStr =
      reason && typeof reason === 'string' && reason.trim()
        ? reason.trim()
        : 'Stage changed by admin.'

    if (!stage) {
      return redirect(`/admin/entities/${entityId}?error=missing_stage`, 302)
    }

    const force = formData.get('force')
    const forceStr = force && typeof force === 'string' && force.trim() ? force.trim() : undefined

    const env = locals.runtime.env
    await transitionStage(
      env.DB,
      session.orgId,
      entityId,
      stage,
      reasonStr,
      forceStr ? { force: forceStr } : undefined
    )

    return redirect(`/admin/entities/${entityId}?stage_updated=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/stage] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities/${entityId}?error=${encodeURIComponent(message)}`, 302)
  }
}
