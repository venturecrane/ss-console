import type { APIRoute } from 'astro'
import { enrichEntity } from '../../../../../lib/enrichment'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/dossier
 *
 * Thin wrapper — the original endpoint generated the full intelligence
 * dossier on admin click, which is now handled automatically at signal
 * ingest (see src/lib/enrichment/index.ts). This route is kept as a
 * "Re-enrich" action the admin can trigger for stale data refresh. It runs
 * the cheap `reviews-and-news` mode (review patterns + review synthesis +
 * news search + outreach draft regeneration) rather than re-billing every
 * module on an already-enriched entity.
 *
 * The "Generate Dossier" button itself was removed from the admin UI in
 * issue #471. Callers still linking to the old path end up on the entity
 * page with a `dossier=1` query param, matching the legacy success redirect.
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

  try {
    await enrichEntity(env, session.orgId, entityId, {
      mode: 'reviews-and-news',
      triggered_by: 'admin:re-enrich',
    })
    return redirect(`/admin/entities/${entityId}?dossier=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/dossier] Error:', err)
    return redirect(`/admin/entities/${entityId}?error=re_enrich_failed`, 302)
  }
}
