import type { APIRoute } from 'astro'
import { createRetainer } from '../../../../lib/db/retainers'
import { transitionStage } from '../../../../lib/db/entities'

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
    const entityId = formData.get('entity_id')
    const monthlyRate = formData.get('monthly_rate')
    const startDate = formData.get('start_date')

    if (
      !entityId ||
      typeof entityId !== 'string' ||
      !entityId.trim() ||
      !monthlyRate ||
      typeof monthlyRate !== 'string' ||
      !monthlyRate.trim() ||
      !startDate ||
      typeof startDate !== 'string' ||
      !startDate.trim()
    ) {
      return redirect('/admin/entities?error=missing', 302)
    }

    const rate = parseFloat(monthlyRate)
    if (isNaN(rate) || rate <= 0) {
      return redirect(`/admin/entities/${entityId.trim()}?error=invalid_rate`, 302)
    }

    const env = locals.runtime.env
    const engagementId = formData.get('engagement_id')
    const includedHours = formData.get('included_hours')
    const scopeDescription = formData.get('scope_description')
    const terms = formData.get('terms')
    const cancellationPolicy = formData.get('cancellation_policy')
    const endDate = formData.get('end_date')

    await createRetainer(env.DB, session.orgId, {
      entity_id: entityId.trim(),
      engagement_id:
        engagementId && typeof engagementId === 'string' && engagementId.trim()
          ? engagementId.trim()
          : null,
      monthly_rate: rate,
      included_hours:
        includedHours && typeof includedHours === 'string' && includedHours.trim()
          ? parseFloat(includedHours) || null
          : null,
      scope_description:
        scopeDescription && typeof scopeDescription === 'string' && scopeDescription.trim()
          ? scopeDescription.trim()
          : null,
      terms: terms && typeof terms === 'string' && terms.trim() ? terms.trim() : null,
      cancellation_policy:
        cancellationPolicy && typeof cancellationPolicy === 'string' && cancellationPolicy.trim()
          ? cancellationPolicy.trim()
          : null,
      start_date: startDate.trim(),
      end_date: endDate && typeof endDate === 'string' && endDate.trim() ? endDate.trim() : null,
    })

    const transitionToOngoing = formData.get('transition_to_ongoing')
    if (transitionToOngoing === '1') {
      await transitionStage(
        env.DB,
        session.orgId,
        entityId.trim(),
        'ongoing',
        'Retainer created — transitioning to ongoing support.'
      )
    }

    return redirect(`/admin/entities/${entityId.trim()}?retainer_created=1`, 302)
  } catch (err) {
    console.error('[api/admin/retainers] Create error:', err)
    const fd = await request
      .clone()
      .formData()
      .catch(() => null)
    const eid = fd?.get('entity_id')
    if (eid && typeof eid === 'string') {
      return redirect(`/admin/entities/${eid}?error=server`, 302)
    }
    return redirect('/admin/entities?error=server', 302)
  }
}
