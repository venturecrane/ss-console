import type { APIRoute } from 'astro'
import { getEntity, transitionStage } from '../../../../../lib/db/entities'
import { dispatchEnrichmentWorkflow } from '../../../../../lib/enrichment/dispatch'
import { scheduleProspectCadence } from '../../../../../lib/follow-ups/scheduler'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/promote
 *
 * Thin wrapper retained as the transport for the "Promote" button on the
 * admin signal row — it still handles the signal → prospect stage transition
 * and schedules the prospect follow-up cadence. Enrichment itself runs on
 * the dedicated EnrichmentWorkflow Worker (#631); on the typical signal
 * the dispatch's idempotency pre-check finds an existing
 * `intelligence_brief` and short-circuits without creating a Workflow,
 * preserving the prior `alreadyEnriched: true` semantic. For entities
 * that arrived before the at-ingest refactor shipped, dispatch creates a
 * Workflow that runs the full 12-module pipeline durably with per-step
 * retry — no longer subject to the post-response CPU budget that drove
 * the original Error 1101 incident.
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

    // Enrichment dispatch runs in the background. On the typical path the
    // entity was already enriched at ingest, so the dispatcher's
    // idempotency pre-check returns alreadyEnriched without creating a
    // Workflow. On backfill, the Workflow handles the full 12-module
    // pipeline durably — no longer subject to the request-path CPU budget.
    const dispatchPromise = dispatchEnrichmentWorkflow(env, {
      entityId,
      orgId: session.orgId,
      mode: 'full',
      triggered_by: 'admin:promote',
    }).catch((err) => {
      console.error('[promote] Background enrichment dispatch failed', { error: err })
    })
    if (locals.cfContext?.waitUntil) {
      locals.cfContext.waitUntil(dispatchPromise)
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
