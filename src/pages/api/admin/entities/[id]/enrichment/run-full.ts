import type { APIRoute } from 'astro'
import { enrichEntity } from '../../../../../../lib/enrichment'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/enrichment/run-full
 *
 * Force-run the full 12-module enrichment pipeline against an entity,
 * bypassing the `intelligence_brief` idempotency short-circuit. Used when
 * an entity was partially enriched and the operator wants to fill in the
 * missing modules without waiting for a cron pass.
 *
 * Detached via locals.cfContext.waitUntil — full enrichment can take 30+
 * seconds across 12 modules and several Claude calls; awaiting that on
 * the request path blew past Worker limits in the past (Error 1101).
 */
export const POST: APIRoute = async ({ params, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const entityId = params.id
  if (!entityId) return redirect('/admin/entities?error=missing', 302)

  const enrichPromise = enrichEntity(env, session.orgId, entityId, {
    mode: 'full',
    force: true,
    triggered_by: 'admin:run-full',
  }).catch((err: unknown) => {
    console.error('[api/admin/entities/enrichment/run-full] background error', { error: err })
  })
  if (locals.cfContext?.waitUntil) {
    locals.cfContext.waitUntil(enrichPromise)
  }

  return redirect(`/admin/entities/${entityId}?enrichment_run_full=1`, 302)
}
