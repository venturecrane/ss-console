import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../lib/db/engagements'
import { createTimeEntry } from '../../../../lib/db/time-entries'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/time-entries
 *
 * Creates a new time entry for an engagement.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields:
 *   - engagement_id (required)
 *   - client_id (required, for redirect)
 *   - date (required)
 *   - hours (required)
 *   - description
 *   - category
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
    const engagementId = formData.get('engagement_id')
    const clientId = formData.get('client_id')

    if (
      !engagementId ||
      typeof engagementId !== 'string' ||
      !clientId ||
      typeof clientId !== 'string'
    ) {
      return redirect('/admin/entities?error=missing', 302)
    }

    const engagement = await getEngagement(env.DB, session.orgId, engagementId.trim())
    if (!engagement) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const timeUrl = `/admin/entities/${clientId.trim()}/engagements/${engagementId.trim()}/time`

    const date = formData.get('date')
    const hours = formData.get('hours')

    if (
      !date ||
      typeof date !== 'string' ||
      !date.trim() ||
      !hours ||
      typeof hours !== 'string' ||
      !hours.trim()
    ) {
      return redirect(`${timeUrl}?error=missing`, 302)
    }

    const parsedHours = parseFloat(hours)
    if (isNaN(parsedHours) || parsedHours <= 0) {
      return redirect(`${timeUrl}?error=missing`, 302)
    }

    const description = formData.get('description')
    const category = formData.get('category')

    await createTimeEntry(env.DB, session.orgId, engagementId.trim(), {
      date: date.trim(),
      hours: parsedHours,
      description:
        description && typeof description === 'string' && description.trim()
          ? description.trim()
          : null,
      category:
        category && typeof category === 'string' && category.trim() ? category.trim() : null,
    })

    return redirect(`${timeUrl}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/time-entries] Create error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
