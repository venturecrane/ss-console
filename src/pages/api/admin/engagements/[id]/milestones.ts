import type { APIContext, APIRoute } from 'astro'
import { getEngagement } from '../../../../../lib/db/engagements'
import {
  createMilestone,
  getMilestone,
  updateMilestone,
  updateMilestoneStatus,
  deleteMilestone,
} from '../../../../../lib/db/milestones'
import { completeMilestoneWithInvoicing } from '../../../../../lib/stripe/milestone-invoicing'
import type { MilestoneStatus } from '../../../../../lib/db/milestones'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../lib/api/helpers'

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

type Redirect = APIContext['redirect']

function trimStr(v: FormDataEntryValue | null): string | null {
  return v && typeof v === 'string' && v.trim() ? v.trim() : null
}

async function handleDelete(
  redirect: Redirect,
  orgId: string,
  engagementId: string,
  detailUrl: string,
  formData: FormData
): Promise<Response> {
  const milestoneId = formData.get('milestone_id')
  if (!milestoneId || typeof milestoneId !== 'string') {
    return redirect(`${detailUrl}?error=missing`, 302)
  }

  const milestone = await getMilestone(env.DB, orgId, milestoneId.trim())
  if (!milestone || milestone.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }

  await deleteMilestone(env.DB, orgId, milestoneId.trim())
  return redirect(`${detailUrl}?milestone_deleted=1`, 302)
}

async function handleTogglePaymentTrigger(
  redirect: Redirect,
  orgId: string,
  engagementId: string,
  detailUrl: string,
  formData: FormData
): Promise<Response> {
  const milestoneId = formData.get('milestone_id')
  if (!milestoneId || typeof milestoneId !== 'string') {
    return redirect(`${detailUrl}?error=missing`, 302)
  }
  const milestone = await getMilestone(env.DB, orgId, milestoneId.trim())
  if (!milestone || milestone.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }
  await updateMilestone(env.DB, orgId, milestoneId.trim(), {
    payment_trigger: !milestone.payment_trigger,
  })
  return redirect(`${detailUrl}?saved=1`, 302)
}

interface TransitionStatusArgs {
  redirect: Redirect
  orgId: string
  engagementId: string
  entityId: string
  detailUrl: string
  formData: FormData
}

async function handleTransitionStatus(args: TransitionStatusArgs): Promise<Response> {
  const { redirect, orgId, engagementId, entityId, detailUrl, formData } = args
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

  const milestone = await getMilestone(env.DB, orgId, milestoneId.trim())
  if (!milestone || milestone.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }

  try {
    if (newStatus === 'completed' && milestone.payment_trigger) {
      const contact = await env.DB.prepare(
        'SELECT email FROM contacts WHERE org_id = ? AND entity_id = ? AND email IS NOT NULL ORDER BY created_at ASC LIMIT 1'
      )
        .bind(orgId, entityId)
        .first<{ email: string }>()

      await completeMilestoneWithInvoicing({
        db: env.DB,
        orgId,
        milestoneId: milestoneId.trim(),
        stripeApiKey: env.STRIPE_API_KEY,
        customerEmail: contact?.email ?? null,
      })
    } else {
      await updateMilestoneStatus(env.DB, orgId, milestoneId.trim(), newStatus as MilestoneStatus)
    }
  } catch (err) {
    console.error('[api/admin/engagements/[id]/milestones] Status transition error:', err)
    return redirect(`${detailUrl}?error=invalid_transition`, 302)
  }

  return redirect(`${detailUrl}?saved=1`, 302)
}

async function handleCreate(
  redirect: Redirect,
  orgId: string,
  engagementId: string,
  detailUrl: string,
  formData: FormData
): Promise<Response> {
  const name = formData.get('name')
  if (!name || typeof name !== 'string' || !name.trim()) {
    return redirect(`${detailUrl}?error=missing`, 302)
  }

  const description = formData.get('description')
  const dueDate = formData.get('due_date')
  const paymentTrigger = formData.get('payment_trigger')
  const sortOrder = formData.get('sort_order')

  await createMilestone(env.DB, orgId, engagementId, {
    name: name.trim(),
    description: trimStr(description),
    due_date: trimStr(dueDate),
    payment_trigger: paymentTrigger === 'on' || paymentTrigger === '1',
    sort_order:
      sortOrder && typeof sortOrder === 'string' && sortOrder.trim()
        ? parseInt(sortOrder, 10) || 0
        : 0,
  })

  return redirect(`${detailUrl}?milestone_added=1`, 302)
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const engagementId = params.id
  if (!engagementId) {
    return errorResponse(400, 'validation_failed', 'Engagement ID required.')
  }

  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const method = formData.get('_method')
    const action = formData.get('action')

    const detailUrl = `/admin/engagements/${engagementId}`

    if (method === 'DELETE') {
      return handleDelete(redirect, session.orgId, engagementId, detailUrl, formData)
    }

    if (action === 'toggle_payment_trigger') {
      return handleTogglePaymentTrigger(redirect, session.orgId, engagementId, detailUrl, formData)
    }

    if (action === 'transition_status') {
      return handleTransitionStatus({
        redirect,
        orgId: session.orgId,
        engagementId,
        entityId: engagement.entity_id,
        detailUrl,
        formData,
      })
    }

    return handleCreate(redirect, session.orgId, engagementId, detailUrl, formData)
  } catch (err) {
    console.error('[api/admin/engagements/[id]/milestones] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
