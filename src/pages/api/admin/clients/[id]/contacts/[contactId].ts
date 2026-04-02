import type { APIRoute } from 'astro'
import { getClient } from '../../../../../../lib/db/clients'
import { updateContact, deleteContact } from '../../../../../../lib/db/contacts'

/**
 * POST /api/admin/clients/:id/contacts/:contactId
 *
 * Updates an existing contact and redirects to the contact edit page.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Accepts the same fields as create. Only non-empty fields are updated.
 * If _method=DELETE is present in the form data, deletes the contact instead.
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
  const contactId = params.contactId
  if (!clientId || !contactId) {
    return new Response(JSON.stringify({ error: 'Client ID and Contact ID required' }), {
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

    // Handle delete via _method override
    const method = formData.get('_method')
    if (method === 'DELETE') {
      const deleted = await deleteContact(env.DB, session.orgId, contactId)
      if (!deleted) {
        return redirect(`/admin/clients/${clientId}?error=contact_not_found`, 302)
      }
      return redirect(`/admin/clients/${clientId}?contact_deleted=1`, 302)
    }

    const name = formData.get('name')

    // Validate required fields
    if (!name || typeof name !== 'string' || !name.trim()) {
      return redirect(`/admin/clients/${clientId}/contacts/${contactId}?error=missing`, 302)
    }

    const email = formData.get('email')
    const phone = formData.get('phone')
    const title = formData.get('title')

    const updated = await updateContact(env.DB, session.orgId, contactId, {
      name: name.trim(),
      email: email && typeof email === 'string' && email.trim() ? email.trim() : null,
      phone: phone && typeof phone === 'string' && phone.trim() ? phone.trim() : null,
      title: title && typeof title === 'string' && title.trim() ? title.trim() : null,
    })

    if (!updated) {
      return redirect(`/admin/clients/${clientId}?error=contact_not_found`, 302)
    }

    return redirect(`/admin/clients/${clientId}/contacts/${contactId}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/clients/[id]/contacts/[contactId]] Update error:', err)
    return redirect(`/admin/clients/${clientId}/contacts/${contactId}?error=server`, 302)
  }
}
