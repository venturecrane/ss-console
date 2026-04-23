import type { APIRoute } from 'astro'
import {
  transitionStage,
  type EntityStage,
  type TransitionStageOptions,
} from '../../../../../lib/db/entities'
import { isLostReasonCode } from '../../../../../lib/db/lost-reasons'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/stage
 *
 * Generic stage transition endpoint.
 * Validates against allowed transitions defined in entities.ts.
 *
 * When `stage === 'lost'`, a structured `lost_reason` form field is
 * required. The `lost_detail` field is optional free-text context.
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

    const options: TransitionStageOptions = {}
    if (stage === 'lost') {
      const rawCode = formData.get('lost_reason')
      const lostReasonCode = typeof rawCode === 'string' ? rawCode : null
      if (!lostReasonCode || !isLostReasonCode(lostReasonCode)) {
        return redirect(
          `/admin/entities/${entityId}?error=${encodeURIComponent('lost_reason_required')}`,
          302
        )
      }
      const rawDetail = formData.get('lost_detail')
      const lostDetail =
        rawDetail && typeof rawDetail === 'string' && rawDetail.trim().length > 0
          ? rawDetail.trim()
          : null
      options.lostReason = { code: lostReasonCode, detail: lostDetail }
    }

    await transitionStage(env.DB, session.orgId, entityId, stage, reasonStr, options)

    return redirect(`/admin/entities/${entityId}?stage_updated=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/stage] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities/${entityId}?error=${encodeURIComponent(message)}`, 302)
  }
}
