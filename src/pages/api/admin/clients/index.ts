import type { APIRoute } from 'astro'
import { captureError } from '../../../../lib/observability/sentry'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../lib/auth/admin-session'
import { createEntity } from '../../../../lib/db/entities'
import { createContact } from '../../../../lib/db/contacts'

/**
 * POST /api/admin/clients
 *
 * Minimal add-a-client: create a record at stage `prospect` (the honest entry
 * point — a business we have started working, not yet signed), with an optional
 * primary contact. The record walks the lifecycle from here; signing is a stage
 * transition gated on an accepted quote, never set at creation.
 */
export const POST: APIRoute = async ({ request, locals, redirect }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  try {
    const form = await request.formData()
    const str = (key: string): string => {
      const v = form.get(key)
      return typeof v === 'string' ? v.trim() : ''
    }
    const name = str('name')
    const contactName = str('contact_name')
    const contactEmail = str('contact_email')
    const contactPhone = str('contact_phone')

    if (!name) return redirect('/admin/clients/new?error=name_required', 302)

    const entity = await createEntity(env.DB, session.orgId, {
      name,
      stage: 'prospect',
      source_pipeline: 'admin_manual',
    })

    if (contactName) {
      await createContact(env.DB, session.orgId, entity.id, {
        name: contactName,
        email: contactEmail || null,
        phone: contactPhone || null,
      })
    }

    return redirect(`/admin/clients/${entity.id}?created=1`, 302)
  } catch (err) {
    console.error('[api/admin/clients] POST Error:', err)
    captureError(err, 'admin.clients.create')
    return redirect('/admin/clients/new?error=server', 302)
  }
}
