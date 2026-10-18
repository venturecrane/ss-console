import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { getIntakeById, setIntakeCustomerSlug } from '../../../../../lib/db/hosted-agent-intake'
import { trimString } from '../../../../../lib/api/helpers'

/**
 * POST /api/admin/hosted-agent/:id/assign-slug (ADR 0067)
 *
 * Captain names the per-customer slug (Fly app hermes-<slug>, Infisical
 * path /ss/hosted/<slug>). Assigned BEFORE the customer is asked for
 * their key so the write-only relay has a vault path to land on. Also
 * mirrored into subscriptions.settings_json for cross-referencing.
 */

const QUEUE = '/admin/hosted-agent'
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{0,31}$/

function back(st: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${QUEUE}?st=${st}` } })
}

export const POST: APIRoute = async ({ locals, params, request }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response

  const id = params.id
  if (typeof id !== 'string' || id.length === 0) return back('invalid')

  const intake = await getIntakeById(env.DB, id)
  if (!intake) return back('invalid')

  const form = await request.formData()
  const slug = trimString(form.get('customer_slug'))
  if (!slug || !SLUG_PATTERN.test(slug)) return back('invalid')

  try {
    await setIntakeCustomerSlug(env.DB, id, slug)
    await env.DB.prepare(
      `UPDATE subscriptions
            SET settings_json = json_set(COALESCE(settings_json, '{}'), '$.customer_slug', ?),
                updated_at = datetime('now')
          WHERE id = ?`
    )
      .bind(slug, intake.subscription_id)
      .run()
  } catch (err) {
    console.error('[admin/hosted-agent] assign-slug failed:', err)
    return back('error')
  }
  return back('slug_saved')
}
