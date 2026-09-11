import type { APIContext, APIRoute } from 'astro'
import { getEngagement } from '../../../../../lib/db/engagements'
import {
  createParkingLotItem,
  dispositionParkingLotItem,
  deleteParkingLotItem,
  getParkingLotItem,
  DISPOSITIONS,
} from '../../../../../lib/db/parking-lot'
import type { Disposition } from '../../../../../lib/db/parking-lot'
import { appendContext } from '../../../../../lib/db/context'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * POST /api/admin/engagements/:id/parking-lot
 *
 * Manages parking lot items on an engagement (Decision Stack #11). Single
 * endpoint with action dispatcher, mirroring the milestones pattern.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields for create:
 *   - description (required)
 *   - requested_by (optional)
 *
 * Form fields for disposition:
 *   - action: "disposition"
 *   - item_id (required)
 *   - disposition (required, one of: fold_in | follow_on | dropped)
 *   - disposition_note (required, non-empty)
 *
 * Form fields for delete:
 *   - _method: "DELETE"
 *   - item_id (required)
 *
 * Note: disposition_note is required at click time per Decision #11 — the
 * methodology demands a rationale at the moment of disposition, not as a
 * pencil-edit later.
 */

type Redirect = APIContext['redirect']

interface EngagementContext {
  entityId: string
  engagementId: string
  orgId: string
}

async function handleDelete(
  redirect: Redirect,
  ctx: EngagementContext,
  detailUrl: string,
  formData: FormData
): Promise<Response> {
  const { orgId, engagementId, entityId } = ctx
  const itemId = formData.get('item_id')
  if (!itemId || typeof itemId !== 'string') {
    return redirect(`${detailUrl}?error=missing`, 302)
  }

  const item = await getParkingLotItem(env.DB, orgId, itemId.trim())
  if (!item || item.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }

  const result = await deleteParkingLotItem(env.DB, orgId, itemId.trim())
  if (result === 'dispositioned') {
    return redirect(`${detailUrl}?error=cannot_delete_dispositioned`, 302)
  }
  if (result !== 'ok') {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }

  await appendContext(env.DB, orgId, {
    entity_id: entityId,
    type: 'parking_lot',
    content: `Deleted parking lot item: ${item.description}`,
    source: 'admin',
    source_ref: `parking_lot:${item.id}:deleted`,
    metadata: { item_id: item.id },
    engagement_id: engagementId,
  })

  return redirect(`${detailUrl}?parking_lot_deleted=1`, 302)
}

async function handleDisposition(
  redirect: Redirect,
  ctx: EngagementContext,
  detailUrl: string,
  formData: FormData
): Promise<Response> {
  const { orgId, engagementId, entityId } = ctx
  const itemId = formData.get('item_id')
  const disposition = formData.get('disposition')
  const note = formData.get('disposition_note')

  if (!itemId || typeof itemId !== 'string') {
    return redirect(`${detailUrl}?error=missing`, 302)
  }

  if (
    !disposition ||
    typeof disposition !== 'string' ||
    !DISPOSITIONS.includes(disposition as Disposition)
  ) {
    return redirect(`${detailUrl}?error=invalid_disposition`, 302)
  }

  if (!note || typeof note !== 'string' || !note.trim()) {
    return redirect(`${detailUrl}?error=missing_note`, 302)
  }

  const item = await getParkingLotItem(env.DB, orgId, itemId.trim())
  if (!item || item.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }

  const updated = await dispositionParkingLotItem(
    env.DB,
    orgId,
    itemId.trim(),
    disposition as Disposition,
    note.trim()
  )

  if (!updated) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }

  await appendContext(env.DB, orgId, {
    entity_id: entityId,
    type: 'parking_lot',
    content: `Dispositioned as ${disposition}: ${note.trim()}`,
    source: 'admin',
    source_ref: `parking_lot:${updated.id}:dispositioned`,
    metadata: { disposition, item_id: updated.id },
    engagement_id: engagementId,
  })

  return redirect(`${detailUrl}?parking_lot_dispositioned=1`, 302)
}

async function handleCreate(
  redirect: Redirect,
  ctx: EngagementContext,
  detailUrl: string,
  formData: FormData
): Promise<Response> {
  const { orgId, engagementId, entityId } = ctx
  const description = formData.get('description')
  if (!description || typeof description !== 'string' || !description.trim()) {
    return redirect(`${detailUrl}?error=missing`, 302)
  }

  const requestedBy = formData.get('requested_by')

  const item = await createParkingLotItem(env.DB, orgId, engagementId, {
    description: description.trim(),
    requested_by:
      requestedBy && typeof requestedBy === 'string' && requestedBy.trim()
        ? requestedBy.trim()
        : null,
  })

  await appendContext(env.DB, orgId, {
    entity_id: entityId,
    type: 'parking_lot',
    content: item.description,
    source: 'admin',
    source_ref: `parking_lot:${item.id}:created`,
    metadata: { requested_by: item.requested_by, item_id: item.id },
    engagement_id: engagementId,
  })

  return redirect(`${detailUrl}?parking_lot_added=1`, 302)
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
    const ctx: EngagementContext = {
      orgId: session.orgId,
      engagementId,
      entityId: engagement.entity_id,
    }

    if (method === 'DELETE') {
      return handleDelete(redirect, ctx, detailUrl, formData)
    }

    if (action === 'disposition') {
      return handleDisposition(redirect, ctx, detailUrl, formData)
    }

    return handleCreate(redirect, ctx, detailUrl, formData)
  } catch (err) {
    console.error('[api/admin/engagements/[id]/parking-lot] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
