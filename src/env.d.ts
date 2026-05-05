// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../.astro/types.d.ts" />

/** WASM module imports — handled by Cloudflare adapter at build time */
declare module '*.wasm' {
  const module: WebAssembly.Module
  export default module
}

/**
 * Service binding shape for the `ss-scan-workflow` Worker. ss-web's
 * /api/scan/verify dispatches the Engine 1 diagnostic pipeline by POSTing
 * to the internal `/dispatch` endpoint on this binding. The target Worker
 * holds the `[[workflows]]` binding for the `ScanDiagnosticWorkflow`
 * class — co-locating the binding with a vanilla (non-Astro) Worker is
 * the durable workaround for #618 (the [[workflows]] binding was
 * unreliable when sharing a bundle with Astro's build pipeline).
 *
 * Service bindings expose a `fetch` member with the same signature as
 * the global fetch — Cloudflare routes the call into the target Worker
 * without ever leaving the data plane.
 */
interface ScanWorkflowServiceBinding {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>
}

/**
 * Service binding shape for the `ss-enrichment-workflow` Worker (#631).
 * ss-web's lead-gen workers and admin endpoints dispatch entity enrichment
 * by POSTing to the internal `/dispatch` endpoint on this binding. The
 * target Worker holds the `[[workflows]]` binding for the
 * `EnrichmentWorkflow` class — co-locating the binding with a vanilla
 * (non-Astro) Worker is the durable workaround for the Astro adapter
 * bundler issue documented in #618.
 */
interface EnrichmentWorkflowServiceBinding {
  fetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response>
}

/**
 * Cloudflare Worker bindings and env vars.
 *
 * Accessed via `import { env } from 'cloudflare:workers'` (adapter v13+).
 * The `Env` interface below augments the one exported by `cloudflare:workers`
 * so callsites get full typing.
 *
 *   D1            — structured data (clients, quotes, engagements, etc.)
 *   R2            — document storage (SOWs, transcripts, handoff docs)
 *   SESSIONS KV   — session storage for auth middleware (custom — separate
 *                   from Astro's built-in session KV, which we don't use)
 *   BOOKING_CACHE — rate-limit buckets for /api/booking/reserve
 *
 * Binding names must match wrangler.toml declarations.
 */
