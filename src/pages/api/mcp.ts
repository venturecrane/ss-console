/**
 * Retired shared MCP endpoint: every method answers 410 Gone.
 *
 * MCP access is per customer at /api/operator/:customer/mcp (ADR 0057); this
 * path stays so an old connector configuration gets a clear answer naming the
 * replacement instead of a 404.
 */
import type { APIRoute } from 'astro'
import { errorResponse } from '../../lib/api/helpers'

const gone = (): Response =>
  errorResponse(410, 'gone', undefined, { detail: 'use the customer-specific MCP URL' })

export const POST: APIRoute = gone
export const GET: APIRoute = gone
export const OPTIONS: APIRoute = gone
