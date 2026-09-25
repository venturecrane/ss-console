/**
 * POST /api/admin/operator/[customer]/clear-stop
 *
 * Captain clears a cost-breaker HARD_STOP/SOFT_STOP for one Operator (ADR 0062
 * §6, #1701). Admin-only (middleware on /api/admin/* + explicit re-check).
 * Authenticates the Captain, proxies the reset to the Machine gate, and records
 * the governance row in `operator_stop_clears`.
 *
 * Form field: `reason` (required — the sticky_stop clear() contract).
 * Status banner on redirect to the operator detail page:
 *   ?clear=ok        — cleared; resulting level returned by the gate
 *   ?clear=invalid   — missing/blank reason
 *   ?clear=not_found — no operator with that slug
 *   ?clear=error     — transport/gate failure (operator stays paused)
 */

import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveEntityIdBySlug } from '../../../../../lib/admin/operator-overview'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { clearStopOnMachine, recordStopClear } from '../../../../../lib/operator/sticky-stop-clear'

function redirect(slug: string, status: string): Response {
  const target = `/admin/operator/${encodeURIComponent(slug)}?clear=${encodeURIComponent(status)}`
  return new Response(null, { status: 303, headers: { Location: target } })
}

async function handlePost(ctx: APIContext): Promise<Response> {
  const auth = requireAdminSession(ctx.locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const slug = ctx.params.customer ?? ''
  const entityId = await resolveEntityIdBySlug(env.DB, slug)
  if (!entityId) return redirect(slug, 'not_found')

  const form = await ctx.request.formData()
  const reason = form.get('reason')
  if (typeof reason !== 'string' || reason.trim().length === 0) {
    return redirect(slug, 'invalid')
  }

  try {
    const result = await clearStopOnMachine(env, slug, {
      captain_id: session.email,
      reason: reason.trim(),
    })
    await recordStopClear(env.DB, {
      entity_id: entityId,
      customer_slug: slug,
      actor_user_id: session.userId,
      actor_email: session.email,
      actor_role: session.role,
      reason: reason.trim(),
      result,
    })
    return redirect(slug, 'ok')
  } catch (err) {
    console.error('clear-stop failed', err instanceof Error ? err.message : String(err))
    return redirect(slug, 'error')
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
