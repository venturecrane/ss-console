import type { APIRoute } from 'astro'
import { getEntity } from '../../../../../lib/db/entities'
import { createContact } from '../../../../../lib/db/contacts'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/:id/contacts
 *
 * Create a contact attached to an entity. Edit + delete live on
 * /api/admin/contacts/[id].
 *
 * Form fields:
 *   - name (required)
 *   - email (optional — required before portal invitations, enforced
 *     elsewhere in the codepath that issues invites)
 *   - phone (optional)
 *   - title (optional)
 *   - role (optional, free-text job role on the contact record itself —
 *     distinct from per-engagement role assignment)
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const entityId = params.id
  if (!entityId) {
    return new Response(JSON.stringify({ error: 'Entity ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const detailUrl = `/admin/entities/${entityId}`
    const formData = await request.formData()

    const nameRaw = formData.get('name')
    if (!nameRaw || typeof nameRaw !== 'string' || !nameRaw.trim()) {
      return redirect(`${detailUrl}?error=missing_name`, 302)
    }

    const stringOrNull = (key: string): string | null => {
      const v = formData.get(key)
      if (typeof v !== 'string') return null
      const trimmed = v.trim()
      return trimmed ? trimmed : null
    }

    await createContact(env.DB, session.orgId, entityId, {
      name: nameRaw.trim(),
      email: stringOrNull('email'),
      phone: stringOrNull('phone'),
      title: stringOrNull('title'),
      role: stringOrNull('role'),
    })

    return redirect(`${detailUrl}?contact_added=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/[id]/contacts] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