declare namespace Cloudflare {
  interface Env {
    DB: D1Database
    STORAGE: R2Bucket
    /**
     * R2 bucket for consultant portrait photos. Separate from STORAGE because
     * this bucket is intended to be public (objects served directly to the
     * portal via a Cloudflare-managed public URL). See wrangler.toml.
     */
    CONSULTANT_PHOTOS: R2Bucket
    SESSIONS: KVNamespace
    BOOKING_CACHE: KVNamespace
    /**
     * Canonical absolute URL for the marketing/admin app, e.g.
     * `https://smd.services`. Used to build outbound auth, portal,
     * and webhook callback links — never derive from request host.
     * See `src/lib/config/app-url.ts` and GitHub issue #173.
     */
    APP_BASE_URL?: string
    /**
     * Canonical absolute URL for the client portal, e.g.
     * `https://portal.smd.services`. Optional — falls back to
     * `APP_BASE_URL` when unset (the portal is the same Worker
     * deployment served under a subdomain rewrite).
     */
    PORTAL_BASE_URL?: string
    /**
     * Canonical absolute URL for the admin console, e.g.
     * `https://admin.smd.services`. Required for OAuth redirect URIs
     * and outbound admin links. Unlike PORTAL_BASE_URL, this does NOT
     * fall back to APP_BASE_URL — silent fallback would emit the wrong
     * OAuth redirect and cause redirect_uri_mismatch errors.
     */
    ADMIN_BASE_URL?: string
    RESEND_API_KEY?: string
    /**
     * Resend webhook signing secret (`whsec_…` from the Resend dashboard
     * webhook detail page). Used to verify Svix-signed webhook deliveries
     * for the outreach attribution path. See
     * src/pages/api/webhooks/resend.ts and issue #587.
     */
    RESEND_WEBHOOK_SECRET?: string
    ANTHROPIC_API_KEY?: string
    SIGNWELL_API_KEY?: string
    SIGNWELL_WEBHOOK_SECRET?: string
    STRIPE_API_KEY?: string
    STRIPE_WEBHOOK_SECRET?: string
    LEAD_INGEST_API_KEY?: string
    GOOGLE_PLACES_API_KEY?: string
    OUTSCRAPER_API_KEY?: string
    SERPAPI_API_KEY?: string
    PROXYCURL_API_KEY?: string
    // Booking system (Calendly replacement) — added with migration 0011
    /** Google Cloud OAuth 2.0 client ID for Calendar integration. */
    GOOGLE_CLIENT_ID?: string
    /** Google Cloud OAuth 2.0 client secret. */
    GOOGLE_CLIENT_SECRET?: string
    /**
     * 32-byte base64-encoded random key used to AES-GCM encrypt Google
     * refresh tokens at rest in the `integrations` table. Generate with
     * `openssl rand -base64 32`.
     */
    BOOKING_ENCRYPTION_KEY?: string
    /** Static video call URL for booking events (e.g. Zoom personal meeting link). */
    MEETING_URL?: string
    /**
     * Public base URL for the CONSULTANT_PHOTOS bucket, e.g.
     * `https://pub-<id>.r2.dev` (dev-time) or a custom domain like
     * `https://photos.smd.services` in production. When unset, the upload
     * endpoint falls back to streaming via `/api/portal/consultants/photo/[key]`.
     */
    CONSULTANT_PHOTOS_PUBLIC_BASE?: string
    /**
     * Lead-gen worker origins. Used by the admin "Run now" button to
     * invoke each worker's fetch handler on demand (bearer-authed via
     * LEAD_INGEST_API_KEY). Unset in dev — the admin UI degrades to a
     * disabled Run-now button when the URL or key is missing.
     */
    NEW_BUSINESS_WORKER_URL?: string
    JOB_MONITOR_WORKER_URL?: string
    REVIEW_MINING_WORKER_URL?: string
    SOCIAL_LISTENING_WORKER_URL?: string
    /**
     * Feature flag for the public /patterns aggregate page. Off by default.
     * Set to "1" or "true" in wrangler.toml once the unlock condition
     * documented in src/pages/patterns.astro is met (>=20 real assessments
     * with cross-vertical diversity, per CLAUDE.md no-fabrication rule).
     * Any other value keeps the page returning 404.
     */
    ENABLE_PUBLIC_PATTERNS?: string
    /**
     * Outside View Phase 1 PR-B feature flag (ADR 0002).
     *
     * Controls the destination of /scan completion emails. OFF by default
     * at merge so the cutover ships behind a flag and shadow-writes
     * outside_views rows + still sends the legacy diagnostic-report email.
     * Captain inspects 1+ shadow row in admin/SQL, then flips this to "1"
     * or "true" in wrangler.toml or via `wrangler secret put`. Once ON,
     * new scans send a "Your Outside View is ready" magic-link email
     * pointing at portal.smd.services/outside-view; the legacy email path
     * is skipped.
     *
     * Rollback: flip this OFF; legacy email path resumes immediately.
     * Any value other than "1" or "true" is treated as OFF.
     */
    OUTSIDE_VIEW_PORTAL_DELIVERY?: string
    /**
     * Service binding to the `ss-scan-workflow` Worker (#618). Hosts the
     * Engine 1 /scan diagnostic Workflow in its own Worker so the
     * `[[workflows]]` binding registers reliably (it didn't when
     * co-located with the Astro build pipeline — see #618). Dispatched
     * from /api/scan/verify by POSTing to the binding's internal
     * `/dispatch` endpoint with `{ scanRequestId }`. Optional in dev /
     * vitest where the binding doesn't exist; the verify endpoint falls
     * back to inline `ctx.waitUntil` execution in that case.
     */
    SCAN_WORKFLOW_SERVICE?: ScanWorkflowServiceBinding
    /**
     * Service binding to the `ss-enrichment-workflow` Worker (#631). Hosts
     * the EnrichmentWorkflow class for entity enrichment. Dispatched from
     * lead-gen workers and admin endpoints by POSTing to the binding's
     * internal `/dispatch` endpoint with `{ entityId, orgId, mode, triggered_by }`.
     * Optional in dev / vitest where the binding doesn't exist; the
     * dispatcher logs a warning and skips when absent in non-prod, throws
     * in prod (a missing binding in prod is a deploy ordering bug).
     */
    ENRICHMENT_WORKFLOW_SERVICE?: EnrichmentWorkflowServiceBinding
  }
}

/**
 * Session data attached by auth middleware on authenticated routes.
 */
interface AuthSession {
  userId: string
  orgId: string
  role: string
  email: string
  expiresAt: string
}

declare namespace App {
  interface Locals {
    /** Populated by auth middleware on /admin/* and /portal/* routes. Null on public routes. */
    session: AuthSession | null
    /** Cloudflare execution context (waitUntil, passThroughOnException). Provided by adapter v13. */
    cfContext?: ExecutionContext
  }
}
