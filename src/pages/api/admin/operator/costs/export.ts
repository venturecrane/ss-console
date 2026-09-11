/**
 * CSV export for the per-customer cost dashboard (#885).
 *
 * `GET /api/admin/operator/costs/export?customer_slug=...&start=YYYY-MM-DD&end=YYYY-MM-DD`
 *
 * Returns the raw `cost_telemetry` rows for one customer in the given
 * date window as a CSV download. Used for billing reconciliation —
 * Captain pulls the file into a spreadsheet to cross-check the ingest
 * against the Anthropic invoice.
 *
 * Admin-only (enforced both by middleware on `/api/admin/*` and by an
 * explicit role check here for defense in depth).
 *
 * Window defaults to the same 30-day window the dashboard uses. `start`
 * and `end` are validated as 'YYYY-MM-DD' strings; bad input returns
 * 400 rather than silently widening the query.
 */

import { errorResponse } from '../../../../../lib/api/helpers'
import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import {
  defaultWindow,
  fetchCustomerCostRows,
  listCostCustomers,
  rowsToCsv,
} from '../../../../../lib/admin/cost-query'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

async function handleGet({ request, locals }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response

  const url = new URL(request.url)
  const customerSlug = url.searchParams.get('customer_slug')
  if (!customerSlug) {
    return errorResponse(400, 'validation_failed', 'customer_slug query param is required.')
  }

  const defaults = defaultWindow()
  const start = url.searchParams.get('start') ?? defaults.start
  const end = url.searchParams.get('end') ?? defaults.end
  if (!DATE_RE.test(start) || !DATE_RE.test(end)) {
    return errorResponse(400, 'validation_failed', 'start and end must be YYYY-MM-DD.')
  }
  if (start >= end) {
    return errorResponse(400, 'validation_failed', 'start must be before end.')
  }

  const customers = await listCostCustomers(env.DB)
  const customer = customers.find((c) => c.customer_slug === customerSlug)
  if (!customer) {
    return errorResponse(404, 'not_found', 'customer not found.')
  }

  // Central cost_telemetry read via the D1 binding (ADR 0062).
  const result = await fetchCustomerCostRows(env.DB, customerSlug, start, end)
  if (result.error) {
    return errorResponse(502, 'upstream_failed', result.error)
  }

  const csv = rowsToCsv(customerSlug, result.rows)
  const filename = `operator-cost-${customerSlug}-${start}-to-${end}.csv`
  return new Response(csv, {
    status: 200,
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': `attachment; filename="${filename}"`,
      'Cache-Control': 'no-store',
    },
  })
}

export const GET: APIRoute = (ctx) => handleGet(ctx)
