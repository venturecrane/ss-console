import type { APIRoute } from 'astro'
import { validateApiKey } from '../../../lib/auth/api-key'
import { createLeadSignal } from '../../../lib/db/lead-signals'

/**
 * POST /api/ingest/leads
 *
 * Ingest endpoint for Make.com lead generation pipelines.
 * Accepts qualified lead signals and writes them to D1.
 *
 * Auth: Bearer token (LEAD_INGEST_API_KEY), not session cookies.
 * This route is outside /api/admin/*, so middleware passes it through.
 *
 * Dedup: Atomic via UNIQUE(org_id, dedup_key, source_pipeline).
 * Same pipeline + same business = duplicate (200).
 * Different pipeline + same business = cross-match (201, auto-linked if prior signal has client_id).
 */

const ORG_ID = '01JQFK0000SMDSERVICES000'
const MAX_BODY_SIZE = 10 * 1024 // 10KB

const ALLOWED_PIPELINES = ['review_mining', 'job_monitor', 'new_business', 'social_listening']

export const POST: APIRoute = async ({ request, locals }) => {
  // Reject oversized payloads
  const contentLength = request.headers.get('content-length')
  if (contentLength && parseInt(contentLength, 10) > MAX_BODY_SIZE) {
    return jsonResponse(413, { error: 'Payload too large' })
  }

  // Validate API key
  const env = locals.runtime.env
  if (!validateApiKey(request, env.LEAD_INGEST_API_KEY)) {
    return jsonResponse(401, { error: 'Unauthorized' })
  }

  // Parse body
  let body: Record<string, unknown>
  try {
    const text = await request.text()
    if (text.length > MAX_BODY_SIZE) {
      return jsonResponse(413, { error: 'Payload too large' })
    }
    body = JSON.parse(text)
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Validate required fields
  const errors: string[] = []

  const businessName = typeof body.business_name === 'string' ? body.business_name.trim() : ''
  if (!businessName) errors.push('business_name is required')

  const sourcePipeline = typeof body.source_pipeline === 'string' ? body.source_pipeline : ''
  if (!sourcePipeline) {
    errors.push('source_pipeline is required')
  } else if (!ALLOWED_PIPELINES.includes(sourcePipeline)) {
    errors.push(`source_pipeline must be one of: ${ALLOWED_PIPELINES.join(', ')}`)
  }

  const dateFound = typeof body.date_found === 'string' ? body.date_found : ''
  if (!dateFound) errors.push('date_found is required')

  if (errors.length > 0) {
    return jsonResponse(400, { error: 'Validation failed', details: errors })
  }

  // Validate optional fields
  const painScore =
    typeof body.pain_score === 'number' && body.pain_score >= 1 && body.pain_score <= 10
      ? body.pain_score
      : null

  const topProblems = Array.isArray(body.top_problems)
    ? body.top_problems.filter((p): p is string => typeof p === 'string')
    : null

  try {
    const result = await createLeadSignal(env.DB, ORG_ID, {
      business_name: businessName,
      phone: stringOrNull(body.phone),
      website: stringOrNull(body.website),
      category: stringOrNull(body.category),
      area: stringOrNull(body.area),
      source_pipeline: sourcePipeline,
      pain_score: painScore,
      top_problems: topProblems,
      evidence_summary: stringOrNull(body.evidence_summary),
      outreach_angle: stringOrNull(body.outreach_angle),
      source_metadata:
        body.source_metadata && typeof body.source_metadata === 'object'
          ? (body.source_metadata as Record<string, unknown>)
          : null,
      date_found: dateFound,
    })

    if (result.status === 'duplicate') {
      return jsonResponse(200, result)
    }

    return jsonResponse(201, result)
  } catch (err) {
    console.error('[api/ingest/leads] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

function stringOrNull(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}
