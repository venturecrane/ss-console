import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { getEntity } from '../../../../../lib/db/entities'
import { getOperatorAgreementKey } from '../../../../../lib/storage/r2'
import {
  createOperatorAgreementDocument,
  deleteOperatorAgreementDocument,
  isExecutedOnValid,
} from '../../../../../lib/db/operator-agreements'
import { getCustomerConfigBySlug } from '../../../../../lib/portal/customer-config'

/**
 * POST /api/admin/clients/:id/operator-agreements
 *
 * The human-reviewed admin flow behind the portal's Executed agreements card
 * (ss#2641). Title and execution date are typed by a person here; nothing
 * downstream derives either from the file. That is the whole reason this
 * endpoint exists rather than a bare R2 prefix listing — a date parsed out of
 * a filename and rendered to a client is CLAUDE.md's Pattern B.
 *
 * `action=delete` removes a recorded document instead of adding one.
 *
 * Every refusal below is a client-facing correctness guard, not hygiene:
 *   - no `executed_on`, a malformed one, or a FUTURE one. An agreement
 *     "executed" tomorrow is not executed, and a draft posted here would read
 *     to the firm as its operative terms.
 *   - an `instance_slug` that is not an operator instance of THIS entity.
 *     Without it a typo would publish one firm's paper into another's portal.
 *
 * Redirects back to the client hub with a flash; this is a form target, not
 * a JSON API.
 */

const MAX_BYTES = 20 * 1024 * 1024

function back(entityId: string, flash: string): Response {
  return new Response(null, {
    status: 303,
    headers: { Location: `/admin/clients/${entityId}?agreement=${flash}` },
  })
}

interface UploadFields {
  instanceSlug: string
  title: string
  executedOn: string
  file: File
}

/** Validate the authored fields. Returns a flash key on the first refusal. */
function parseUploadFields(form: FormData): UploadFields | string {
  const instanceSlug = form.get('instance_slug')
  const title = form.get('title')
  const executedOn = form.get('executed_on')
  const file = form.get('file')

  if (typeof instanceSlug !== 'string' || instanceSlug === '') return 'no_instance'
  if (typeof title !== 'string' || title.trim() === '') return 'no_title'
  if (typeof executedOn !== 'string' || !isExecutedOnValid(executedOn)) return 'bad_date'
  if (!(file instanceof File) || file.size === 0) return 'no_file'
  if (file.size > MAX_BYTES) return 'too_large'

  return { instanceSlug, title: title.trim(), executedOn, file }
}

/** Remove a recorded document: the D1 row first, then the object. An orphaned
 * R2 object is invisible and harmless; a row pointing at deleted bytes is a
 * broken link on a client's portal page. */
async function removeDocument(orgId: string, entityId: string, form: FormData): Promise<Response> {
  const id = form.get('document_id')
  if (typeof id !== 'string' || id === '') return back(entityId, 'failed')

  const removed = await deleteOperatorAgreementDocument(env.DB, orgId, id)
  if (!removed) return back(entityId, 'not_found')

  try {
    await env.STORAGE.delete(removed.storage_key)
  } catch (err) {
    console.error('[admin/operator-agreements] R2 delete failed:', err)
  }
  return back(entityId, 'removed')
}

async function recordDocument(
  orgId: string,
  entityId: string,
  userId: string,
  fields: UploadFields
): Promise<Response> {
  // The slug must belong to this entity. A foreign or unknown slug would put
  // one firm's executed paper on another firm's Compliance page.
  const config = await getCustomerConfigBySlug(env.DB, fields.instanceSlug)
  if (!config || config.entity_id !== entityId) return back(entityId, 'no_instance')

  // The row id is generated here, before the put, because the key is built
  // from it: two documents with the same filename are two rows and two
  // objects (A3). Put first, then insert — an orphan object is harmless, a
  // row pointing at nothing is a 404 on the client's Compliance page.
  const documentId = crypto.randomUUID()
  const key = getOperatorAgreementKey(orgId, fields.instanceSlug, documentId, fields.file.name)
  await env.STORAGE.put(key, await fields.file.arrayBuffer(), {
    httpMetadata: { contentType: fields.file.type || 'application/octet-stream' },
    customMetadata: { originalName: fields.file.name, uploadedAt: new Date().toISOString() },
  })
  await createOperatorAgreementDocument(env.DB, {
    id: documentId,
    org_id: orgId,
    entity_id: entityId,
    instance_slug: fields.instanceSlug,
    title: fields.title,
    executed_on: fields.executedOn,
    storage_key: key,
    file_name: key.split('/').pop() ?? fields.file.name,
    uploaded_by: userId,
  })
  return back(entityId, 'recorded')
}

export const POST: APIRoute = async ({ locals, params, request }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth
  const entityId = params.id
  if (!entityId) return new Response(null, { status: 303, headers: { Location: '/admin/clients' } })

  const entity = await getEntity(env.DB, session.orgId, entityId)
  if (!entity) {
    return new Response(null, {
      status: 303,
      headers: { Location: '/admin/clients?error=not_found' },
    })
  }

  let form: FormData
  try {
    form = await request.formData()
  } catch {
    return back(entityId, 'failed')
  }

  try {
    if (form.get('action') === 'delete') {
      return await removeDocument(session.orgId, entityId, form)
    }
    const fields = parseUploadFields(form)
    if (typeof fields === 'string') return back(entityId, fields)
    return await recordDocument(session.orgId, entityId, session.userId, fields)
  } catch (err) {
    console.error('[admin/operator-agreements] request failed:', err)
    return back(entityId, 'failed')
  }
}
