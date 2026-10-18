/**
 * POST /api/internal/telegram-bind (ADR 0091 s5, plan R4)
 *
 * A couples seat's bot received `/start <token>` from a Telegram user it does
 * not know. The seat's gate forwards the token and the sender's numeric id
 * here, authenticated by the seat's own Machine credential (machine-key.ts:
 * `Authorization: Bearer <key>` + `X-Tenant-Slug`). The console redeems the
 * token and records the binding on the partner row.
 *
 * WHAT THIS DOES NOT DO. It admits no one to the agent. The seat's Telegram
 * allowlist is authored config: the bound id reaches customer.yaml only
 * through the generator's reviewed PR, and the seat reads it on restart.
 *
 * Body: { "token": "<43 chars>", "telegram_user_id": "<digits>" }
 * 200 { "result": "bound", "person": "p1" | "p2" }
 * 200 { "result": "rejected" }  unknown, expired, used, wrong seat, or an id
 *                               already bound to the other partner. One
 *                               answer for all of them, so a caller learns
 *                               nothing about why.
 * 401 bad or missing credential; 400 malformed body.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { errorResponse, isRecord, jsonResponse } from '../../../lib/api/helpers'
import { verifyMachineRequest } from '../../../lib/auth/machine-key'
import { redeemLinkToken } from '../../../lib/db/couples-link-tokens'
import { failedResponse } from '../../../lib/api/failures'

export const POST: APIRoute = async ({ request }) => {
  const auth = await verifyMachineRequest(request, env.DB)
  if (!auth.ok) return errorResponse(401, 'unauthorized')

  let body: unknown
  try {
    body = await request.json()
  } catch {
    return errorResponse(400, 'invalid_json')
  }
  if (!isRecord(body)) return errorResponse(400, 'invalid_json')
  const token = body['token']
  const telegramUserId = body['telegram_user_id']
  if (typeof token !== 'string' || typeof telegramUserId !== 'string') {
    return errorResponse(400, 'invalid_body')
  }

  try {
    const outcome = await redeemLinkToken(env.DB, {
      token,
      seatSlug: auth.slug,
      telegramUserId,
    })
    if (outcome.result === 'bound') {
      return jsonResponse(200, { result: 'bound', person: outcome.personKey })
    }
    return jsonResponse(200, { result: 'rejected' })
  } catch (err) {
    return failedResponse(err, 'telegram-bind')
  }
}
