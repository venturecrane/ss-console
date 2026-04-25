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
 * refactor shipped, the call does an on-demand backfill — but it runs under
 * ctx.waitUntil so a 12-module pipeline doesn't blow past Worker CPU /
 * subrequest limits and crash the redirect (Error 1101 on admin click).
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

    // Enrichment backfill runs in the background. On the typical path the
    // entity was already enriched at ingest so this resolves immediately via
    // the alreadyEnriched short-circuit. On backfill it can hit 10+ external
    // APIs and multiple Claude calls — awaiting that from the request path
    // blew past Worker limits (Error 1101) on ~50% of promotes.
    const enrichPromise = enrichEntity(env, session.orgId, entityId, {
      mode: 'full',
      triggered_by: 'admin:promote',
    }).catch((err) => {
      console.error('[promote] Background enrichment failed', { error: err })
    })
    if (locals.cfContext?.waitUntil) {
      locals.cfContext.waitUntil(enrichPromise)
    }

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
