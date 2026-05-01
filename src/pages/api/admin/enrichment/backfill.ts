import type { APIRoute } from 'astro'
import { dispatchEnrichmentWorkflow } from '../../../../lib/enrichment/dispatch'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/enrichment/backfill (#631)
 *
 * One-time (and re-runnable) operation to dispatch enrichment Workflows
 * for entities that lack a successful `intelligence_brief` row. The
 * 2026-04-30 measurement found 86% of created entities had no enrichment
 * activity — `ctx.waitUntil(enrichEntity(...))` was being killed by the
 * post-response CPU budget. The Workflow refactor fixes the forward path;
 * this endpoint backfills the entities already stuck.
 *
 * Bounded operation:
 *   - `limit` defaults to 50, max 500
 *   - Dispatch is throttled at ~5/second so the consumer Worker isn't
 *     overwhelmed
 *   - Operator clicks repeatedly to drain the backlog
 *
 * `dry_run: true` returns the total unenriched-entity count plus an
 * estimated cost (based on $0.18/dossier from the cost model documented
 * in the conversation that produced this PR) so the operator sees the
 * full population before committing real spend.
 *
 * Body shape:
 *   { limit?: number; dry_run?: boolean }
 *
 * Response shape:
 *   {
 *     enqueued: number;          // count actually dispatched
 *     total_remaining: number;   // unenriched entities still in DB
 *     dry_run: boolean;
 *     estimated_cost_usd?: number;  // dry_run only
 *   }
 */

const DEFAULT_LIMIT = 50
const MAX_LIMIT = 500
const THROTTLE_MS = 200 // ~5 dispatches/second
const PER_DOSSIER_COST_USD = 0.18

interface BackfillBody {
  limit?: unknown
  dry_run?: unknown
}

interface BackfillResponse {
  enqueued: number
  total_remaining: number
  dry_run: boolean
  estimated_cost_usd?: number
  errors?: string[]
}

export const POST: APIRoute = async ({ request, locals }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return jsonResponse(401, { error: 'Unauthorized' })
  }

  let body: BackfillBody = {}
  try {
    body = (await request.json()) as BackfillBody
  } catch {
    // Empty body is fine — defaults apply.
    body = {}
  }

  const dryRun = body.dry_run === true
  const limitInput = typeof body.limit === 'number' ? body.limit : DEFAULT_LIMIT
  const limit = Math.min(Math.max(1, Math.floor(limitInput)), MAX_LIMIT)

  // Total unenriched count — needed for both dry_run estimate AND for
  // total_remaining in the live response. Single COUNT query.
  const countRow = (await env.DB.prepare(
    `SELECT COUNT(*) AS n
       FROM entities
      WHERE id NOT IN (
        SELECT DISTINCT entity_id FROM enrichment_runs
         WHERE module = 'intelligence_brief' AND status = 'succeeded'
      )`
  ).first()) as { n: number } | null
  const totalUnenriched = countRow?.n ?? 0

  if (dryRun) {
    const response: BackfillResponse = {
      enqueued: 0,
      total_remaining: totalUnenriched,
      dry_run: true,
      estimated_cost_usd: Number((totalUnenriched * PER_DOSSIER_COST_USD).toFixed(2)),
    }
    return jsonResponse(200, response)
  }

  // Live path — fetch the bounded slice and dispatch. We dispatch in
  // sequence with a small throttle so the consumer Worker isn't drowned;
  // backfill is a maintenance operation and operators won't notice the
  // few seconds of latency on the response.
  const slice = (await env.DB.prepare(
    `SELECT id, org_id
       FROM entities
      WHERE id NOT IN (
        SELECT DISTINCT entity_id FROM enrichment_runs
         WHERE module = 'intelligence_brief' AND status = 'succeeded'
      )
      LIMIT ?`
  )
    .bind(limit)
    .all()) as { results?: Array<{ id: string; org_id: string }> }

  const rows = slice.results ?? []
  const errors: string[] = []
  let enqueued = 0

  for (let i = 0; i < rows.length; i++) {
    const row = rows[i]
    try {
      const result = await dispatchEnrichmentWorkflow(env, {
        entityId: row.id,
        orgId: row.org_id,
        mode: 'full',
        triggered_by: 'admin:backfill',
      })
      if (result.dispatched) {
        enqueued++
      } else if (result.alreadyEnriched) {
        // Race: another caller enriched between our COUNT and our SELECT.
        // Don't count it as enqueued.
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      errors.push(`${row.id}: ${msg}`)
    }
    // Throttle between dispatches.
    if (i < rows.length - 1) {
      await new Promise((resolve) => setTimeout(resolve, THROTTLE_MS))
    }
  }

  const response: BackfillResponse = {
    enqueued,
    total_remaining: Math.max(0, totalUnenriched - enqueued),
    dry_run: false,
  }
  if (errors.length > 0) {
    response.errors = errors
  }
  return jsonResponse(200, response)
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
