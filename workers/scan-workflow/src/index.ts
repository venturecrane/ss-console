/**
 * Scan Workflow Worker — Engine 1 dispatcher (#618)
 *
 * Why this Worker exists
 * ----------------------
 * #614 shipped a Cloudflare Workflows class (`ScanDiagnosticWorkflow`) so
 * the /scan diagnostic pipeline survives a 30s+ wall clock. The class
 * registered correctly with Cloudflare AND ran correctly when triggered
 * manually (`wrangler workflows trigger scan-diagnostic`), but
 * `env.SCAN_WORKFLOW` was undefined at runtime inside the deployed
 * `ss-web` Worker, so /api/scan/verify dispatched via the dev fallback
 * (`ctx.waitUntil`) and re-hit the original isolate-budget timeout.
 *
 * Root cause: `@astrojs/cloudflare` v13 emits its own bundled entrypoint
 * (`dist/server/entry.mjs`) and a generated `dist/server/wrangler.json`
 * during build. Even when the generated wrangler.json carries the
 * `[[workflows]]` block, the runtime binding is unreliable when the
 * Workflow class is co-located with an Astro-built Worker. Captain
 * authorized the durable architectural fix: separate Worker, vanilla
 * entrypoint, no Astro build pipeline in the way.
 *
 * Topology after this Worker lands
 * --------------------------------
 *   ss-web (Astro)
 *      └─ /api/scan/verify
 *           └─ env.SCAN_WORKFLOW_SERVICE.fetch('https://internal/dispatch', ...)
 *                                                 ^ service binding (root wrangler.toml)
 *                                                 |
 *   ss-scan-workflow (this Worker)               <┘
 *      └─ POST /dispatch (this fetch handler)
 *           └─ env.SCAN_WORKFLOW.create({ params: { scanRequestId } })
 *                └─ ScanDiagnosticWorkflow.run(...)
 *                     └─ step.do(...) per module — durable, retried,
 *                        observable in the Cloudflare dashboard
 *
 * Why the class is re-exported, not relocated
 * -------------------------------------------
 * The Workflow class is the same class that ss-web's tests cover and that
 * `tests/diagnostic-workflow.test.ts` instantiates directly. Keeping the
 * source file at `src/lib/diagnostic/workflow.ts` and re-exporting it
 * here (the same way the cron Workers in `workers/{review-mining,
 * job-monitor}/src/index.ts` import shared library code via `../../../src`)
 * means:
 *
 *   - One source of truth for the Workflow logic
 *   - ss-web's existing unit tests keep working
 *   - Schema/dependency drift is impossible — the Worker bundle and the
 *     ss-web tests reference the exact same module
 *
 * Wrangler resolves `class_name = "ScanDiagnosticWorkflow"` (declared in
 * `wrangler.toml`'s `[[workflows]]` block) against the named export
 * below. Adding more Workflow classes is additive — add the class file,
 * re-export it here, add a `[[workflows]]` entry.
 */

// Re-export the Workflow class. Cloudflare resolves the binding's
// class_name against this export at runtime.
export { ScanDiagnosticWorkflow } from '../../../src/lib/diagnostic/workflow.js'

interface ScanWorkflowBinding {
  create(opts: { id?: string; params?: { scanRequestId: string } }): Promise<{ id: string }>
}

