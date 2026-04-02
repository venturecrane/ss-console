import type { APIRoute } from 'astro'
import {
  getParkingLotItem,
  updateParkingLotItem,
  disposeParkingLotItem,
  deleteParkingLotItem,
} from '../../../../lib/db/parking-lot'
import type { Disposition } from '../../../../lib/db/parking-lot'
import { getEngagement } from '../../../../lib/db/engagements'

/**
 * POST /api/admin/parking-lot/:id
 *
 * Updates, disposes, or deletes a parking lot item.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields for update:
 *   - action: "update"
 *   - client_id (required, for redirect)
 *   - description
 *   - requested_by
 *
 * Form fields for disposition:
 *   - action: "dispose"
 *   - client_id (required, for redirect)
 *   - disposition (required: fold_in | follow_on | dropped)
 *   - disposition_note
 *
 * Form fields for delete:
 *   - _method: "DELETE"
 *   - client_id (required, for redirect)
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const itemId = params.id
  if (!itemId) {
    return new Response(JSON.stringify({ error: 'Parking lot item ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const item = await getParkingLotItem(env.DB, itemId)
    if (!item) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    // Verify the engagement belongs to this org
    const engagement = await getEngagement(env.DB, session.orgId, item.engagement_id)
    if (!engagement) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    const formData = await request.formData()
    const clientId = formData.get('client_id')
    const clientIdStr =
      clientId && typeof clientId === 'string' ? clientId.trim() : engagement.entity_id

    const parkingLotUrl = `/admin/entities/${clientIdStr}/engagements/${item.engagement_id}/parking-lot`

    const method = formData.get('_method')
    const action = formData.get('action')

    // Handle DELETE
    if (method === 'DELETE') {
      await deleteParkingLotItem(env.DB, itemId)
      return redirect(`${parkingLotUrl}?deleted=1`, 302)
    }

    // Handle disposition
    if (action === 'dispose') {
      const disposition = formData.get('disposition')
      if (!disposition || typeof disposition !== 'string') {
        return redirect(`${parkingLotUrl}?error=missing`, 302)
      }

      const validDispositions: Disposition[] = ['fold_in', 'follow_on', 'dropped']
      if (!validDispositions.includes(disposition as Disposition)) {
        return redirect(`${parkingLotUrl}?error=invalid_status`, 302)
      }

      const dispositionNote = formData.get('disposition_note')

      await disposeParkingLotItem(
        env.DB,
        itemId,
        disposition as Disposition,
        dispositionNote && typeof dispositionNote === 'string' && dispositionNote.trim()
          ? dispositionNote.trim()
          : null
      )

      return redirect(`${parkingLotUrl}?saved=1`, 302)
    }

    // Handle update
    const description = formData.get('description')
    const requestedBy = formData.get('requested_by')

    await updateParkingLotItem(env.DB, itemId, {
      description:
        description && typeof description === 'string' && description.trim()
          ? description.trim()
          : undefined,
      requested_by:
        requestedBy !== null && typeof requestedBy === 'string'
          ? requestedBy.trim() || null
          : undefined,
    })

    return redirect(`${parkingLotUrl}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/parking-lot/[id]] Error:', err)
    return redirect('/admin/clients?error=server', 302)
  }
}
