import type { APIContext, APIRoute } from 'astro'
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
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * POST /api/admin/engagements/:id/contacts
 *
 * Manages engagement_contacts rows: add, remove, or set-primary.
 *
 * Protected by auth middleware (requires admin role).
 */

type Redirect = APIContext['redirect']

interface ContactActionArgs {
  redirect: Redirect
  orgId: string
  engagementId: string
  entityId: string
  detailUrl: string
  formData: FormData
}

async function handleDelete({
  redirect,
  orgId,
  engagementId,
  entityId,
  detailUrl,
  formData,
}: ContactActionArgs): Promise<Response> {
  const ecId = formData.get('engagement_contact_id')
  if (!ecId || typeof ecId !== 'string') return redirect(`${detailUrl}?error=missing`, 302)
  const existing = await getEngagementContact(env.DB, orgId, ecId.trim())
  if (!existing || existing.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }
  await removeEngagementContact(env.DB, orgId, ecId.trim())
  const contact = await getContact(env.DB, orgId, existing.contact_id)
  await appendContext(env.DB, orgId, {
    entity_id: entityId,
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

async function handleSetPrimary({
  redirect,
  orgId,
  engagementId,
  entityId,
  detailUrl,
  formData,
}: ContactActionArgs): Promise<Response> {
  const ecId = formData.get('engagement_contact_id')
  if (!ecId || typeof ecId !== 'string') return redirect(`${detailUrl}?error=missing`, 302)
  const existing = await getEngagementContact(env.DB, orgId, ecId.trim())
  if (!existing || existing.engagement_id !== engagementId) {
    return redirect(`${detailUrl}?error=not_found`, 302)
  }
  const updated = await setEngagementContactPrimary(env.DB, orgId, ecId.trim())
  if (!updated) return redirect(`${detailUrl}?error=not_found`, 302)
  const contact = await getContact(env.DB, orgId, existing.contact_id)
  await appendContext(env.DB, orgId, {
    entity_id: entityId,
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

function validateAddForm(
  formData: FormData,
  detailUrl: string,
  redirect: Redirect
): { contactIdRaw: string; roleRaw: string; isPrimary: boolean; notes: string | null } | Response {
  const contactIdRaw = formData.get('contact_id')
  const roleRaw = formData.get('role')
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
  const isPrimaryRaw = formData.get('is_primary')
  const notesRaw = formData.get('notes')
  const isPrimary = isPrimaryRaw === '1' || isPrimaryRaw === 'on' || isPrimaryRaw === 'true'
  const notes = notesRaw && typeof notesRaw === 'string' && notesRaw.trim() ? notesRaw.trim() : null
  return { contactIdRaw, roleRaw, isPrimary, notes }
}

async function handleAdd({
  redirect,
  orgId,
  engagementId,
  entityId,
  detailUrl,
  formData,
}: ContactActionArgs): Promise<Response> {
  const validated = validateAddForm(formData, detailUrl, redirect)
  if (validated instanceof Response) return validated
  const { contactIdRaw, roleRaw, isPrimary, notes } = validated

  const contactId = contactIdRaw.trim()
  const contact = await getContact(env.DB, orgId, contactId)
  if (!contact || contact.entity_id !== entityId) {
    return redirect(`${detailUrl}?error=invalid_contact`, 302)
  }

  try {
    const created = await addEngagementContact(env.DB, engagementId, {
      contact_id: contactId,
      role: roleRaw as EngagementContactRole,
      is_primary: isPrimary,
      notes,
    })
    await appendContext(env.DB, orgId, {
      entity_id: entityId,
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
    const message = err instanceof Error ? err.message : String(err)
    if (message.includes('UNIQUE') || message.includes('constraint')) {
      return redirect(`${detailUrl}?error=duplicate_role`, 302)
    }
    throw err
  }
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const engagementId = params.id
  if (!engagementId) {
    return errorResponse(400, 'validation_failed', 'Engagement ID required.')
  }

  try {
    const engagement = await getEngagement(env.DB, session.orgId, engagementId)
    if (!engagement) return redirect('/admin/entities?error=not_found', 302)

    const formData = await request.formData()
    const method = formData.get('_method')
    const action = formData.get('action')
    const detailUrl = `/admin/engagements/${engagementId}`

    const args: ContactActionArgs = {
      redirect,
      orgId: session.orgId,
      engagementId,
      entityId: engagement.entity_id,
      detailUrl,
      formData,
    }
    if (method === 'DELETE') return handleDelete(args)
    if (action === 'set_primary') return handleSetPrimary(args)
    return handleAdd(args)
  } catch (err) {
    console.error('[api/admin/engagements/[id]/contacts] Error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
