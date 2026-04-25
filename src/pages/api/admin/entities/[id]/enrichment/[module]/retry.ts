import type { APIRoute } from 'astro'
import { runSingleModule } from '../../../../../../../lib/enrichment'
import { isModuleId } from '../../../../../../../lib/enrichment/modules'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/entities/[id]/enrichment/[module]/retry
 *
 * Re-run a single enrichment module against an entity. Records a
 * `enrichment_runs` row with `triggered_by = 'admin:retry:<module>'`.
 * Concurrency-safe: the wrapper's startRun lock prevents racing a
 * cron-triggered run.
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
  const moduleParam = params.module
  if (!entityId || !moduleParam || !isModuleId(moduleParam)) {
    return redirect(`/admin/entities/${entityId ?? ''}?error=invalid_module`, 302)
  }

  try {
    await runSingleModule(env, session.orgId, entityId, moduleParam, {
      triggered_by: `admin:retry:${moduleParam}`,
    })
    return redirect(`/admin/entities/${entityId}?enrichment_retried=${moduleParam}`, 302)
  } catch (err) {
    console.error('[api/admin/entities/enrichment/retry] error', { error: err })
    return redirect(`/admin/entities/${entityId}?error=enrichment_retry_failed`, 302)
  }
}
