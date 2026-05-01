import type { APIRoute } from 'astro'
import { getEngagement } from '../../../../../lib/db/engagements'
import { getContact } from '../../../../../lib/db/contacts'
import {
  addEngagementContact,
  getEngagementContact,
  removeEngagementContact,
  setEngagementContactPrimary,
  ENGAGEMENT_CONTACT_ROLES,
} from '../../../../../lib/db/engagement-contacts'
import type { EngagementContactRole } from '../../../../../lib/db/engagement-contacts'
import { appendContext } from '../../../../../lib/db/context'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/engagements/:id/contacts
 *
 * Manages engagement_contacts rows: add a role assignment, remove one, or
 * mark a row as the primary POC. Single endpoint with action dispatcher,
 * mirroring the parking-lot.ts pattern.
 *
 * Protected by auth middleware (requires admin role).
 *
 * Form fields for create (default action):
 *   - contact_id (required, must belong to engagement's entity)
 *   - role (required, one of: owner | decision_maker | champion)
 *   - is_primary (optional, "1" to set)
 *   - notes (optional)
 *
 * Form fields for set-primary:
 *   - action: "set_primary"
 *   - engagement_contact_id (required)
 *
 * Form fields for delete:
 *   - _method: "DELETE"
 *   - engagement_contact_id (required)
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const engagementId = params.id
  if (!engagementId) {
    return new Response(JSON.stringify({ error: 'Engagement ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
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

    // DELETE
    if (method === 'DELETE') {
      const ecId = formData.get('engagement_contact_id')
      if (!ecId || typeof ecId !== 'string') {
        return redirect(`${detailUrl}?error=missing`, 302)
      }
      const existing = await getEngagementContact(env.DB, session.orgId, ecId.trim())
      if (!existing || existing.engagement_id !== engagementId) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }
      await removeEngagementContact(env.DB, session.orgId, ecId.trim())

      const contact = await getContact(env.DB, session.orgId, existing.contact_id)
      await appendContext(env.DB, session.orgId, {
        entity_id: engagement.entity_id,
        type: 'engagement_log',
        content: `Removed ${existing.role} role assignment for ${contact?.name ?? 'contact'}`,
        source: 'admin',
        source_ref: `engagement_contact:${existing.id}:removed`,
        metadata: {
          engagement_contact_id: existing.id,
          contact_id: existing.contact_id,
          role: existing.role,
        },
        engagement_id: engagementId,
      })

      return redirect(`${detailUrl}?engagement_contact_removed=1`, 302)
    }

    // Set primary
    if (action === 'set_primary') {
      const ecId = formData.get('engagement_contact_id')
      if (!ecId || typeof ecId !== 'string') {
        return redirect(`${detailUrl}?error=missing`, 302)
      }
      const existing = await getEngagementContact(env.DB, session.orgId, ecId.trim())
      if (!existing || existing.engagement_id !== engagementId) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }

      const updated = await setEngagementContactPrimary(env.DB, session.orgId, ecId.trim())
      if (!updated) {
        return redirect(`${detailUrl}?error=not_found`, 302)
      }

      const contact = await getContact(env.DB, session.orgId, existing.contact_id)
      await appendContext(env.DB, session.orgId, {
        entity_id: engagement.entity_id,
        type: 'engagement_log',
        content: `Set ${contact?.name ?? 'contact'} as primary POC`,
        source: 'admin',
        source_ref: `engagement_contact:${existing.id}:set_primary`,
        metadata: {
          engagement_contact_id: existing.id,
          contact_id: existing.contact_id,
          role: existing.role,
        },
        engagement_id: engagementId,
      })

      return redirect(`${detailUrl}?engagement_contact_primary_set=1`, 302)
    }

    // Default action: add
    const contactIdRaw = formData.get('contact_id')
    const roleRaw = formData.get('role')
    const isPrimaryRaw = formData.get('is_primary')
    const notesRaw = formData.get('notes')

    if (!contactIdRaw || typeof contactIdRaw !== 'string' || !contactIdRaw.trim()) {
      return redirect(`${detailUrl}?error=missing_contact`, 302)
    }
    if (
      !roleRaw ||
      typeof roleRaw !== 'string' ||
      !ENGAGEMENT_CONTACT_ROLES.some((r) => r.value === roleRaw)
    ) {
      return redirect(`${detailUrl}?error=invalid_role`, 302)
    }

    const contactId = contactIdRaw.trim()

    // Org + entity scoping: contact must belong to this engagement's entity.
    const contact = await getContact(env.DB, session.orgId, contactId)
    if (!contact || contact.entity_id !== engagement.entity_id) {
      return redirect(`${detailUrl}?error=invalid_contact`, 302)
    }

    const isPrimary = isPrimaryRaw === '1' || isPrimaryRaw === 'on' || isPrimaryRaw === 'true'
    const notes =
      notesRaw && typeof notesRaw === 'string' && notesRaw.trim() ? notesRaw.trim() : null

    try {
      const created = await addEngagementContact(env.DB, engagementId, {
        contact_id: contactId,
        role: roleRaw as EngagementContactRole,
        is_primary: isPrimary,
        notes,
      })

      await appendContext(env.DB, session.orgId, {
        entity_id: engagement.entity_id,
        type: 'engagement_log',
        content: `Assigned ${contact.name} as ${roleRaw}${isPrimary ? ' (primary POC)' : ''}`,
        source: 'admin',
        source_ref: `engagement_contact:${created.id}:added`,
        metadata: {
          engagement_contact_id: created.id,
          contact_id: contactId,
          role: roleRaw,
          is_primary: isPrimary,
        },
        engagement_id: engagementId,
      })

      return redirect(`${detailUrl}?engagement_contact_added=1`, 302)
    } catch (err) {
      // UNIQUE(engagement_id, contact_id, role) constraint hit.
      const message = err instanceof Error ? err.message : String(err)
      if (message.includes('UNIQUE') || message.includes('constraint')) {
        return redirect(`${detailUrl}?error=duplicate_role`, 302)
      }
      throw err
    }
  } catch (err) {
    console.error('[api/admin/engagements/[id]/contacts] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
