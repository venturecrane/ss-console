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
}

type Runtime = import('@astrojs/cloudflare').Runtime<CfEnv>

declare namespace App {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface Locals extends Runtime {}
}
