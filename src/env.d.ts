// eslint-disable-next-line @typescript-eslint/triple-slash-reference
/// <reference path="../.astro/types.d.ts" />

/**
 * Cloudflare bindings available via Astro.locals.runtime.env
 *
 * D1  — structured data (clients, quotes, engagements, etc.)
 * R2  — document storage (SOWs, transcripts, handoff docs)
 * KV  — session storage for auth middleware
 *
 * Binding names match wrangler.toml declarations.
 */
type CfEnv = {
  DB: D1Database
  STORAGE: R2Bucket
  SESSIONS: KVNamespace
  RESEND_API_KEY?: string
  ANTHROPIC_API_KEY?: string
  SIGNWELL_API_KEY?: string
  SIGNWELL_WEBHOOK_SECRET?: string
  STRIPE_API_KEY?: string
  STRIPE_WEBHOOK_SECRET?: string
  LEAD_INGEST_API_KEY?: string
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
