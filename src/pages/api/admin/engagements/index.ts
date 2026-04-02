import type { APIRoute } from 'astro'
import { createEngagement } from '../../../../lib/db/engagements'
import { createMilestone } from '../../../../lib/db/milestones'

/**
 * POST /api/admin/engagements
 *
 * Creates a new engagement from form data and redirects to the engagement detail page.
 * Optionally creates default milestones if included in the form.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields:
 *   - client_id (required)
 *   - quote_id (required)
 *   - start_date
 *   - estimated_end
 *   - scope_summary
 *   - estimated_hours
 *   - milestone_name[] (repeatable)
 *   - milestone_description[] (repeatable)
 *   - milestone_due_date[] (repeatable)
 *   - milestone_payment_trigger[] (repeatable)
 */
export const POST: APIRoute = async ({ request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const formData = await request.formData()
    const clientId = formData.get('client_id')
    const quoteId = formData.get('quote_id')

    if (
      !clientId ||
      typeof clientId !== 'string' ||
      !clientId.trim() ||
      !quoteId ||
      typeof quoteId !== 'string' ||
      !quoteId.trim()
    ) {
      return redirect(`/admin/clients?error=missing`, 302)
    }

    const env = locals.runtime.env
    const startDate = formData.get('start_date')
    const estimatedEnd = formData.get('estimated_end')
    const scopeSummary = formData.get('scope_summary')
    const estimatedHours = formData.get('estimated_hours')

    const engagement = await createEngagement(env.DB, session.orgId, {
      entity_id: clientId.trim(),
      quote_id: quoteId.trim(),
      start_date:
        startDate && typeof startDate === 'string' && startDate.trim() ? startDate.trim() : null,
      estimated_end:
        estimatedEnd && typeof estimatedEnd === 'string' && estimatedEnd.trim()
          ? estimatedEnd.trim()
          : null,
      scope_summary:
        scopeSummary && typeof scopeSummary === 'string' && scopeSummary.trim()
          ? scopeSummary.trim()
          : null,
      estimated_hours:
        estimatedHours && typeof estimatedHours === 'string' && estimatedHours.trim()
          ? parseFloat(estimatedHours) || null
          : null,
    })

    // Create default milestones if provided
    const milestoneNames = formData.getAll('milestone_name')
    for (let i = 0; i < milestoneNames.length; i++) {
      const name = milestoneNames[i]
      if (!name || typeof name !== 'string' || !name.trim()) continue

      const descriptions = formData.getAll('milestone_description')
      const dueDates = formData.getAll('milestone_due_date')
      const paymentTriggers = formData.getAll('milestone_payment_trigger')

      const description = descriptions[i]
      const dueDate = dueDates[i]
      const paymentTrigger = paymentTriggers[i]

      await createMilestone(env.DB, engagement.id, {
        name: name.trim(),
        description:
          description && typeof description === 'string' && description.trim()
            ? description.trim()
            : null,
        due_date: dueDate && typeof dueDate === 'string' && dueDate.trim() ? dueDate.trim() : null,
        payment_trigger: paymentTrigger === 'on' || paymentTrigger === '1',
        sort_order: i,
      })
    }

    return redirect(`/admin/entities/${clientId.trim()}/engagements/${engagement.id}`, 302)
  } catch (err) {
    console.error('[api/admin/engagements] Create error:', err)
    const formData = await request
      .clone()
      .formData()
      .catch(() => null)
    const clientId = formData?.get('client_id')
    if (clientId && typeof clientId === 'string') {
      return redirect(`/admin/entities/${clientId}/engagements/new?error=server`, 302)
    }
    return redirect('/admin/clients?error=server', 302)
  }
}
