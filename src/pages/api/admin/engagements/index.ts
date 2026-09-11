import type { APIContext, APIRoute } from 'astro'
import { trimString } from '../../../../lib/api/helpers'
import { createEngagement } from '../../../../lib/db/engagements'
import { createMilestone } from '../../../../lib/db/milestones'
import { getSignalById } from '../../../../lib/db/signal-attribution'
import { env } from 'cloudflare:workers'
import type { D1Database } from '@cloudflare/workers-types'
import { requireAdminSession } from '../../../../lib/auth/admin-session'

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

/** Resolve originating signal id from form value against the DB. */
async function resolveSignalId(
  db: D1Database,
  orgId: string,
  entityId: string,
  signalRaw: FormDataEntryValue | null
): Promise<string | null | undefined> {
  if (typeof signalRaw !== 'string') return undefined
  const v = signalRaw.trim()
  if (v === '__none__') return null
  if (v === '') return undefined
  const signal = await getSignalById(db, orgId, v)
  return signal && signal.entity_id === entityId ? signal.id : undefined
}

async function createFormMilestones(
  db: D1Database,
  orgId: string,
  engagementId: string,
  formData: FormData
): Promise<void> {
  const milestoneNames = formData.getAll('milestone_name')
  const descriptions = formData.getAll('milestone_description')
  const dueDates = formData.getAll('milestone_due_date')
  const paymentTriggers = formData.getAll('milestone_payment_trigger')

  for (let i = 0; i < milestoneNames.length; i++) {
    const name = milestoneNames[i]
    if (!name || typeof name !== 'string' || !name.trim()) continue
    await createMilestone(db, orgId, engagementId, {
      name: name.trim(),
      description: trimString(descriptions[i] ?? null),
      due_date: trimString(dueDates[i] ?? null),
      payment_trigger: paymentTriggers[i] === 'on' || paymentTriggers[i] === '1',
      sort_order: i,
    })
  }
}

async function handlePost({ request, locals, redirect }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

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

    const entityIdTrimmed = clientId.trim()
    const originatingSignalId = await resolveSignalId(
      env.DB,
      session.orgId,
      entityIdTrimmed,
      formData.get('originating_signal_id')
    )

    const engagement = await createEngagement(env.DB, session.orgId, {
      entity_id: entityIdTrimmed,
      quote_id: quoteId.trim(),
      start_date: trimString(formData.get('start_date')),
      estimated_end: trimString(formData.get('estimated_end')),
      scope_summary: trimString(formData.get('scope_summary')),
      estimated_hours: (() => {
        const raw = formData.get('estimated_hours')
        return raw && typeof raw === 'string' && raw.trim() ? parseFloat(raw) || null : null
      })(),
      ...(originatingSignalId !== undefined && { originating_signal_id: originatingSignalId }),
    })

    await createFormMilestones(env.DB, session.orgId, engagement.id, formData)

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

export const POST: APIRoute = (ctx) => handlePost(ctx)
