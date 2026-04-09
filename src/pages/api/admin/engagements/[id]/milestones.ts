import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../../lib/db/engagements'
import {
  createMilestone,
  getMilestone,
  updateMilestone,
  updateMilestoneStatus,
  deleteMilestone,
} from '../../../../../lib/db/milestones'
import type { MilestoneStatus } from '../../../../../lib/db/milestones'

/**
 * POST /api/admin/engagements/:id/milestones
 *
 * Creates a new milestone for an engagement, handles status transitions,
 * or deletes a milestone (via _method=DELETE).
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields for create:
 *   - name (required)
 *   - description
 *   - due_date
 *   - payment_trigger
 *   - sort_order
 *
 * Form fields for status transition:
 *   - action: "transition_status"
 *   - milestone_id (required)
 *   - new_status (required)
 *
 * Form fields for delete:
 *   - _method: "DELETE"
 *   - milestone_id (required)
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
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const method = formData.get('_method')
    const action = formData.get('action')

    const detailUrl = `/admin/engagements/${engagementId}`

    // Handle DELETE
    if (method === 'DELETE') {
      const milestoneId = formData.get('milestone_id')
      if (!milestoneId || typeof milestoneId !== 'string') {
        return redirect(`${detailUrl}?error=missing`, 302)
      }

      const milestone = await getMilestone(env.DB, milestoneId.trim())
      if (!milestone || milestone.engagement_id !== engagementId) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }

      await deleteMilestone(env.DB, milestoneId.trim())
      return redirect(`${detailUrl}?milestone_deleted=1`, 302)
    }

    // Handle payment_trigger toggle
    if (action === 'toggle_payment_trigger') {
      const milestoneId = formData.get('milestone_id')
      if (!milestoneId || typeof milestoneId !== 'string') {
        return redirect(`${detailUrl}?error=missing`, 302)
      }
      const milestone = await getMilestone(env.DB, milestoneId.trim())
      if (!milestone || milestone.engagement_id !== engagementId) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }
      await updateMilestone(env.DB, milestoneId.trim(), {
        payment_trigger: !milestone.payment_trigger,
      })
      return redirect(`${detailUrl}?saved=1`, 302)
    }

    // Handle status transition
    if (action === 'transition_status') {
      const milestoneId = formData.get('milestone_id')
      const newStatus = formData.get('new_status')

      if (
        !milestoneId ||
        typeof milestoneId !== 'string' ||
        !newStatus ||
        typeof newStatus !== 'string'
      ) {
        return redirect(`${detailUrl}?error=invalid_status`, 302)
      }

      const milestone = await getMilestone(env.DB, milestoneId.trim())
      if (!milestone || milestone.engagement_id !== engagementId) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }

      try {
        await updateMilestoneStatus(env.DB, milestoneId.trim(), newStatus as MilestoneStatus)
      } catch (err) {
        console.error('[api/admin/engagements/[id]/milestones] Status transition error:', err)
        return redirect(`${detailUrl}?error=invalid_transition`, 302)
      }

      return redirect(`${detailUrl}?saved=1`, 302)
    }

    // Handle create
    const name = formData.get('name')
    if (!name || typeof name !== 'string' || !name.trim()) {
      return redirect(`${detailUrl}?error=missing`, 302)
    }

    const description = formData.get('description')
    const dueDate = formData.get('due_date')
    const paymentTrigger = formData.get('payment_trigger')
    const sortOrder = formData.get('sort_order')

    await createMilestone(env.DB, engagementId, {
      name: name.trim(),
      description:
        description && typeof description === 'string' && description.trim()
          ? description.trim()
          : null,
      due_date: dueDate && typeof dueDate === 'string' && dueDate.trim() ? dueDate.trim() : null,
      payment_trigger: paymentTrigger === 'on' || paymentTrigger === '1',
      sort_order:
        sortOrder && typeof sortOrder === 'string' && sortOrder.trim()
          ? parseInt(sortOrder, 10) || 0
          : 0,
    })

    return redirect(`${detailUrl}?milestone_added=1`, 302)
  } catch (err) {
    console.error('[api/admin/engagements/[id]/milestones] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
