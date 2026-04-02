/**
 * Review Mining Worker — Pipeline 1
 *
 * Cloudflare Worker cron job that discovers Phoenix-area businesses via
 * Google Places, fetches recent reviews via Outscraper, scores them with
 * Claude for operational pain signals, and writes high-scoring leads to D1.
 *
 * Schedule: Weekly on Monday at 8:00 AM MST (15:00 UTC)
 * Trigger: Also via POST /run with Authorization: Bearer <LEAD_INGEST_API_KEY>
 * Flow: Google Places discovery → Outscraper reviews → D1 dedup → Claude score → filter pain >= 7 → D1 write
 * Threshold: pain_score >= 7 qualifies for the Lead Inbox
 */

import { ORG_ID } from '../../../src/lib/constants.js'
import { findOrCreateEntity } from '../../../src/lib/db/entities.js'
import { appendContext } from '../../../src/lib/db/context.js'
import { computeSlug } from '../../../src/lib/entities/slug.js'
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
  LEAD_INGEST_API_KEY: string
}

async function run(env: Env): Promise<RunSummary> {
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

  // Phase 2: Fetch reviews (one Outscraper call per business)
  // Cap at 50 businesses per run to manage Outscraper costs (~$1/run at $2/1000 reviews)
  const MAX_REVIEW_CHECKS = 50
  const BATCH_SIZE = 10
  const businessesToCheck = allBusinesses.slice(0, MAX_REVIEW_CHECKS)
  const businessesWithReviews = []

  console.log(
    `Checking reviews for ${businessesToCheck.length} of ${allBusinesses.length} businesses`
  )

  for (let i = 0; i < businessesToCheck.length; i += BATCH_SIZE) {
    const batch = businessesToCheck.slice(i, i + BATCH_SIZE)
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
      const slug = computeSlug(business.name, business.area)
      const existing = await env.DB.prepare('SELECT 1 FROM entities WHERE org_id = ? AND slug = ?')
        .bind(ORG_ID, slug)
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

      // Find or create entity
      const { entity } = await findOrCreateEntity(env.DB, ORG_ID, {
        name: scoring.business_name,
        area: business.area,
        phone: business.phone,
        website: business.website,
        source_pipeline: 'review_mining',
      })

      // Build context content
      const evidenceSummary = scoring.signals
        .map((s) => `${s.problem_id}: "${s.quote}"`)
        .join(' | ')
      const contentParts: string[] = []
      if (evidenceSummary) contentParts.push(evidenceSummary)
      if (scoring.outreach_angle) {
        contentParts.push(`**Outreach angle:** ${scoring.outreach_angle}`)
      }
      const content = contentParts.join('\n\n') || 'Signal from review_mining.'

      // Build metadata
      const dateFound = new Date().toISOString().split('T')[0]
      const metadata: Record<string, unknown> = {
        place_id: scoring.place_id,
        google_rating: business.rating,
        review_count: business.total_reviews,
        signals_count: scoring.signals.length,
        ...(scoring.pain_score != null ? { pain_score: scoring.pain_score } : {}),
        ...(scoring.top_problems ? { top_problems: scoring.top_problems } : {}),
        ...(scoring.outreach_angle ? { outreach_angle: scoring.outreach_angle } : {}),
        date_found: dateFound,
      }

      // Append context
      await appendContext(env.DB, ORG_ID, {
        entity_id: entity.id,
        type: 'signal',
        content,
        source: 'review_mining',
        metadata,
      })

      summary.written++
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
