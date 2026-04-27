import type { APIRoute } from 'astro'
import { createEngagement } from '../../../../lib/db/engagements'
import { createMilestone } from '../../../../lib/db/milestones'
import { getSignalById } from '../../../../lib/db/signal-attribution'
import { env } from 'cloudflare:workers'

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
      return redirect(`/admin/entities?error=missing`, 302)
    }

    const startDate = formData.get('start_date')
    const estimatedEnd = formData.get('estimated_end')
    const scopeSummary = formData.get('scope_summary')
    const estimatedHours = formData.get('estimated_hours')

    // Originating signal attribution (#589). Form may pass:
    //   "" / missing  → defer to DAL default (most-recent signal)
    //   "__none__"    → explicit unattributed (sentinel; admin chose blank)
    //   "<id>"        → explicit override; we validate org/entity scoping
    // Bad ids fall back to default rather than blocking engagement creation.
    const signalRaw = formData.get('originating_signal_id')
    const entityIdTrimmed = clientId.trim()
    let originatingSignalId: string | null | undefined
    if (typeof signalRaw === 'string') {
      const v = signalRaw.trim()
      if (v === '__none__') {
        originatingSignalId = null
      } else if (v !== '') {
        const signal = await getSignalById(env.DB, session.orgId, v)
        originatingSignalId = signal && signal.entity_id === entityIdTrimmed ? signal.id : undefined
      }
    }

    const engagement = await createEngagement(env.DB, session.orgId, {
      entity_id: entityIdTrimmed,
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
      ...(originatingSignalId !== undefined && { originating_signal_id: originatingSignalId }),
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

      await createMilestone(env.DB, session.orgId, engagement.id, {
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

    return redirect(`/admin/engagements/${engagement.id}`, 302)
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
    return redirect('/admin/entities?error=server', 302)
  }
}