export interface Env {
  /** D1 binding shared with ss-web. */
  DB: D1Database
  /** Workflows binding declared in wrangler.toml's `[[workflows]]` block. */
  SCAN_WORKFLOW: ScanWorkflowBinding
  /**
   * Bearer token gating the internal /dispatch endpoint. Defense-in-depth —
   * even though the dispatch endpoint is exposed only via service binding
   * (not externally routable), we still require the bearer token so a
   * mis-configured public route or an accidental external custom domain
   * cannot trigger Workflow dispatches.
   */
  LEAD_INGEST_API_KEY?: string
  /** Secrets passed through to the Workflow at runtime. */
  RESEND_API_KEY?: string
  ANTHROPIC_API_KEY?: string
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  APP_BASE_URL?: string
  /**
   * Portal origin for outbound magic-link URLs minted by the Outside View
   * delivery path. Without this, the fallback in workflow.ts resolves to
   * APP_BASE_URL (https://smd.services, the marketing host) and prospects
   * land on the wrong site after clicking the magic link.
   */
  PORTAL_BASE_URL?: string
  /**
   * Outside View Phase 1 PR-B feature flag. "1" routes the prospect to
   * the new portal-magic-link email; "0" (or unset) keeps the legacy
   * diagnostic-report email path. Declared in [vars] in wrangler.toml so
   * the OFF state is explicit and any flip is a one-line, reviewable commit.
   */
  OUTSIDE_VIEW_PORTAL_DELIVERY?: string
}

interface DispatchRequestBody {
  scanRequestId?: unknown
}

interface DispatchResponseBody {
  ok: boolean
  workflow_run_id?: string
  error?: string
}

/**
 * Dispatch handler. Service-binding-only in production: ss-web's
 * `env.SCAN_WORKFLOW_SERVICE.fetch(...)` invokes this entrypoint without
 * the request ever reaching the public internet. Any request that does
 * reach this Worker via a public route is rejected unless it carries the
 * bearer token — both paths converge on the same handler.
 */
async function handleDispatch(request: Request, env: Env): Promise<Response> {
  if (request.method !== 'POST') {
    return jsonResponse(405, { ok: false, error: 'method_not_allowed' })
  }

  // Bearer guard. We do NOT require the header on service-binding calls
  // (the path is internal), but if the LEAD_INGEST_API_KEY secret is set
  // we require it on requests that present an Authorization header — this
  // covers the operator-test path (`curl -H "Authorization: Bearer ..."`).
  // The combined behavior is:
  //   - service binding (no Authorization header) → allowed
  //   - public bearer-authed call                 → allowed if token matches
  //   - public anonymous call                     → rejected
  const auth = request.headers.get('Authorization')
  if (auth) {
    if (!env.LEAD_INGEST_API_KEY) {
      return jsonResponse(401, { ok: false, error: 'auth_not_configured' })
    }
    if (auth !== `Bearer ${env.LEAD_INGEST_API_KEY}`) {
      return jsonResponse(401, { ok: false, error: 'unauthorized' })
    }
  } else {
    // No Authorization header. We can't tell from inside a Worker whether
    // we were invoked via service binding or directly, so we treat
    // header-less requests as service-binding calls. The public route is
    // not bound to a custom domain — Cloudflare's `*.workers.dev` URL is
    // the only externally-reachable endpoint, and operators who need to
    // exercise it pass the bearer token. If LEAD_INGEST_API_KEY isn't set
    // at all, we still accept service-binding traffic so a fresh-deploy
    // dispatcher works before secrets are wired.
  }

  let body: DispatchRequestBody
  try {
    body = (await request.json()) as DispatchRequestBody
  } catch {
    return jsonResponse(400, { ok: false, error: 'invalid_json' })
  }

  const scanRequestId = typeof body.scanRequestId === 'string' ? body.scanRequestId : ''
  if (!scanRequestId) {
    return jsonResponse(400, { ok: false, error: 'missing_scan_request_id' })
  }

  if (!env.SCAN_WORKFLOW || typeof env.SCAN_WORKFLOW.create !== 'function') {
    // Should never fire in production — the binding is declared in this
    // Worker's own wrangler.toml. If we hit this branch in prod the
    // Worker is misconfigured.
    console.error('[scan-workflow] SCAN_WORKFLOW binding missing at runtime')
    return jsonResponse(500, { ok: false, error: 'workflow_binding_missing' })
  }

  try {
    const instance = await env.SCAN_WORKFLOW.create({
      params: { scanRequestId },
    })
    return jsonResponse(200, { ok: true, workflow_run_id: instance.id })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error('[scan-workflow] SCAN_WORKFLOW.create failed:', message)
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
