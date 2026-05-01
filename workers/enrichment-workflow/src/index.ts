/**
 * Enrichment Workflow Worker — dispatcher (#631)
 *
 * Why this Worker exists
 * ----------------------
 * Production measurement on 2026-04-30 found 86% of created entities had
 * no enrichment activity at all. The legacy `ctx.waitUntil(enrichEntity(...))`
 * orchestration in lead-gen workers and admin endpoints was being killed
 * by the Cloudflare Workers post-response CPU budget — same failure class
 * as `/scan` before #614/#615/#618 moved it to a Workflow.
 *
 * Cloudflare Workflows is the durable-execution primitive for this shape.
 * The class lives in `src/lib/enrichment/workflow.ts` (re-exported below)
 * and gets each module as a discrete `step.do` call.
 *
 * Why a separate Worker (not co-located with ss-web)
 * --------------------------------------------------
 * `@astrojs/cloudflare` v13 emits its own bundled entrypoint and a
 * generated `dist/server/wrangler.json` during build. Even when the
 * generated wrangler.json carries the `[[workflows]]` block, the runtime
 * binding is unreliable when the Workflow class is co-located with an
 * Astro-built Worker — this is the bug class #618 was filed to escape.
 * Captain authorized the durable architectural fix: separate Worker,
 * vanilla entrypoint, no Astro build pipeline in the way. We're paralleling
 * that pattern, not relitigating it.
 *
 * Topology
 * --------
 *   ss-web (Astro) — 7 trigger sites
 *      └─ dispatchEnrichmentWorkflow(env, params)
 *           └─ env.ENRICHMENT_WORKFLOW_SERVICE.fetch('https://internal/dispatch', ...)
 *                                                ^ service binding (root wrangler.toml)
 *                                                |
 *   ss-enrichment-workflow (this Worker)        <┘
 *      └─ POST /dispatch (this fetch handler)
 *           └─ env.ENRICHMENT_WORKFLOW.create({ params })
 *                └─ EnrichmentWorkflow.run(...)
 *                     └─ step.do(...) per module — durable, retried,
 *                        observable in the Cloudflare dashboard
 */

// Re-export the Workflow class. Cloudflare resolves the binding's
// class_name against this export at runtime.
export { EnrichmentWorkflow } from '../../../src/lib/enrichment/workflow.js'

interface EnrichmentWorkflowBinding {
  create(opts: {
    id?: string
    params?: {
      entityId: string
      orgId: string
      mode: 'full' | 'reviews-and-news'
      triggered_by: string
    }
  }): Promise<{ id: string }>
}

export interface Env {
  /** D1 binding shared with ss-web. */
  DB: D1Database
  /** Workflows binding declared in wrangler.toml's `[[workflows]]` block. */
  ENRICHMENT_WORKFLOW: EnrichmentWorkflowBinding
  /**
   * Bearer token gating the internal /dispatch endpoint. Same defense-in-depth
   * pattern as ss-scan-workflow — service-binding traffic doesn't carry an
   * Authorization header so it's allowed unconditionally; public-route
   * traffic must carry the bearer token.
   */
  LEAD_INGEST_API_KEY?: string
  /** Secrets passed through to the Workflow at runtime. */
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  SERPAPI_API_KEY?: string
  PROXYCURL_API_KEY?: string
}

interface DispatchRequestBody {
  entityId?: unknown
  orgId?: unknown
  mode?: unknown
  triggered_by?: unknown
}

interface DispatchResponseBody {
  ok: boolean
  workflow_run_id?: string
  error?: string
}

/**
 * Dispatch handler. Service-binding-only in production: ss-web's
 * `env.ENRICHMENT_WORKFLOW_SERVICE.fetch(...)` invokes this entrypoint
 * without the request ever reaching the public internet. Public requests
 * (workers.dev URL) must carry the bearer token.
 */
async function handleDispatch(request: Request, env: Env): Promise<Response> {
  if (request.method !== 'POST') {
    return jsonResponse(405, { ok: false, error: 'method_not_allowed' })
  }

  // Bearer guard. Header-less requests are treated as service-binding
  // traffic and allowed; header-bearing requests must match the secret.
  const auth = request.headers.get('Authorization')
  if (auth) {
    if (!env.LEAD_INGEST_API_KEY) {
      return jsonResponse(401, { ok: false, error: 'auth_not_configured' })
    }
    if (auth !== `Bearer ${env.LEAD_INGEST_API_KEY}`) {
      return jsonResponse(401, { ok: false, error: 'unauthorized' })
    }
  }

  let body: DispatchRequestBody
  try {
    body = (await request.json()) as DispatchRequestBody
  } catch {
    return jsonResponse(400, { ok: false, error: 'invalid_json' })
  }

  const entityId = typeof body.entityId === 'string' ? body.entityId : ''
  const orgId = typeof body.orgId === 'string' ? body.orgId : ''
  const mode = body.mode === 'reviews-and-news' ? 'reviews-and-news' : 'full'
  const triggered_by = typeof body.triggered_by === 'string' ? body.triggered_by : 'unknown'

  if (!entityId) {
    return jsonResponse(400, { ok: false, error: 'missing_entity_id' })
  }
  if (!orgId) {
    return jsonResponse(400, { ok: false, error: 'missing_org_id' })
  }

  if (!env.ENRICHMENT_WORKFLOW || typeof env.ENRICHMENT_WORKFLOW.create !== 'function') {
    // Should never fire in production — the binding is declared in this
    // Worker's own wrangler.toml. If we hit this branch in prod the
    // Worker is misconfigured.
    console.error('[enrichment-workflow] ENRICHMENT_WORKFLOW binding missing at runtime')
    return jsonResponse(500, { ok: false, error: 'workflow_binding_missing' })
  }

  try {
    const instance = await env.ENRICHMENT_WORKFLOW.create({
      params: { entityId, orgId, mode, triggered_by },
    })
    return jsonResponse(200, { ok: true, workflow_run_id: instance.id })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error('[enrichment-workflow] ENRICHMENT_WORKFLOW.create failed:', message)
    return jsonResponse(500, { ok: false, error: `dispatch_failed: ${message}` })
  }
}

/**
 * Lightweight health endpoint. Lets `wrangler tail` confirm the Worker is
 * up without dispatching a Workflow. Always 200, no auth needed.
 */
function handleHealth(): Response {
  return jsonResponse(200, { ok: true })
}

function jsonResponse(status: number, body: DispatchResponseBody | { ok: boolean }): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export default {
  async fetch(request: Request, env: Env, _ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url)
    if (url.pathname === '/dispatch') {
      return handleDispatch(request, env)
    }
    if (url.pathname === '/health' || url.pathname === '/') {
      return handleHealth()
    }
    return jsonResponse(404, { ok: false, error: 'not_found' })
  },
} satisfies ExportedHandler<Env>
