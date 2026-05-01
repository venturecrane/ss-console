import type { APIRoute } from 'astro'
import { dispatchEnrichmentWorkflow } from '../../../../../lib/enrichment/dispatch'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/dossier
 *
 * "Re-enrich" action the admin can trigger for stale-data refresh. Runs
 * the cheap `reviews-and-news` mode (review patterns + review synthesis +
 * news search + intelligence_brief backfill if missing + outreach draft
 * regeneration).
 *
 * UX change (#631): the legacy implementation awaited enrichment inline,
 * blocking the redirect for 5-15 seconds while Claude generated the
 * refresh. Enrichment now runs on the dedicated EnrichmentWorkflow Worker;
 * we dispatch via `ctx.waitUntil` and redirect immediately with
 * `?dossier=queued`. The entity page renders a "Re-enrichment queued —
 * refresh in 30s" banner when the param is `queued`. The legacy `?dossier=1`
 * query param is kept as an alias so old bookmarks don't break.
 *
 * The "Generate Dossier" button itself was removed from the admin UI in
 * issue #471. This endpoint backs the "Re-enrich" toolbar action.
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

  // Dispatch in the background so the redirect lands instantly. The
  // Workflow runs the cheap reviews-and-news pipeline on the dedicated
  // Worker; failures are recorded as failed runs in `enrichment_runs`
  // and visible in the Cloudflare Workflows dashboard.
  const dispatchPromise = dispatchEnrichmentWorkflow(env, {
    entityId,
    orgId: session.orgId,
    mode: 'reviews-and-news',
    triggered_by: 'admin:re-enrich',
  }).catch((err) => {
    console.error('[api/admin/entities/dossier] dispatch failed:', err)
  })
  if (locals.cfContext?.waitUntil) {
    locals.cfContext.waitUntil(dispatchPromise)
  }

  return redirect(`/admin/entities/${entityId}?dossier=queued`, 302)
}
