/**
 * POST /api/internal/operator-request-card
 *
 * A seat reports one request a person emailed it, and SMD ops gets one email
 * (migration 0122). Kinds: `replied` (first reply settled), `no_reply` (the
 * alarm: nothing answered inside the seat's window), `job_done` (a queued job
 * the request started reached a terminal state).
 *
 * Auth: per-seat Bearer key + X-Tenant-Slug header, the heartbeat's
 * credential (`src/lib/auth/machine-key.ts`).
 *
 * Body: the wire contract in `src/lib/operator/request-card.ts`, mirrored by
 * the overlay's `shared/request_cards.py`.
 *
 * Responses (the seat treats any 200 as done and retries anything else):
 *   200 {ok:true}                  emailed now
 *   200 {ok:true, duplicate:true}  this card was already emailed
 *   400 invalid_json | invalid_body (with `field`)
 *   401 unauthorized
 *   502 upstream_failed            Resend refused or was unreachable; retry
 *
 * ADR 0052 s5: the card's text (sender, subject, reply opening, matter) goes
 * into the email only. D1 receives the card's numbers and keys.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { errorResponse, isRecord, jsonResponse } from '../../../lib/api/helpers'
import { failedResponse } from '../../../lib/api/failures'
import { verifyMachineRequest } from '../../../lib/auth/machine-key'
import {
  markCardAttempt,
  parseRequestCard,
  recordCard,
  sendCardEmail,
} from '../../../lib/operator/request-card'

export const POST: APIRoute = async ({ request }) => {
  const auth = await verifyMachineRequest(request, env.DB)
  if (!auth.ok) return errorResponse(401, 'unauthorized')

  let parsed: unknown
  try {
    parsed = await request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }
  if (!isRecord(parsed)) return errorResponse(400, 'invalid_json')

  const result = parseRequestCard(parsed)
  if (!result.ok) {
    return errorResponse(400, 'invalid_body', undefined, { field: result.field })
  }
  const card = result.card

  const { alreadySent } = await recordCard(env.DB, auth.slug, card)
  if (alreadySent) return jsonResponse(200, { ok: true, duplicate: true })

  const sent = await sendCardEmail(env, auth.slug, card)
  await markCardAttempt(env.DB, auth.slug, card.cardKey, sent.ok)
  if (!sent.ok) {
    // The detail is a status code or a transport message; never card text.
    // Captured to Sentry: the seat retries, but a send that keeps failing is
    // SMD going blind to requests, and that pages.
    const err = new Error(`${auth.slug} ${card.kind} card not sent: ${sent.detail ?? 'unknown'}`)
    return failedResponse(err, 'operator-request-card', { status: 502, code: 'upstream_failed' })
  }
  return jsonResponse(200, { ok: true })
}
