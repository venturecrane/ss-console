import type { APIRoute } from 'astro'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * Retired scalar trust-ceiling endpoint.
 *
 * The flag-day entitlement model authors persona exposure and skill initiation.
 * This legacy endpoint no longer records changes because doing so would create
 * audit rows the runtime cannot enforce.
 */
export const POST: APIRoute = () =>
  errorResponse(410, 'gone', 'The scalar trust ceiling is retired.')
