import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import {
  resolveCouplesAccess,
  COUPLES_LANDING_PATH,
} from '../../../../../../lib/portal/couples-access'
import { recordPersonIntake } from '../../../../../../lib/db/couples-onboarding'
import { trimString } from '../../../../../../lib/api/helpers'

/**
 * POST /api/portal/products/hosted-agent/couples/intake
 *
 * One partner's own details: their name as they want the agent to use it, and
 * their consent (recorded once, with its time and the consent wording's
 * version). Writes the caller's own partner row only; the row is resolved from
 * the session, never from the form, so a partner cannot write the other's.
 */

function back(st: string): Response {
  return new Response(null, {
    status: 303,
    headers: { Location: `${COUPLES_LANDING_PATH}?st=${st}` },
  })
}

export const POST: APIRoute = async ({ locals, request }) => {
  const access = await resolveCouplesAccess(env.DB, locals)
  if (access.kind === 'redirect') {
    return new Response(null, { status: 303, headers: { Location: access.to } })
  }
  const form = await request.formData()
  const name = trimString(form.get('name'))
  if (!name || name.length > 80) return back('invalid')
  if (form.get('consent') !== '1') return back('consent_required')

  await recordPersonIntake(env.DB, access.person.id, { name, consent: true })
  return back('details_saved')
}
