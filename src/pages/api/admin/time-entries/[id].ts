import type { APIRoute } from 'astro'
import { getTimeEntry, updateTimeEntry, deleteTimeEntry } from '../../../../lib/db/time-entries'
import { getEngagement } from '../../../../lib/db/engagements'

/**
 * POST /api/admin/time-entries/:id
 *
 * Updates or deletes a time entry (via _method=DELETE).
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields for update:
 *   - client_id (required, for redirect)
 *   - date
 *   - hours
 *   - description
 *   - category
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

  const entryId = params.id
  if (!entryId) {
    return new Response(JSON.stringify({ error: 'Time entry ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const entry = await getTimeEntry(env.DB, entryId)
    if (!entry) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    // Verify the engagement belongs to this org
    const engagement = await getEngagement(env.DB, session.orgId, entry.engagement_id)
    if (!engagement) {
      return redirect('/admin/clients?error=not_found', 302)
    }

    const formData = await request.formData()
    const clientId = formData.get('client_id')
    const clientIdStr =
      clientId && typeof clientId === 'string' ? clientId.trim() : engagement.entity_id

    const timeUrl = `/admin/entities/${clientIdStr}/engagements/${entry.engagement_id}/time`

    const method = formData.get('_method')

    // Handle DELETE
    if (method === 'DELETE') {
      await deleteTimeEntry(env.DB, entryId)
      return redirect(`${timeUrl}?deleted=1`, 302)
    }

    // Handle update
    const date = formData.get('date')
    const hours = formData.get('hours')
    const description = formData.get('description')
    const category = formData.get('category')

    await updateTimeEntry(env.DB, entryId, {
      date: date && typeof date === 'string' && date.trim() ? date.trim() : undefined,
      hours:
        hours && typeof hours === 'string' && hours.trim()
          ? parseFloat(hours) || undefined
          : undefined,
      description:
        description !== null && typeof description === 'string'
          ? description.trim() || null
          : undefined,
      category:
        category !== null && typeof category === 'string' ? category.trim() || null : undefined,
    })

    return redirect(`${timeUrl}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/time-entries/[id]] Error:', err)
    return redirect('/admin/clients?error=server', 302)
  }
}
