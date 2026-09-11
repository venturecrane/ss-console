// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../.astro/types.d.ts" />

/** WASM module imports — handled by Cloudflare adapter at build time */
declare module '*.wasm' {
  const module: WebAssembly.Module
  export default module
}

/** Raw-text imports (Vite ?raw) — used to load operator skill bodies as the single source of truth. */
declare module '*.md?raw' {
  const content: string
  export default content
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
    /**
     * R2 bucket holding per-customer Operator config (customer.yaml) + voice
     * vaults. Source of truth for live reconfiguration: the console apply path
     * writes the live config to `vaults/<slug>/customer.yaml` and a byte
     * snapshot to `customers/<slug>/history/<digest>.yaml`; the on-Machine root
     * applier pulls the live key. Same bucket the provisioning scripts use
     * (R2_BUCKET_CONFIG, default `smd-customer-config`). See wrangler.toml.
     */
    CUSTOMER_CONFIG: R2Bucket
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
     * Meta Conversions API access token (ADR 0066 gate 2, #1723), generated
     * under Events Manager > Conversions API settings for the SMD-owned
     * pixel. Secret (wrangler secret via Infisical /ss). Unset = server
     * events fail closed with an honest 'unconfigured' result.
     */
    META_CAPI_ACCESS_TOKEN?: string
    /** Optional Events Manager test_event_code for verifying CAPI delivery. */
    META_CAPI_TEST_EVENT_CODE?: string
    /**
     * Resend webhook signing secret (`whsec_…` from the Resend dashboard
     * webhook detail page). Used to verify Svix-signed webhook deliveries
     * for the outreach attribution path. See
     * src/pages/api/webhooks/resend.ts and issue #587.
     */
    RESEND_WEBHOOK_SECRET?: string
    ANTHROPIC_API_KEY?: string
    /** ElevenLabs API key (voice agent for the assessment funnel). Org key from /vc. */
    ELEVENLABS_API_KEY?: string
    /** ID of the ElevenLabs assessment agent (custom-LLM = our interviewer). */
    ELEVENLABS_ASSESSMENT_AGENT_ID?: string
    /** Optional shared secret the agent's custom-LLM sends as `Authorization: Bearer`. */
    ELEVENLABS_LLM_SECRET?: string
    SIGNWELL_API_KEY?: string
    SIGNWELL_WEBHOOK_SECRET?: string
    STRIPE_API_KEY?: string
    STRIPE_WEBHOOK_SECRET?: string
    // Booking system (Calendly replacement) — added with migration 0011
    /** Google Cloud OAuth 2.0 client ID for Calendar integration. */
    GOOGLE_CLIENT_ID?: string
    /** Google Cloud OAuth 2.0 client secret. */
    GOOGLE_CLIENT_SECRET?: string
    // Note: the Smokeball connector OAuth exchange happens ON the per-customer
    // Machine (ADR 0054), not on this Worker — so no SMOKEBALL_* client creds live
    // here. The Worker is out of the firm-delegated connector credential path.
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
     * Feature flag for the public /patterns aggregate page. Off by default.
     * Set to "1" or "true" in wrangler.toml once the unlock condition
     * documented in src/pages/patterns.astro is met (>=20 real assessments
     * with cross-vertical diversity, per CLAUDE.md no-fabrication rule).
     * Any other value keeps the page returning 404.
     */
    ENABLE_PUBLIC_PATTERNS?: string
    /**
     * Sentry DSN for Workers-side error monitoring. Optional — when unset,
     * the integration is a complete no-op (no SDK init, zero overhead).
     * Provisioned via `wrangler secret put SENTRY_DSN`. See
     * src/lib/observability/sentry.ts.
     */
    SENTRY_DSN?: string
    /**
     * Clerk secret key (sk_test_* for dev, sk_live_* for prod). Used by
     * @clerk/astro middleware to authenticate Clerk sessions on
     * portal.smd.services. Pulled from Infisical at deploy time per the
     * standard wrangler secret bulk pattern documented in CLAUDE.md.
     */
    CLERK_SECRET_KEY?: string
    /**
     * Microsoft Graph OAuth 2.0 client ID and secret. Used by the
     * Operator OAuth callback (issue #879) to exchange authorization
     * codes for tokens during connector consent flows. Issued by an
     * Azure AD app registration whose redirect URI list includes
     * `${ADMIN_BASE_URL}/api/oauth/callback`.
     */
    MICROSOFT_GRAPH_CLIENT_ID?: string
    MICROSOFT_GRAPH_CLIENT_SECRET?: string
    /**
     * HMAC-SHA256 signing key for stateless OAuth state parameters used
     * by /api/oauth/callback (issue #879, Operator connector consent).
     * 32 random bytes, base64-encoded. Generate with
     * `openssl rand -base64 32`. Rotation: bump the secret in Workers
     * env; in-flight states issued under the old key fail validation at
     * the callback and the reviewer simply re-initiates consent. No
     * grace window required because state TTL is 10 minutes. See
     * src/lib/oauth/state.ts.
     */
    OAUTH_STATE_SIGNING_KEY?: string
    /**
     * HMAC signing key for public live-assessment session tokens
     * (`/api/assessment/turn`, ADR 0039 node [1]). Base64-encoded raw bytes;
     * generate with `openssl rand -base64 32`. A dedicated key keeps
     * assessment sessions cryptographically independent of booking links and
     * OAuth state — rotating one must not invalidate the others. The signed
     * `sid` is what makes the per-session turn/cost ceiling unforgeable, which
     * is what defeats IP-rotation budget exhaustion. Must be a Worker secret,
     * never a `[vars]` entry. See src/lib/assessment/session.ts.
     */
    ASSESSMENT_SESSION_SIGNING_KEY?: string
    /**
     * Fly.io API token (SMD-owned, from Infisical) used by the OAuth token
     * relay (`src/lib/oauth/store.ts`) to set a customer app's
     * `GOOGLE_TOKEN_JSON` secret and restart its Machine on connect/re-consent.
     * Must be a Worker secret, never a `[vars]` entry. Scope it to the
     * customer apps it manages. See the OAuth-token-relay ADR.
     */
    FLY_API_TOKEN?: string
    /**
     * TRANSITIONAL shared bearer for the Machine -> control-plane paths
     * (`POST /api/internal/heartbeat`, `/runtime-summary`, `/sentry-probe`).
     * Since migration 0114 each seat authenticates with its own credential
     * (`machine_credentials`, HMAC-SHA256 with a per-row salt, minted by
     * operator/bin/lib/machine_credential.py); this secret is accepted only
     * for a slug that has NO credential row yet, and every such use is
     * logged as `[machine-key] shared-key fallback`. Once every shipped seat
     * has a row, unset this secret on the Worker: that is the retirement of
     * the Wave 1 shared key (ADR 0023 §"Cross-cutting calls" #10).
     */
    MACHINE_HEARTBEAT_KEY?: string
    /**
     * Per-customer secret-relay endpoint base for the write-only static-secret
     * entry path (ADR 0042 / ADR 0036). When set, the client credential-entry
     * endpoint relays a client-entered API key into the customer's per-customer
     * vault (Fly secret + Machine restart) and is enabled; when unset,
     * `isSecretTransportConfigured` returns false and the endpoint returns an
     * honest `not_enabled`. Provisioned at the integration step that wires the
     * relay — see src/lib/operator/credential-secret-transport.ts.
     */
    OPERATOR_SECRET_RELAY_URL?: string
    /**
     * Infisical Universal Auth machine identity for the Hosted Agent BYO-key
     * write transport (ADR 0067). Scoped write-only to the hosted-customers
     * path. When any of the three is unset,
     * `isHostedSecretTransportConfigured` returns false and the portal key
     * endpoint returns an honest `not_enabled` (the key is then collected at
     * the Captain-run go-live step). See
     * src/lib/operator/infisical-secret-transport.ts.
     */
    INFISICAL_UA_CLIENT_ID?: string
    INFISICAL_UA_CLIENT_SECRET?: string
    INFISICAL_PROJECT_ID?: string
    /** Infisical environment slug for the hosted-key writes; defaults to 'prod'. */
    INFISICAL_ENV_SLUG?: string
    /**
     * Host template for the live console→Machine runtime read path (ADR 0043
     * path A). A `{app}` placeholder is substituted with the registry-resolved
     * Fly app (e.g. `https://{app}.fly.dev`); absent, it falls back to
     * `https://<app>.fly.dev`. Enables the read path together with
     * OPERATOR_RUNTIME_READ_SECRET — when either is unset,
     * `isRuntimeReadConfigured` is false and drill-in surfaces render honest
     * empty states. See src/lib/operator/runtime-read-transport.ts.
     */
    OPERATOR_RUNTIME_READ_URL?: string
    /**
     * Master secret for the per-customer runtime read key. The console sends
     * `Bearer HMAC-SHA256(master, customer_slug)`; each Machine holds only its
     * own derived key (set at provision). The master lives ONLY on the console.
     * Required (with OPERATOR_RUNTIME_READ_URL) to enable the read path.
     * See src/lib/operator/runtime-read-transport.ts (ADR 0043 path A).
     */
    OPERATOR_RUNTIME_READ_SECRET?: string
    /**
     * Master secret for the per-customer MCP webhook delivery key. The console
     * derives `HMAC-SHA256(master, customer_slug)` and sends it as a bearer
     * token to the Machine's `/webhooks/mcp` gate; each Machine holds only its
     * own derived key (`WEBHOOK_SECRET_MCP` set at provision). The master lives
     * ONLY on the console. Required (with OPERATOR_RUNTIME_READ_URL) to enable
     * `operator_handoff_task` (Phase 2 MCP connector).
     * See src/lib/operator/mcp/webhook-transport.ts.
     */
    OPERATOR_MCP_WEBHOOK_SECRET?: string
    /**
     * Sentry Internal Integration Client Secret used to verify
     * `Sentry-Hook-Signature` headers on inbound alert-rule webhook
     * deliveries to `/api/webhooks/sentry`. Pulled from the SMD-owned
     * `smd-operator` Sentry project's Internal Integration settings
     * (ADR 0023 Wave 1).
     */
    SENTRY_WEBHOOK_SECRET?: string
    /**
     * Shared bearer token for inbound healthchecks.io webhook deliveries
     * to `/api/webhooks/healthchecks`. Healthchecks.io does NOT sign
     * webhooks, so the integration is configured with an
     * `Authorization: Bearer <secret>` header set in the healthchecks.io
     * UI (ADR 0023 Wave 1).
     */
    HEALTHCHECKS_WEBHOOK_SECRET?: string

    /**
     * Optional bearer secret that unlocks binding-level detail on the public
     * `GET /api/health` endpoint. When unset (the default), the endpoint returns
     * only a bare `{ status }` and the detail path is fail-closed. Set it with
     * `wrangler secret put HEALTH_DETAIL_TOKEN` when an internal monitor needs
     * the binding breakdown. Not required for the liveness probe itself.
     */
    HEALTH_DETAIL_TOKEN?: string
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

interface ImportMetaEnv {
  readonly PUBLIC_GA4_MEASUREMENT_ID?: string
  readonly PUBLIC_GA4_INTERNAL_HOST_PATTERNS?: string
  /**
   * Meta Pixel / dataset id (ADR 0066 gate 2, #1723). Public by nature,
   * shipped in the browser pixel. Unset = pixel + CAPI fail closed (no
   * events, honestly reported). Set in .env.production once the SMD-owned
   * Meta ad account exists.
   */
  readonly PUBLIC_META_PIXEL_ID?: string
  /**
   * Clerk publishable key (pk_test_* for dev, pk_live_* for prod). Required
   * at build time — @clerk/astro inlines it into the client bundle. Pulled
   * from Infisical into .dev.vars locally and into Workers env at deploy.
   */
  readonly PUBLIC_CLERK_PUBLISHABLE_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
