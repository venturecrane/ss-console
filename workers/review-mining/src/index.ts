/**
 * Review Mining Worker — Pipeline 1
 *
 * Cloudflare Worker cron job that discovers Phoenix-area businesses via
 * Google Places, fetches recent reviews via Outscraper, scores them with
 * Claude for operational pain signals, and writes high-scoring leads to D1.
 *
 * Schedule: Weekly on Monday at 8:00 AM MST (15:00 UTC)
 * Flow: Google Places discovery → Outscraper reviews → D1 dedup → Claude score → filter pain >= 7 → D1 write
 * Threshold: pain_score >= 7 qualifies for the Lead Inbox
 */

import { ORG_ID } from '../../../src/lib/constants.js'
import { computeDedupKey, createLeadSignal } from '../../../src/lib/db/lead-signals.js'
import { discoverBusinesses, fetchReviews, DISCOVERY_QUERIES } from './outscraper.js'
import { scoreReviews } from './qualify.js'
import { sendFailureAlert, type RunSummary } from './alert.js'
import type { DiscoveredBusiness } from './outscraper.js'

const PAIN_THRESHOLD = 7

export interface Env {
  DB: D1Database
  GOOGLE_PLACES_API_KEY: string
  OUTSCRAPER_API_KEY: string
  ANTHROPIC_API_KEY: string
  RESEND_API_KEY: string
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const summary: RunSummary = {
      queries: 0,
      discovered: 0,
      withReviews: 0,
      newBusinesses: 0,
      qualified: 0,
      belowThreshold: 0,
      written: 0,
      errors: 0,
      errorDetails: [],
    }

    // Phase 1: Discover businesses via Google Places
    const allBusinesses: DiscoveredBusiness[] = []
    const seenPlaceIds = new Set<string>()

    for (const query of DISCOVERY_QUERIES) {
      summary.queries++
      try {
        const businesses = await discoverBusinesses(query, env.GOOGLE_PLACES_API_KEY)
        for (const b of businesses) {
          if (!seenPlaceIds.has(b.place_id)) {
            seenPlaceIds.add(b.place_id)
            allBusinesses.push(b)
          }
        }
      } catch (err) {
        summary.errors++
        summary.errorDetails.push(
          `Discovery "${query}": ${err instanceof Error ? err.message : String(err)}`
        )
      }
    }

    summary.discovered = allBusinesses.length
    console.log(`Discovery: ${summary.queries} queries, ${summary.discovered} unique businesses`)

    // Phase 2: Fetch reviews in batches of 10
    const BATCH_SIZE = 10
    const businessesWithReviews = []

    for (let i = 0; i < allBusinesses.length; i += BATCH_SIZE) {
      const batch = allBusinesses.slice(i, i + BATCH_SIZE)
      try {
        const results = await fetchReviews(batch, env.OUTSCRAPER_API_KEY)
        businessesWithReviews.push(...results)
      } catch (err) {
        summary.errors++
        summary.errorDetails.push(
          `Outscraper batch ${i}: ${err instanceof Error ? err.message : String(err)}`
        )
      }
    }

    summary.withReviews = businessesWithReviews.length
    console.log(`Reviews: ${summary.withReviews} businesses with recent reviews`)

    // Phase 3: Score each business
    for (const business of businessesWithReviews) {
      try {
        // Dedup check
        const dedupKey = computeDedupKey(business.name, business.category, business.area)
        const existing = await env.DB.prepare(
          "SELECT 1 FROM lead_signals WHERE org_id = ? AND dedup_key = ? AND source_pipeline = 'review_mining'"
        )
          .bind(ORG_ID, dedupKey)
          .first()

        if (existing) continue

        summary.newBusinesses++

        const scoring = await scoreReviews(business, env.ANTHROPIC_API_KEY)
        if (!scoring) {
          summary.errors++
          summary.errorDetails.push(`Claude failed for "${business.name}"`)
          continue
        }

        if (scoring.pain_score < PAIN_THRESHOLD) {
          summary.belowThreshold++
          continue
        }

        summary.qualified++

        const result = await createLeadSignal(env.DB, ORG_ID, {
          business_name: scoring.business_name,
          phone: null,
          website: null,
          category: business.category,
          area: business.area,
          source_pipeline: 'review_mining',
          pain_score: scoring.pain_score,
          top_problems: scoring.top_problems,
          evidence_summary: scoring.signals.map((s) => `${s.problem_id}: "${s.quote}"`).join(' | '),
          outreach_angle: scoring.outreach_angle,
          source_metadata: {
            place_id: scoring.place_id,
            google_rating: business.rating,
            review_count: business.total_reviews,
            signals_count: scoring.signals.length,
          },
          date_found: new Date().toISOString().split('T')[0],
        })

        if (result.status === 'created') {
          summary.written++
        }
      } catch (err) {
        summary.errors++
        const msg = err instanceof Error ? err.message : String(err)
        summary.errorDetails.push(`Score "${business.name}": ${msg}`)
      }
    }

    console.log(
      `Run complete: ${summary.newBusinesses} new, ${summary.qualified} qualified (pain>=${PAIN_THRESHOLD}), ` +
        `${summary.belowThreshold} below threshold, ${summary.written} written, ${summary.errors} errors`
    )

    if (summary.written === 0 && summary.errors > 0 && env.RESEND_API_KEY) {
      ctx.waitUntil(sendFailureAlert(summary, env.RESEND_API_KEY))
    }
  },
} satisfies ExportedHandler<Env>
