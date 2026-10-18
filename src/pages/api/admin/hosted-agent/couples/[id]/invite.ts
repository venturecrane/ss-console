import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../../lib/auth/admin-session'
import { trimString } from '../../../../../../lib/api/helpers'
import {
  clerkUserIdFor,
  inviteCouplesPartner,
} from '../../../../../../lib/hosted-agent/couples/invite'

/**
 * POST /api/admin/hosted-agent/couples/:id/invite (ADR 0090 decision 4)
 *
 * Invites ONE partner (form field `person_id`) to the couple's portal from the
 * admin console. The flow itself (Clerk org on first invite, org:member
 * invitation, partner row marked invited) lives in
 * src/lib/hosted-agent/couples/invite.ts, shared with the SMD agent route
 * /api/internal/agent/couples/:id/invite.
 *
 * Sending is an explicit per-partner click, never part of creating the
 * instance. The outcome maps to a status banner; the invitee's address is not
 * logged.
 */

function back(id: string, st: string): Response {
  return new Response(null, {
    status: 303,
    headers: { Location: `/admin/hosted-agent/couples/${id}?st=${encodeURIComponent(st)}` },
  })
}

export const POST: APIRoute = async (context) => {
  const auth = requireAdminSession(context.locals)
  if (!auth.ok) return auth.response

  const id = context.params.id
  if (typeof id !== 'string' || id.length === 0) return back('', 'invalid')

  const personId = trimString((await context.request.formData()).get('person_id'))
  if (!personId) return back(id, 'invalid')

  const inviter = await clerkUserIdFor(env.DB, auth.session.userId)
  if (!inviter) return back(id, 'no_clerk_user')

  const outcome = await inviteCouplesPartner(context, {
    instanceId: id,
    personId,
    inviterClerkUserId: inviter,
    source: 'admin',
  })
  return back(id, outcome.result)
}
