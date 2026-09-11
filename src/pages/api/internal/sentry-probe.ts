/**
 * POST /api/internal/sentry-probe
 *
 * Deliberate uncaught-error probe for the Sentry middleware seam
 * (#1626 / ADR 0023). Throws after auth so the error propagates out of
 * the route handler and through `withSentryRequestHandler`
 * (src/middleware.ts) — the ONLY way to prove on the real runtime that
 * uncaught request errors reach the ss-web Sentry project. A green unit
 * test cannot prove this seam; this route exists so a `crane_verify`
 * record can.
 *
 * Auth: same shared Machine bearer + X-Tenant-Slug as
 * /api/internal/heartbeat (`src/lib/auth/machine-key.ts`) — internal
 * callers only; unauthenticated hits get a uniform 401 and produce no
 * Sentry event.
 *
 * Expected caller-visible result: HTTP 500. Expected far-end effect: an
 * event in Sentry project ss-web tagged with the probe message below.
 */
import { errorResponse } from '../../../lib/api/helpers'
import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { verifyMachineRequest } from '../../../lib/auth/machine-key'

export const POST: APIRoute = async ({ request }) => {
  const auth = await verifyMachineRequest(request, env.MACHINE_HEARTBEAT_KEY, env.DB)
  if (!auth.ok) {
    return errorResponse(auth.status, 'unauthorized')
  }

  throw new Error(`sentry-probe: deliberate uncaught error (tenant=${auth.slug})`)
}
