import type { APIRoute } from 'astro'
import { validateApiKey } from '../../../lib/auth/api-key'
import { findOrCreateEntity } from '../../../lib/db/entities'
import { appendContext, type ContextType } from '../../../lib/db/context'
import { enrichEntity } from '../../../lib/enrichment'
import { ORG_ID } from '../../../lib/constants'
import { env } from 'cloudflare:workers'

/**
 * POST /api/ingest/signals
 *
 * Ingest endpoint for lead generation pipelines.
 * Finds or creates an entity by slug, then appends a context entry.
 *
 * Auth: Bearer token (LEAD_INGEST_API_KEY), not session cookies.
 *
 * Dedup: Entity dedup via UNIQUE(org_id, slug). Same business from
 * multiple pipelines = one entity with multiple signal context entries.
 */
const MAX_BODY_SIZE = 10 * 1024 // 10KB

const ALLOWED_PIPELINES = ['review_mining', 'job_monitor', 'new_business', 'social_listening']

export const POST: APIRoute = async ({ request, locals }) => {
  // Reject oversized payloads
  const contentLength = request.headers.get('content-length')
  if (contentLength && parseInt(contentLength, 10) > MAX_BODY_SIZE) {
    return jsonResponse(413, { error: 'Payload too large' })
  }

  // Validate API key
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

  // Build context content from pipeline payload
  const evidenceSummary = stringOrNull(body.evidence_summary)
  const outreachAngle = stringOrNull(body.outreach_angle)

  const contentParts: string[] = []
  if (evidenceSummary) contentParts.push(evidenceSummary)
  if (outreachAngle) contentParts.push(`**Outreach angle:** ${outreachAngle}`)
  const content = contentParts.join('\n\n') || `Signal from ${sourcePipeline} on ${dateFound}.`

  // Build metadata from pipeline-specific fields
  const painScore =
    typeof body.pain_score === 'number' && body.pain_score >= 1 && body.pain_score <= 10
      ? body.pain_score
      : null

  const topProblems = Array.isArray(body.top_problems)
    ? body.top_problems.filter((p): p is string => typeof p === 'string')
    : null

  const sourceMetadata =
    body.source_metadata && typeof body.source_metadata === 'object'
      ? (body.source_metadata as Record<string, unknown>)
      : {}

  const metadata: Record<string, unknown> = {
    ...sourceMetadata,
    ...(painScore != null && { pain_score: painScore }),
    ...(topProblems && { top_problems: topProblems }),
    ...(outreachAngle && { outreach_angle: outreachAngle }),
    date_found: dateFound,
  }

  try {
    // Find or create entity by normalized slug
    const result = await findOrCreateEntity(env.DB, ORG_ID, {
      name: businessName,
      area: stringOrNull(body.area),
      phone: stringOrNull(body.phone),
      website: stringOrNull(body.website),
      source_pipeline: sourcePipeline,
    })

    // Append signal context entry
    const contextEntry = await appendContext(env.DB, ORG_ID, {
      entity_id: result.entity.id,
      type: 'signal' as ContextType,
      content,
      source: sourcePipeline,
      metadata,
    })

    // At-ingest enrichment for newly-created entities. The lead-gen workers
    // (new_business, review_mining) already trigger enrichment from their own
    // ingest paths, but the generic /api/ingest/signals endpoint is the
    // catch-all for any external signal source — and prior to this it created
    // entities without ever calling enrichEntity, so they were born with no
    // dossier (and no `intelligence_brief` context entry, so the admin
    // Dossier Summary panel never appeared). Detached via locals.cfContext
    // so a 12-module Claude pipeline doesn't block the ingest response.
    if (result.status === 'created') {
      const enrichPromise = enrichEntity(env, ORG_ID, result.entity.id, {
        mode: 'full',
        triggered_by: 'ingest:signals',
      }).catch((err) => {
        console.error('[api/ingest/signals] background enrichment failed', { error: err })
      })
      if (locals.cfContext?.waitUntil) {
        locals.cfContext.waitUntil(enrichPromise)
      }
    }

    return jsonResponse(result.status === 'created' ? 201 : 200, {
      status: result.status === 'created' ? 'created' : 'appended',
      entity_id: result.entity.id,
      context_id: contextEntry.id,
      entity_name: result.entity.name,
      is_new_entity: result.status === 'created',
    })
  } catch (err) {
    console.error('[api/ingest/signals] Error:', err)
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
