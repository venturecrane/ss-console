/**
 * POST /api/admin/operator/requests/acknowledge
 * POST /api/admin/operator/requests/resolve
 * POST /api/admin/operator/requests/decline
 *
 * SMD actions on a client change request (design §4.4). Body:
 *
 *   { id: number, resolution_note?: string | null }
 *
 * - acknowledge → records receipt (stays in the inbox, not closed)
 * - resolve     → terminal; SMD made the change
 * - decline     → terminal; note recommended
 *
 * The resolver (updateChangeRequestStatus) stamps resolved_by_email +
 * resolved_at for terminal states — that is the inline audit of the Layer-0 SMD
 * actor for this control-plane mutation (foundations §6). Admin-only, enforced
 * by middleware on /api/admin/* and re-checked here.
 */

import { jsonResponse, errorResponse } from '../../../../../lib/api/helpers'
import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { updateChangeRequestStatus } from '../../../../../lib/portal/operator/change-request'
import { actionToStatus } from '../../../../../lib/admin/change-request-inbox'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

const MAX_NOTE_LENGTH = 4000

interface ParsedBody {
  id: number
  resolution_note: string | null
}

function parseBody(body: unknown): ParsedBody | { error: string } {
  if (!body || typeof body !== 'object') return { error: 'body must be a JSON object' }
  const obj = body as Record<string, unknown>
  const id = obj.id
  if (typeof id !== 'number' || !Number.isInteger(id) || id <= 0) {
    return { error: 'id is required and must be a positive integer' }
  }
  const note = obj.resolution_note
  if (note === undefined || note === null) return { id, resolution_note: null }
  if (typeof note !== 'string') return { error: 'resolution_note must be a string or null' }
  if (note.length > MAX_NOTE_LENGTH) return { error: `resolution_note exceeds ${MAX_NOTE_LENGTH}` }
  const trimmed = note.trim()
  return { id, resolution_note: trimmed === '' ? null : trimmed }
}

async function handlePost(ctx: APIContext): Promise<Response> {
  const auth = requireAdminSession(ctx.locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const status = actionToStatus(ctx.params.action ?? '')
  if (status === null) {
    return errorResponse(404, 'not_found', `unknown action: ${ctx.params.action}`)
  }

  let body: unknown
  try {
    body = await ctx.request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }

  const parsed = parseBody(body)
  if ('error' in parsed) return errorResponse(400, 'validation_failed', parsed.error)

  const updated = await updateChangeRequestStatus(env.DB, {
    id: parsed.id,
    status,
    resolved_by_email: session.email,
    resolution_note: parsed.resolution_note,
  })
  if (!updated) return errorResponse(404, 'not_found', 'change request not found.')
  return jsonResponse(200, { ok: true, id: parsed.id, status })
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
