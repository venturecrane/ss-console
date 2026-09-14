/**
 * The two ways an API route answers a 5xx, and both reach Sentry before the
 * caller sees the status (code review 2026-09-10, Code Quality 4: 174
 * catch-bearing files, 5 capturing; a 500 was a Worker log line nobody pages
 * on). `tests/api-error-vocabulary.test.ts` refuses a route that hands a 5xx
 * to `errorResponse` directly.
 *
 * Kept apart from ./helpers on purpose: this module imports the Sentry seam,
 * which imports `cloudflare:workers`, and ./helpers is also loaded by plain
 * Node scripts (scripts/validate-customer-yaml.ts) that cannot resolve that
 * scheme.
 */

import { errorResponse } from './helpers'
import type { ApiErrorCode } from './errors'
import { captureError, captureWarning } from '../observability/sentry'

export interface FailureOptions {
  /** 500 for our own failure, 502 for an upstream that answered badly, 503 for a dependency that is not there right now. */
  status?: 500 | 502 | 503
  code?: ApiErrorCode
  /** The site's own wording for the person reading it; the catalog default otherwise. */
  message?: string
  extra?: Record<string, unknown>
}

/**
 * A caught failure (an exception, or a vendor result carrying an error). The
 * value reaches Sentry tagged with the route's area, and the Worker log, then
 * the caller gets 500 internal_error unless the site says otherwise.
 */
export function failedResponse(err: unknown, area: string, opts: FailureOptions = {}): Response {
  console.error(`[${area}]`, err)
  captureError(err, area)
  return errorResponse(opts.status ?? 500, opts.code ?? 'internal_error', opts.message, opts.extra)
}

/**
 * A missing secret or binding. Nothing threw, but a route that cannot do its
 * job because deployment configuration is absent is a page-worthy condition,
 * not a debug line, so it goes to Sentry as a warning grouped by the missing
 * name. Defaults to 500 server_misconfigured; a public route that would
 * rather tell its caller "temporarily unavailable" passes its own status,
 * code and wording.
 */
export function misconfiguredResponse(
  area: string,
  missing: string,
  opts: FailureOptions = {}
): Response {
  console.error(`[${area}] ${missing} not configured`)
  captureWarning(`${missing} not configured`, area)
  return errorResponse(
    opts.status ?? 500,
    opts.code ?? 'server_misconfigured',
    opts.message,
    opts.extra
  )
}
