/**
 * The API error vocabulary: one machine code per failure class, with the prose
 * a person can be shown beside it.
 *
 * Before 2026-09-11 the `error` key under `src/pages/api` carried two
 * vocabularies at once: 36 snake_case codes and 33 human sentences, including
 * `'Invalid JSON.'` beside `'Invalid JSON'` and three phrasings of rate
 * limiting (code review 2026-09-10, Architecture 5). A caller could not tell a
 * parseable code from a sentence meant for a person. The shape is now fixed:
 *
 *   { error: <ApiErrorCode>, message: <prose>, ...extra }
 *
 * `error` is always a code from `API_ERROR_CATALOG`; `message` is always
 * present (the site's own wording, else the catalog default). Browser callers
 * read `message` for display (`src/scripts/book.ts`, the intake and contact
 * pages, the admin detail clients); machines read `error`.
 *
 * Enforced by `tests/api-error-vocabulary.test.ts` (every literal passed as a
 * code is in the catalog; no route builds an `{ error }` body by hand) and by
 * the `no-restricted-syntax` guard in eslint.config.js.
 */

export const API_ERROR_CATALOG = {
  // Request shape
  invalid_json: 'Invalid JSON.',
  validation_failed: 'Validation failed.',
  invalid_body: 'The request body is not valid.',
  invalid_email: 'Invalid email address.',
  invalid_action: 'Unknown action.',
  invalid_action_class: 'Unknown action class.',
  invalid_level: 'Unknown level.',
  invalid_profile: 'Unknown profile.',
  invalid_stage: 'Unknown stage.',
  invalid_subject: 'Unknown subject.',
  missing_entity_id: 'entity_id is required.',
  missing_heartbeat_ts: 'heartbeat_ts is required.',
  missing_pushed_at: 'pushed_at is required.',
  missing_signature: 'A signature is required.',
  missing_tenant_tag: 'A tenant tag is required.',
  missing_tenant_or_status: 'Tenant and status are required.',
  missing_token: 'A token is required.',
  // Identity and access
  unauthorized: 'Unauthorized.',
  forbidden: 'Forbidden.',
  cross_site_request: 'Cross-site requests are refused.',
  unknown_tenant: 'Unknown tenant.',
  session_invalid: 'Your session is no longer valid. Please restart.',
  session_limit_reached: 'This session has reached its length limit. Please restart to continue.',
  // Verification
  invalid_signature: 'Invalid signature.',
  invalid_timestamp: 'Invalid timestamp.',
  stale: 'Stale request.',
  expired: 'This link has expired.',
  gone: 'This endpoint has been retired.',
  // State
  not_found: 'Not found.',
  entity_not_found: 'Entity not found.',
  already_cancelled: 'Already cancelled.',
  cancelled: 'This booking has been cancelled.',
  no_active_subscription: 'No active subscription.',
  slot_taken: 'That time was just taken. Please choose another.',
  slot_unavailable: 'That time is no longer available.',
  stage_transition_failed: 'The stage could not be changed.',
  signing_failed: 'The document could not be sent for signature.',
  calendar_sync_failed: 'The calendar could not be updated.',
  calendar_unavailable: 'The calendar is temporarily unavailable.',
  // Limits and availability
  rate_limited: 'Too many requests. Please try again later.',
  unavailable: 'Temporarily unavailable. Please try again.',
  upstream_failed: 'An upstream service failed.',
  server_misconfigured: 'Server misconfigured.',
  internal_error: 'Internal server error.',
} as const

export type ApiErrorCode = keyof typeof API_ERROR_CATALOG

/** The wire shape every error response carries. */
export interface ApiErrorBody {
  error: ApiErrorCode
  message: string
  [extra: string]: unknown
}

export function apiErrorBody(
  code: ApiErrorCode,
  message?: string,
  extra?: Record<string, unknown>
): ApiErrorBody {
  return { error: code, message: message ?? API_ERROR_CATALOG[code], ...extra }
}
