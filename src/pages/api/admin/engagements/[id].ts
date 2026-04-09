import type { APIRoute } from 'astro'
import {
  getEngagement,
  updateEngagement,
  updateEngagementStatus,
} from '../../../../lib/db/engagements'
import type { EngagementStatus } from '../../../../lib/db/engagements'

/**
 * POST /api/admin/engagements/:id
 *
 * Updates an existing engagement from form data.
 * Handles field updates and status transitions.
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const engagementId = params.id
  if (!engagementId) {
    return new Response(JSON.stringify({ error: 'Engagement ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const existing = await getEngagement(env.DB, session.orgId, engagementId)
    if (!existing) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')

    // Handle status transition as a separate action
    if (action === 'transition_status') {
      const newStatus = formData.get('new_status')
      if (!newStatus || typeof newStatus !== 'string') {
        return redirect(`/admin/engagements/${engagementId}?error=invalid_status`, 302)
      }

      try {
        await updateEngagementStatus(
          env.DB,
          session.orgId,
          engagementId,
          newStatus as EngagementStatus
        )
      } catch (err) {
        console.error('[api/admin/engagements/[id]] Status transition error:', err)
        return redirect(`/admin/engagements/${engagementId}?error=invalid_transition`, 302)
      }

      return redirect(`/admin/engagements/${engagementId}?saved=1`, 302)
    }

    // Handle general update
    const scopeSummary = formData.get('scope_summary')
    const startDate = formData.get('start_date')
    const estimatedEnd = formData.get('estimated_end')
    const estimatedHours = formData.get('estimated_hours')
    const actualHours = formData.get('actual_hours')

    await updateEngagement(env.DB, session.orgId, engagementId, {
      scope_summary:
        scopeSummary && typeof scopeSummary === 'string' ? scopeSummary.trim() || null : undefined,
      start_date:
        startDate && typeof startDate === 'string' && startDate.trim() ? startDate.trim() : null,
      estimated_end:
        estimatedEnd && typeof estimatedEnd === 'string' && estimatedEnd.trim()
          ? estimatedEnd.trim()
          : null,
      estimated_hours:
        estimatedHours && typeof estimatedHours === 'string' && estimatedHours.trim()
          ? parseFloat(estimatedHours) || null
          : null,
      actual_hours:
        actualHours && typeof actualHours === 'string' && actualHours.trim()
          ? parseFloat(actualHours) || null
          : undefined,
    })

    return redirect(`/admin/engagements/${engagementId}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/engagements/[id]] Update error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
