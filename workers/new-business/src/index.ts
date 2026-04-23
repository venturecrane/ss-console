/**
 * New Business Detection Worker — Pipeline 3
 *
 * Cloudflare Worker cron job that fetches recent commercial permits from
 * Phoenix, Scottsdale, Mesa, and Tempe open data portals, qualifies them
 * with Claude, and writes qualified leads to D1.
 *
 * Schedule: Daily at 7:00 AM MST (14:00 UTC)
 * Trigger: Also via POST /run with Authorization: Bearer <LEAD_INGEST_API_KEY>
 * Flow: City APIs → D1 dedup → Claude qualify → D1 write
 * Cost: Free data sources + Haiku API calls (~$0.001 per permit)
 */

import { ORG_ID } from '../../../src/lib/constants.js'
import { findOrCreateEntity } from '../../../src/lib/db/entities.js'
import { appendContext } from '../../../src/lib/db/context.js'
import { getGeneratorConfig, recordGeneratorRun } from '../../../src/lib/db/generators.js'
import type { NewBusinessConfig } from '../../../src/lib/generators/types.js'
import { enrichEntity } from '../../../src/lib/enrichment/index.js'
import { fetchAllPermits, type SodaCity } from './soda.js'
import { qualifyNewBusiness } from './qualify.js'
import { sendFailureAlert, type RunSummary } from './alert.js'

export interface Env {
  DB: D1Database
  ANTHROPIC_API_KEY: string
  RESEND_API_KEY: string
  LEAD_INGEST_API_KEY: string
  // Optional API keys consumed by the at-ingest enrichment pipeline. When any
  // are missing the corresponding module is skipped — enrichment is
  // best-effort, never a hard dependency.
  GOOGLE_PLACES_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  SERPAPI_API_KEY?: string
  PROXYCURL_API_KEY?: string
}

async function run(env: Env, ctx?: ExecutionContext): Promise<RunSummary> {
  const summary: RunSummary = {
    sources: 5,
    totalPermits: 0,
    newPermits: 0,
    qualified: 0,
    disqualified: 0,
    written: 0,
    errors: 0,
    errorDetails: [],
  }

  const configRow = await getGeneratorConfig(env.DB, ORG_ID, 'new_business')
  if (!configRow.enabled) {
    console.log('new_business: disabled by admin config — skipping run')
    await recordGeneratorRun(env.DB, ORG_ID, 'new_business', {
      signalsCount: 0,
      error: null,
    })
    return summary
  }
  const cfg = configRow.config as NewBusinessConfig
  const enabledCities = cfg.soda_sources.filter((s) => s.enabled).map((s) => s.city as SodaCity)

  const permits = await fetchAllPermits(enabledCities)
  summary.totalPermits = permits.length
  console.log(
    `SODA: ${summary.totalPermits} total permits from ${enabledCities.length} enabled sources`
  )

  for (const permit of permits) {
    try {
      const alreadyProcessed = await env.DB.prepare(
        `SELECT 1 FROM context WHERE org_id = ? AND source = 'new_business' AND source_ref = ?`
      )
        .bind(ORG_ID, permit.permit_number)
        .first()

      if (alreadyProcessed) continue

      summary.newPermits++

      const qualification = await qualifyNewBusiness(permit, env.ANTHROPIC_API_KEY)
      if (!qualification) {
        summary.errors++
        summary.errorDetails.push(`Claude failed for "${permit.business_name}"`)
        continue
      }

      if (qualification.outreach_timing === 'not_recommended') {
        summary.disqualified++
        continue
      }

      summary.qualified++

      // Find or create entity
      const { entity } = await findOrCreateEntity(env.DB, ORG_ID, {
        name: qualification.business_name,
        area: qualification.area,
        source_pipeline: 'new_business',
      })

      // Build context content
      const evidenceSummary = `${qualification.entity_type} — ${qualification.source}. ${qualification.notes}`
      const contentParts: string[] = []
      if (evidenceSummary) contentParts.push(evidenceSummary)
      if (qualification.outreach_angle) {
        contentParts.push(`**Outreach angle:** ${qualification.outreach_angle}`)
      }
      const content = contentParts.join('\n\n') || 'Signal from new_business.'

      // Build metadata
      const dateFound = new Date().toISOString().split('T')[0]
      const metadata: Record<string, unknown> = {
        permit_number: permit.permit_number ?? null,
        permit_type: permit.permit_type ?? null,
        entity_type: qualification.entity_type,
        filing_date: permit.filing_date,
        source: qualification.source,
        vertical_match: qualification.vertical_match,
        size_estimate: qualification.size_estimate,
        outreach_timing: qualification.outreach_timing,
        ...(qualification.outreach_angle ? { outreach_angle: qualification.outreach_angle } : {}),
        date_found: dateFound,
      }

      // Append context
      await appendContext(env.DB, ORG_ID, {
        entity_id: entity.id,
        type: 'signal',
        content,
        source: 'new_business',
        source_ref: permit.permit_number,
        metadata,
      })

      summary.written++

      // At-ingest enrichment (issue #471). Fire-and-forget via waitUntil when
      // we have an ExecutionContext so the 50-state cron loop is not blocked
      // by N Claude round-trips. On the /run fetch path we also detach;
      // enrichment errors are self-contained and should not turn a successful
      // ingest into a failed run. Idempotent: the pipeline no-ops once a
      // prior intelligence_brief exists.
      const enrichPromise = enrichEntity(env, ORG_ID, entity.id, { mode: 'full' }).catch((err) => {
        console.error('[new_business] enrichment failed for', entity.id, err)
      })
      if (ctx) {
        ctx.waitUntil(enrichPromise)
      }
    } catch (err) {
      summary.errors++
      const msg = err instanceof Error ? err.message : String(err)
      summary.errorDetails.push(`Permit "${permit.business_name}": ${msg}`)
    }
  }

  console.log(
    `Run complete: ${summary.newPermits} new, ${summary.qualified} qualified, ` +
      `${summary.disqualified} disqualified, ${summary.written} written, ${summary.errors} errors`
  )

  await recordGeneratorRun(env.DB, ORG_ID, 'new_business', {
    signalsCount: summary.written,
    error: summary.errors > 0 ? summary.errorDetails.slice(0, 3).join(' · ') : null,
  })

  return summary
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const summary = await run(env, ctx)
    if (summary.written === 0 && summary.errors > 0 && env.RESEND_API_KEY) {
      ctx.waitUntil(sendFailureAlert(summary, env.RESEND_API_KEY))
    }
  },

  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const auth = request.headers.get('Authorization')
    if (auth !== `Bearer ${env.LEAD_INGEST_API_KEY}`) {
      return new Response('Unauthorized', { status: 401 })
    }
    const summary = await run(env, ctx)
    return new Response(JSON.stringify(summary, null, 2), {
      headers: { 'Content-Type': 'application/json' },
    })
  },
} satisfies ExportedHandler<Env>
