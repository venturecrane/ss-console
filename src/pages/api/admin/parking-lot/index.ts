import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../lib/db/engagements'
import { createParkingLotItem } from '../../../../lib/db/parking-lot'

/**
 * POST /api/admin/parking-lot
 *
 * Creates a new parking lot item for an engagement.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields:
 *   - engagement_id (required)
 *   - client_id (required, for redirect)
 *   - description (required)
 *   - requested_by
 */
export const POST: APIRoute = async ({ request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const formData = await request.formData()
    const engagementId = formData.get('engagement_id')
    const clientId = formData.get('client_id')

    if (
      !engagementId ||
      typeof engagementId !== 'string' ||
      !clientId ||
      typeof clientId !== 'string'
    ) {
      return redirect('/admin/clients?error=missing', 302)
    }

    const engagement = await getEngagement(env.DB, session.orgId, engagementId.trim())
    if (!engagement) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    const parkingLotUrl = `/admin/clients/${clientId.trim()}/engagements/${engagementId.trim()}/parking-lot`

    const description = formData.get('description')
    if (!description || typeof description !== 'string' || !description.trim()) {
      return redirect(`${parkingLotUrl}?error=missing`, 302)
    }

    const requestedBy = formData.get('requested_by')

    await createParkingLotItem(env.DB, engagementId.trim(), {
      description: description.trim(),
      requested_by:
        requestedBy && typeof requestedBy === 'string' && requestedBy.trim()
          ? requestedBy.trim()
          : null,
    })

    return redirect(`${parkingLotUrl}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/parking-lot] Create error:', err)
    return redirect('/admin/clients?error=server', 302)
  }
}
