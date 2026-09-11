import * as Sentry from '@sentry/cloudflare'
import type { APIContext } from 'astro'
import { env } from 'cloudflare:workers'

/**
 * Workers-side Sentry integration. Wraps the Astro middleware request
 * handler with Sentry instrumentation when `SENTRY_DSN` is set; no-ops
 * otherwise.
 *
 * Why middleware-level wrapping (and not `withSentry` on the exported
 * fetch handler): the @astrojs/cloudflare adapter writes the Worker
 * entry at build time — there is no source-controlled handler to wrap.
 * `wrapRequestHandler` is the official @sentry/cloudflare path for
 * exactly this case (it's how the SvelteKit-on-Cloudflare integration
 * initialises Sentry from a per-request hook). See the Sentry SDK
 * README for `@sentry/cloudflare`.
 *
 * No-op behaviour: when `SENTRY_DSN` is unset, `withSentryRequestHandler`
 * returns the bare handler result. The Sentry SDK is imported but never
 * initialised, so no transport opens and no global handlers are wired.
 */

function buildOptions(dsn: string): Sentry.CloudflareOptions {
  return {
    dsn,
    environment: env.APP_BASE_URL?.includes('smd.services') ? 'production' : 'development',
    tracesSampleRate: 0.1,
    sendDefaultPii: false,
  }
}

export async function withSentryRequestHandler(
  context: APIContext,
  handler: () => Promise<Response>
): Promise<Response> {
  const dsn = env.SENTRY_DSN
  if (!dsn) return handler()

  return Sentry.wrapRequestHandler(
    {
      options: buildOptions(dsn),
      request: context.request,
      context: context.locals.cfContext,
    },
    handler
  )
}

/**
 * Report a caught-and-degraded error to Sentry without changing the
 * caller's control flow. Use at catch sites where the request continues
 * (or returns a handled 4xx/5xx) but the failure is operationally
 * significant — otherwise the graceful degrade makes the error invisible
 * to monitoring (2026-07-29 code review, Security §2.8).
 *
 * Only meaningful inside a request wrapped by `withSentryRequestHandler`
 * (the middleware wraps every request). No-ops when SENTRY_DSN is unset,
 * matching the wrapper's contract.
 */
export function captureError(err: unknown, area: string): void {
  if (!env.SENTRY_DSN) return
  Sentry.captureException(err, { tags: { area } })
}

/**
 * Report an operationally significant condition that is not an exception.
 *
 * Some things worth paging on never throw: a seat reporting that it has lost
 * audit rows (#2498) is a successful request carrying bad news. Wrapping it in
 * a synthetic Error to reach `captureError` would put a meaningless stack in
 * the issue and group unrelated conditions by that stack, so it goes through
 * `captureMessage` at warning level instead — grouped by its own text, with the
 * numbers in `extra`.
 *
 * Same contract as `captureError`: no-ops when SENTRY_DSN is unset, and only
 * meaningful inside a request wrapped by `withSentryRequestHandler`.
 * `extra` must never carry secret material or client content.
 */
export function captureWarning(
  message: string,
  area: string,
  extra?: Record<string, unknown>
): void {
  if (!env.SENTRY_DSN) return
  Sentry.captureMessage(message, { level: 'warning', tags: { area }, extra })
}
