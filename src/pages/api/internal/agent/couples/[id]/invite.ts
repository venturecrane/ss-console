/**
 * POST /api/internal/agent/couples/:id/invite
 *
 * Lets an SMD agent send ONE couples partner's portal invitation through the
 * website, which holds the live Clerk key. Same flow as the admin-console
 * button (src/lib/hosted-agent/couples/invite.ts); the inviter is the SMD
 * admin user (SMD_ADMIN_USER_ID).
 *
 * Auth: `Authorization: Bearer <SMD_AGENT_ADMIN_KEY>` (agent-admin-key.ts).
 * Unset secret = every request is 401.
 *
 * Scope: invitations only. It creates no instance, deletes nothing, and
 * changes no partner data beyond marking the partner invited.
 *
 * Body: { "person_key": "p1" | "p2" }  or  { "person_id": "<id>" }
 * 200 { "result": "invited", "invitation_id": "orginv_..." }
 * 200 { "result": "already_member" | "already_invited" }
 * 404 { "result": "invalid" }        unknown instance, or a partner not on it
 * 409 { "result": "no_clerk_user" }  the SMD admin user has no Clerk user id
 * 502 { "result": "invite_failed" }  Clerk refused for another reason
 * 401 bad or missing key; 400 malformed body.
 *
 * The invitee's address is never logged or returned.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { errorResponse, isRecord, jsonResponse } from '../../../../../../lib/api/helpers'
import { SMD_ADMIN_USER_ID, verifyAgentAdminKey } from '../../../../../../lib/auth/agent-admin-key'
import { listPeople } from '../../../../../../lib/db/couples-onboarding'
import {
  clerkUserIdFor,
  inviteCouplesPartner,
  type CouplesInviteResult,
} from '../../../../../../lib/hosted-agent/couples/invite'

const STATUS: Record<CouplesInviteResult, number> = {
  invited: 200,
  already_member: 200,
  already_invited: 200,
  invalid: 404,
  invite_failed: 502,
}

type PersonRef = { key: 'p1' | 'p2' } | { id: string }

function parsePersonRef(body: Record<string, unknown>): PersonRef | null {
  const key = body['person_key']
  const id = body['person_id']
  if (key !== undefined && id !== undefined) return null
  if (key === 'p1' || key === 'p2') return { key }
  if (typeof id === 'string' && id.trim().length > 0) return { id: id.trim() }
  return null
}

export const POST: APIRoute = async (context) => {
  if (!verifyAgentAdminKey(context.request, env.SMD_AGENT_ADMIN_KEY)) {
    return errorResponse(401, 'unauthorized')
  }

  const id = context.params.id
  if (typeof id !== 'string' || id.length === 0) return jsonResponse(404, { result: 'invalid' })

  let body: unknown
  try {
    body = await context.request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }
  if (!isRecord(body)) return errorResponse(400, 'invalid_json')
  const ref = parsePersonRef(body)
  if (!ref) return errorResponse(400, 'invalid_body')

  let personId: string
  if ('id' in ref) {
    personId = ref.id
  } else {
    const person = (await listPeople(env.DB, id)).find((p) => p.person_key === ref.key)
    if (!person) return jsonResponse(404, { result: 'invalid' })
    personId = person.id
  }

  const inviter = await clerkUserIdFor(env.DB, SMD_ADMIN_USER_ID)
  if (!inviter) return jsonResponse(409, { result: 'no_clerk_user' })

  const outcome = await inviteCouplesPartner(context, {
    instanceId: id,
    personId,
    inviterClerkUserId: inviter,
    source: 'agent',
  })
  return jsonResponse(STATUS[outcome.result], {
    result: outcome.result,
    ...(outcome.invitationId ? { invitation_id: outcome.invitationId } : {}),
  })
}
