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
import { fetchAllPermits } from './soda.js'
import { qualifyNewBusiness } from './qualify.js'
import { sendFailureAlert, type RunSummary } from './alert.js'

export interface Env {
  DB: D1Database
  ANTHROPIC_API_KEY: string
  RESEND_API_KEY: string
  LEAD_INGEST_API_KEY: string
}

async function run(env: Env): Promise<RunSummary> {
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

  const permits = await fetchAllPermits()
  summary.totalPermits = permits.length
  console.log(`SODA: ${summary.totalPermits} total permits from 5 sources`)

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

  return summary
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const summary = await run(env)
    if (summary.written === 0 && summary.errors > 0 && env.RESEND_API_KEY) {
      ctx.waitUntil(sendFailureAlert(summary, env.RESEND_API_KEY))
    }
  },

  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const auth = request.headers.get('Authorization')
    if (auth !== `Bearer ${env.LEAD_INGEST_API_KEY}`) {
      return new Response('Unauthorized', { status: 401 })
    }
    const summary = await run(env)
    return new Response(JSON.stringify(summary, null, 2), {
      headers: { 'Content-Type': 'application/json' },
    })
  },
} satisfies ExportedHandler<Env>
