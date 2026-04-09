// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../.astro/types.d.ts" />

/**
 * Cloudflare bindings available via Astro.locals.runtime.env
 *
 * D1            — structured data (clients, quotes, engagements, etc.)
 * R2            — document storage (SOWs, transcripts, handoff docs)
 * SESSIONS KV   — session storage for auth middleware
 * BOOKING_CACHE — rate-limit buckets for /api/booking/reserve (no slot cache in v1)
 *
 * Binding names match wrangler.toml declarations.
 */
type CfEnv = {
  DB: D1Database
  STORAGE: R2Bucket
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
   * `APP_BASE_URL` when unset (the portal is the same Pages
   * deployment served under a subdomain rewrite).
   */
  PORTAL_BASE_URL?: string
  /** Cloudflare Turnstile site key for the /book booking widget. Public. */
  PUBLIC_TURNSTILE_SITE_KEY?: string
  RESEND_API_KEY?: string
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
  /** Cloudflare Turnstile secret key (paired with PUBLIC_TURNSTILE_SITE_KEY). */
  TURNSTILE_SECRET_KEY?: string
}

type Runtime = import('@astrojs/cloudflare').Runtime<CfEnv>

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
  interface Locals extends Runtime {
    /** Populated by auth middleware on /admin/* and /portal/* routes. Null on public routes. */
    session: AuthSession | null
  }
}
