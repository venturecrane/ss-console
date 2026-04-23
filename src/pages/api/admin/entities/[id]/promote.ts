import type { APIRoute } from 'astro'
import { getEntity, transitionStage } from '../../../../../lib/db/entities'
import { enrichEntity } from '../../../../../lib/enrichment'
import { scheduleProspectCadence } from '../../../../../lib/follow-ups/scheduler'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/promote
 *
 * Thin wrapper retained as the transport for the "Promote" button on the
 * admin signal row — it still handles the signal → prospect stage transition
 * and schedules the prospect follow-up cadence. Enrichment itself now runs
 * automatically at signal ingest (see src/lib/enrichment/index.ts and the
 * lead-gen workers), so on the typical signal this endpoint finds the entity
 * already fully enriched and `enrichEntity` returns `alreadyEnriched: true`
 * without re-billing Claude. For entities that arrived before the at-ingest
 * refactor shipped, the call does an on-demand backfill.
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
  if (!entityId) {
    return redirect('/admin/entities?error=missing', 302)
  }

  try {
    // Stage transition first so the rest of the UI reads "prospect" while
    // enrichment fills in (enrichment is idempotent and safe to re-run).
    await transitionStage(env.DB, session.orgId, entityId, 'prospect', 'Promoted from signal.')

    const entity = await getEntity(env.DB, session.orgId, entityId)
    if (!entity) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    // Backfill enrichment if this signal predates the at-ingest refactor.
    // No-op when an intelligence_brief already exists.
    await enrichEntity(env, session.orgId, entityId, { mode: 'full' })

    try {
      await scheduleProspectCadence(env.DB, session.orgId, entityId, new Date().toISOString())
    } catch (err) {
      console.error('[promote] Follow-up cadence scheduling failed (non-blocking):', err)
    }

    return redirect(`/admin/entities/${entityId}?promoted=1`, 302)
  } catch (err) {
    console.error('[api/admin/entities/promote] Error:', err)
    const message = err instanceof Error ? err.message : 'server'
    return redirect(`/admin/entities?error=${encodeURIComponent(message)}`, 302)
  }
}
