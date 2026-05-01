/**
 * Dispatch helper for the EnrichmentWorkflow (#631).
 *
 * Replaces the legacy `ctx.waitUntil(enrichEntity(...))` pattern at all
 * 7 trigger sites. Performs an idempotency pre-check at the dispatch
 * site (saves a Workflow round-trip when the entity is already enriched)
 * and routes through the `ENRICHMENT_WORKFLOW_SERVICE` service binding.
 *
 * No inline-orchestrator fallback. The legacy enrichEntity orchestrator
 * was deleted because its `ctx.waitUntil` invocation shape was being
 * killed by the Cloudflare Workers post-response CPU budget — replicating
 * it as a fallback would re-introduce the bug we're escaping. Behavior
 * when the binding is absent:
 *
 *   - Dev / vitest (binding undefined) — log a warning, return
 *     `{ workflowRunId: null, alreadyEnriched: false, dispatched: false }`.
 *     Tests mock this module directly; they never hit this path.
 *   - Production (binding undefined) — throw. A misconfigured deploy
 *     should fail loudly so operators see it, rather than silently
 *     re-creating the under-production bug class.
 */

import { listContext } from '../db/context'
import type { EnrichMode } from './index'

interface EnrichmentWorkflowServiceBinding {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>
}

interface DispatchEnv {
  DB: D1Database
  ENRICHMENT_WORKFLOW_SERVICE?: EnrichmentWorkflowServiceBinding
}

interface DispatchSuccessResponse {
  ok: boolean
  workflow_run_id?: string
  error?: string
}

export interface EnrichmentWorkflowDispatchParams {
  entityId: string
  orgId: string
  mode: EnrichMode
  triggered_by: string
}

export interface EnrichmentWorkflowDispatchResult {
  workflowRunId: string | null
  alreadyEnriched: boolean
  /** True if a Workflow instance was actually created. False on idempotent
   *  short-circuit and on dev-binding-absent log paths. */
  dispatched: boolean
}

/**
 * Dispatch an EnrichmentWorkflow run for a single entity.
 *
 * Idempotency pre-check: when `mode === 'full'`, we check
 * `enrichment_runs` for a successful `intelligence_brief` row. If one
 * exists, we return immediately with `alreadyEnriched: true` — no
 * Workflow is created, no API budget is spent.
 *
 * The Workflow itself ALSO performs this check in its `init` step (so
 * direct `wrangler workflows trigger` calls also short-circuit), but
 * the dispatch-site check saves a Workflow round-trip on the hot path.
 */
export async function dispatchEnrichmentWorkflow(
  env: DispatchEnv,
  params: EnrichmentWorkflowDispatchParams
): Promise<EnrichmentWorkflowDispatchResult> {
  // Idempotency pre-check (full mode only).
  if (params.mode === 'full') {
    try {
      const existing = await listContext(env.DB, params.entityId, { type: 'enrichment' })
      const hasBrief = existing.some((e) => e.source === 'intelligence_brief')
      if (hasBrief) {
        return { workflowRunId: null, alreadyEnriched: true, dispatched: false }
      }
    } catch (err) {
      // listContext failed — log and proceed. Failing-closed (refusing
      // to dispatch) would replicate the under-production bug; better
      // to dispatch and let the Workflow's own init step re-check.
      console.error('[enrichment-dispatch] idempotency pre-check failed:', err)
    }
  }

  // Service-binding dispatch path.
  if (
    env.ENRICHMENT_WORKFLOW_SERVICE &&
    typeof env.ENRICHMENT_WORKFLOW_SERVICE.fetch === 'function'
  ) {
    const dispatchRes = await env.ENRICHMENT_WORKFLOW_SERVICE.fetch('https://internal/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    })

    if (!dispatchRes.ok) {
      const text = await dispatchRes.text().catch(() => '')
      throw new Error(`[enrichment-dispatch] returned ${dispatchRes.status}: ${text.slice(0, 200)}`)
    }

    const body = (await dispatchRes.json()) as DispatchSuccessResponse
    if (!body.ok || !body.workflow_run_id) {
      throw new Error(
        `[enrichment-dispatch] payload missing workflow_run_id: ${body.error ?? 'no_error'}`
      )
    }

    return {
      workflowRunId: body.workflow_run_id,
      alreadyEnriched: false,
      dispatched: true,
    }
  }

  // Binding absent. Production should never hit this — fail loudly.
  // Detection: the Cloudflare Workers runtime exposes
  // `globalThis.navigator?.userAgent === 'Cloudflare-Workers'` in prod;
  // we use a presence check on env vars instead since that's already
  // available without extra imports.
  const isProd = isProductionEnvironment()
  if (isProd) {
    throw new Error(
      '[enrichment-dispatch] ENRICHMENT_WORKFLOW_SERVICE binding undefined in production — deploy ordering issue'
    )
  }

  console.warn(
    '[enrichment-dispatch] ENRICHMENT_WORKFLOW_SERVICE binding undefined — skipping dispatch (dev/test)'
  )
  return { workflowRunId: null, alreadyEnriched: false, dispatched: false }
}

/**
 * Best-effort detection of the production runtime. Cloudflare Workers
 * sets `navigator.userAgent === 'Cloudflare-Workers'` at runtime — we use
 * that as the signal. In Node (vitest) `navigator` is undefined; in dev
 * (`wrangler dev`) the userAgent is also `Cloudflare-Workers`, but the
 * binding is present anyway, so we never reach this branch in dev.
 *
 * Conservative on the side of "don't crash dev": if we can't tell, we
 * treat it as non-prod and log a warning.
 */
function isProductionEnvironment(): boolean {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const ua = (globalThis as any).navigator?.userAgent
  return typeof ua === 'string' && ua === 'Cloudflare-Workers'
}
