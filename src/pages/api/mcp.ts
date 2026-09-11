import type { APIRoute } from 'astro'
import { errorResponse } from '../../lib/api/helpers'

const gone = (): Response =>
  errorResponse(410, 'gone', undefined, { detail: 'use the customer-specific MCP URL' })

export const POST: APIRoute = gone
export const GET: APIRoute = gone
export const OPTIONS: APIRoute = gone
