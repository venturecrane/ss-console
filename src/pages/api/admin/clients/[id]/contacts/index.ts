import type { APIRoute } from 'astro'
import { getClient } from '../../../../../../lib/db/clients'
import { createContact } from '../../../../../../lib/db/contacts'

/**
 * POST /api/admin/clients/:id/contacts
 *
 * Creates a new contact linked to a client and redirects to the client detail page.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields:
 *   - name (required)
 *   - email
 *   - phone
 *   - title
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const clientId = params.id
  if (!clientId) {
    return new Response(JSON.stringify({ error: 'Client ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  // Verify client exists and belongs to org
  const client = await getClient(env.DB, session.orgId, clientId)
  if (!client) {
    return redirect('/admin/clients?error=not_found', 302)
  }

  try {
    const formData = await request.formData()
    const name = formData.get('name')

    // Validate required fields
    if (!name || typeof name !== 'string' || !name.trim()) {
      return redirect(`/admin/clients/${clientId}/contacts/new?error=missing`, 302)
    }

    const email = formData.get('email')
    const phone = formData.get('phone')
    const title = formData.get('title')

    await createContact(env.DB, session.orgId, clientId, {
      name: name.trim(),
      email: email && typeof email === 'string' && email.trim() ? email.trim() : null,
      phone: phone && typeof phone === 'string' && phone.trim() ? phone.trim() : null,
      title: title && typeof title === 'string' && title.trim() ? title.trim() : null,
    })

    return redirect(`/admin/clients/${clientId}?contacts_saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/clients/[id]/contacts] Create error:', err)
    return redirect(`/admin/clients/${clientId}/contacts/new?error=server`, 302)
  }
}
