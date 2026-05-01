import type { APIRoute } from 'astro'
import { getContact, updateContact, deleteContact } from '../../../../lib/db/contacts'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/contacts/:id
 *
 * Edit (default) or delete (with `_method=DELETE`) a contact. Redirects back
 * to the entity detail page on success. Org-scoped via getContact pre-check.
 *
 * Form fields for edit:
 *   - name, email, phone, title, role (all optional — sparse update)
 *
 * Form fields for delete:
 *   - _method: "DELETE"
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const contactId = params.id
  if (!contactId) {
    return new Response(JSON.stringify({ error: 'Contact ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const contact = await getContact(env.DB, session.orgId, contactId)
    if (!contact) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const detailUrl = `/admin/entities/${contact.entity_id}`
    const formData = await request.formData()
    const method = formData.get('_method')

    if (method === 'DELETE') {
      const ok = await deleteContact(env.DB, session.orgId, contactId)
      if (!ok) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }
      return redirect(`${detailUrl}?contact_deleted=1`, 302)
    }

    // Sparse update: only fields the form sent (and that differ).
    const stringOrNull = (key: string): string | null | undefined => {
      if (!formData.has(key)) return undefined
      const v = formData.get(key)
      if (typeof v !== 'string') return null
      const trimmed = v.trim()
      return trimmed ? trimmed : null
    }

    const nameRaw = formData.get('name')
    const name =
      nameRaw && typeof nameRaw === 'string' && nameRaw.trim() ? nameRaw.trim() : undefined

    if (formData.has('name') && !name) {
      return redirect(`${detailUrl}?error=missing_name`, 302)
    }

    await updateContact(env.DB, session.orgId, contactId, {
      ...(name !== undefined ? { name } : {}),
      email: stringOrNull('email'),
      phone: stringOrNull('phone'),
      title: stringOrNull('title'),
      role: stringOrNull('role'),
    })

    return redirect(`${detailUrl}?contact_updated=1`, 302)
  } catch (err) {
    console.error('[api/admin/contacts/[id]] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
