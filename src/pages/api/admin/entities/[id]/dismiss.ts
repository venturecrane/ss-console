import type { APIRoute } from 'astro'
import { transitionStage } from '../../../../../lib/db/entities'
import { isLostReasonCode } from '../../../../../lib/db/lost-reasons'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/dismiss
 *
 * Dismiss a signal entity (stage → lost with structured reason).
 *
 * Form fields:
 * - `lost_reason` (required): one of the codes in LOST_REASONS
 * - `lost_detail` (optional): free-text operator note
 * - `reason` (optional, legacy): free-text summary captured on the
 *   stage_change content. Falls back to the selected code's human label.
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
    const rawReasonCode = formData.get('lost_reason')
    const lostReasonCode = typeof rawReasonCode === 'string' ? rawReasonCode : null

    if (!lostReasonCode || !isLostReasonCode(lostReasonCode)) {
      return redirect('/admin/entities?error=lost_reason_required', 302)
    }

    const rawDetail = formData.get('lost_detail')
    const lostDetail =
      rawDetail && typeof rawDetail === 'string' && rawDetail.trim().length > 0
        ? rawDetail.trim()
        : null

    const rawSummary = formData.get('reason')
    const reasonSummary =
      rawSummary && typeof rawSummary === 'string' && rawSummary.trim().length > 0
        ? rawSummary.trim()
        : `Dismissed: ${lostReasonCode}${lostDetail ? ` — ${lostDetail}` : ''}`

    await transitionStage(env.DB, session.orgId, entityId, 'lost', reasonSummary, {
      lostReason: { code: lostReasonCode, detail: lostDetail },
    })

    return redirect('/admin/entities?dismissed=1', 302)
  } catch (err) {
    console.error('[api/admin/entities/dismiss] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities?error=${encodeURIComponent(message)}`, 302)
  }
}
