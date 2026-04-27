/**
 * GET /api/scan/verify?token=... — magic-link click handler (#598).
 *
 * Verifies the inbound token against the SHA-256 hash on file, marks
 * the scan_request as `verified`, and kicks off the pruned enrichment
 * pipeline via `ctx.waitUntil()` so the response returns instantly.
 *
 * Per Captain's `feedback_ctx_waituntil_for_heavy_work` memory: heavy
 * work in Workers request handlers MUST go through ctx.waitUntil — a
 * pipeline that issues 4 Claude calls would otherwise blow the 30s
 * single-invocation budget AND wedge the prospect's verification UX.
 *
 * The verify endpoint is also used directly by the Astro page
 * `/scan/verify/[token].astro` via a `redirect=1` query string, so the
 * UX is: click magic-link → land on a friendly page → that page POSTs
 * here for the actual verification + side-effects.
 *
 * The endpoint exposes both GET (link click, redirects to confirmation
 * page) and POST (Astro page programmatic call). They share the same
 * verify logic.
 */

import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { hashScanToken, isScanTokenFresh } from '../../../lib/scan/tokens'
import { getScanRequestByTokenHash, markScanVerified } from '../../../lib/db/scan-requests'
import { runDiagnosticScan } from '../../../lib/diagnostic'

interface VerifyResponse {
  ok: boolean
  status:
    | 'verified'
    | 'already_completed'
    | 'expired'
    | 'invalid_token'
    | 'thin_footprint'
    | 'failed'
  /** Domain echoed back so the confirmation page can name the scan. Never
   *  echoed when the token is invalid (no info leak). */
  domain?: string
}

async function handleVerify(token: string, locals: App.Locals): Promise<VerifyResponse> {
  if (!token || typeof token !== 'string') return { ok: false, status: 'invalid_token' }

  const hash = await hashScanToken(token)
  const row = await getScanRequestByTokenHash(env.DB, hash)
  if (!row) return { ok: false, status: 'invalid_token' }

  if (row.scan_status === 'completed') {
    return { ok: true, status: 'already_completed', domain: row.domain }
  }
  if (row.scan_status === 'thin_footprint') {
    return { ok: true, status: 'thin_footprint', domain: row.domain }
  }
  if (row.scan_status === 'failed') {
    return { ok: false, status: 'failed', domain: row.domain }
  }

  // Token must still be within its 24-hour TTL.
  if (!isScanTokenFresh(row.created_at)) {
    return { ok: false, status: 'expired', domain: row.domain }
  }

  // Mark verified and kick off the scan via waitUntil.
  await markScanVerified(env.DB, row.id)

  const ctx = locals.cfContext
  const scanPromise = runDiagnosticScan(
    {
      DB: env.DB,
      ANTHROPIC_API_KEY: env.ANTHROPIC_API_KEY,
      GOOGLE_PLACES_API_KEY: env.GOOGLE_PLACES_API_KEY,
      OUTSCRAPER_API_KEY: env.OUTSCRAPER_API_KEY,
      RESEND_API_KEY: env.RESEND_API_KEY,
      APP_BASE_URL: env.APP_BASE_URL,
    },
    row.id
  )

  if (ctx?.waitUntil) {
    ctx.waitUntil(scanPromise)
  } else {
    // Dev fallback — no execution context. Fire-and-forget the promise
    // and swallow rejections so an open handle doesn't crash the test
    // runner. Production always has a cfContext, so this branch is for
    // unit tests / `astro dev`.
    scanPromise.catch((err) => console.error('[api/scan/verify] dev fallback scan threw:', err))
  }

  return { ok: true, status: 'verified', domain: row.domain }
}

export const GET: APIRoute = async ({ url, locals }) => {
  const token = url.searchParams.get('token') ?? ''
  const result = await handleVerify(token, locals)
  return jsonResponse(result.ok ? 200 : 400, result)
}

export const POST: APIRoute = async ({ request, locals }) => {
  let body: Record<string, unknown> = {}
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { ok: false, status: 'invalid_token' })
  }
  const token = typeof body.token === 'string' ? body.token : ''
  const result = await handleVerify(token, locals)
  return jsonResponse(result.ok ? 200 : 400, result)
}

function jsonResponse(status: number, data: VerifyResponse): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
