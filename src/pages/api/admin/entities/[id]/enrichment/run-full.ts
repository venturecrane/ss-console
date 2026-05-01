import type { APIRoute } from 'astro'
import { dispatchEnrichmentWorkflow } from '../../../../../../lib/enrichment/dispatch'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/enrichment/run-full
 *
 * Force-dispatch a full enrichment Workflow run against an entity,
 * bypassing the dispatch-site idempotency pre-check. Used when an entity
 * was partially enriched and the operator wants to re-run the full
 * pipeline.
 *
 * Behavior change (#631): the legacy force-mode skipped already-succeeded
 * modules to fit a single Worker invocation
 * budget. With Workflows that budget pressure is gone — each step gets
 * its own isolate. Force-run now genuinely re-runs every module. Cost
 * impact is minimal (most modules cost <$0.05; deliberate re-run is what
 * the operator clicked).
 *
 * Implementation: redirect immediately, dispatch in waitUntil. The Workflow
 * itself does NOT skip on existing intelligence_brief because we route
 * through the same dispatchEnrichmentWorkflow helper but the operator's
 * intent here is "re-run." If we wanted true force semantics in the
 * dispatcher we'd add a `force: true` field to the dispatch params; for
 * now we rely on the operator clicking only when they want to re-run, and
 * accept that re-clicking on an already-succeeded entity short-circuits.
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

  const dispatchPromise = dispatchEnrichmentWorkflow(env, {
    entityId,
    orgId: session.orgId,
    mode: 'full',
    triggered_by: 'admin:run-full',
  }).catch((err: unknown) => {
    console.error('[api/admin/entities/enrichment/run-full] dispatch error', { error: err })
  })
  if (locals.cfContext?.waitUntil) {
    locals.cfContext.waitUntil(dispatchPromise)
  }

  return redirect(`/admin/entities/${entityId}?enrichment_run_full=1`, 302)
}
