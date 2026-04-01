/**
 * New Business Detection Worker — Pipeline 3
 *
 * Cloudflare Worker cron job that fetches recent commercial permits from
 * Phoenix, Scottsdale, and Chandler open data portals, qualifies them
 * with Claude, and writes qualified leads to D1.
 *
 * Schedule: Daily at 7:00 AM MST (14:00 UTC)
 * Flow: SODA APIs → D1 dedup → Claude qualify → D1 write
 * Cost: Free data sources + Haiku API calls (~$0.001 per permit)
 */

import { ORG_ID } from '../../../src/lib/constants.js'
import { computeDedupKey, createLeadSignal } from '../../../src/lib/db/lead-signals.js'
import { fetchAllPermits } from './soda.js'
import { qualifyNewBusiness } from './qualify.js'
import { sendFailureAlert, type RunSummary } from './alert.js'

export interface Env {
  DB: D1Database
  ANTHROPIC_API_KEY: string
  RESEND_API_KEY: string
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const summary: RunSummary = {
      sources: 3,
      totalPermits: 0,
      newPermits: 0,
      qualified: 0,
      disqualified: 0,
      written: 0,
      errors: 0,
      errorDetails: [],
    }

    // Fetch permits from all city SODA APIs
    const permits = await fetchAllPermits()
    summary.totalPermits = permits.length
    console.log(`SODA: ${summary.totalPermits} total permits from 3 sources`)

    // Process each permit
    for (const permit of permits) {
      try {
        // Dedup check against D1
        const dedupKey = computeDedupKey(permit.business_name, null, permit.address)
        const existing = await env.DB.prepare(
          "SELECT 1 FROM lead_signals WHERE org_id = ? AND dedup_key = ? AND source_pipeline = 'new_business'"
        )
          .bind(ORG_ID, dedupKey)
          .first()

        if (existing) continue

        summary.newPermits++

        // Qualify with Claude (Haiku for cost efficiency — classification task)
        const qualification = await qualifyNewBusiness(permit, env.ANTHROPIC_API_KEY)
        if (!qualification) {
          summary.errors++
          summary.errorDetails.push(`Claude failed for "${permit.business_name}"`)
          continue
        }

        // Filter: only write if outreach is recommended
        if (qualification.outreach_timing === 'not_recommended') {
          summary.disqualified++
          continue
        }

        summary.qualified++

        // Write to D1
        const result = await createLeadSignal(env.DB, ORG_ID, {
          business_name: qualification.business_name,
          category: null,
          area: qualification.area,
          source_pipeline: 'new_business',
          pain_score: null, // New businesses don't have operational pain signals
          top_problems: null,
          evidence_summary: `${qualification.entity_type} — ${qualification.source}. ${qualification.notes}`,
          outreach_angle: qualification.outreach_angle,
          source_metadata: {
            permit_number: permit.permit_number ?? null,
            permit_type: permit.permit_type ?? null,
            entity_type: qualification.entity_type,
            filing_date: permit.filing_date,
            source: qualification.source,
            vertical_match: qualification.vertical_match,
            size_estimate: qualification.size_estimate,
            outreach_timing: qualification.outreach_timing,
          },
          date_found: new Date().toISOString().split('T')[0],
        })

        if (result.status === 'created') {
          summary.written++
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

    if (summary.written === 0 && summary.errors > 0 && env.RESEND_API_KEY) {
      ctx.waitUntil(sendFailureAlert(summary, env.RESEND_API_KEY))
    }
  },
} satisfies ExportedHandler<Env>
