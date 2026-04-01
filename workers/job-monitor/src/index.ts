/**
 * Job Monitor Worker — Pipeline 2
 *
 * Cloudflare Worker cron job that searches for Phoenix-area job postings
 * signaling operational pain, qualifies them with Claude, and writes
 * qualified leads to D1 for triage in the admin Lead Inbox.
 *
 * Schedule: Daily at 6:00 AM MST (13:00 UTC)
 * Flow: SerpAPI → D1 dedup → Claude qualify → D1 write
 */

import { ORG_ID } from '../../../src/lib/constants.js'
import { computeDedupKey, createLeadSignal } from '../../../src/lib/db/lead-signals.js'
import { searchJobs, JOB_QUERIES } from './serpapi.js'
import { qualifyJob, derivePainScore } from './qualify.js'
import { sendFailureAlert, type RunSummary } from './alert.js'
import type { SerpApiJob } from './serpapi.js'

export interface Env {
  DB: D1Database
  SERPAPI_API_KEY: string
  ANTHROPIC_API_KEY: string
  RESEND_API_KEY: string
}

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const summary: RunSummary = {
      queries: 0,
      totalResults: 0,
      newJobs: 0,
      qualified: 0,
      disqualified: 0,
      written: 0,
      errors: 0,
      errorDetails: [],
    }

    // Collect all job results across queries
    const allJobs: Array<{ job: SerpApiJob; query: string }> = []

    for (const query of JOB_QUERIES) {
      summary.queries++
      try {
        const jobs = await searchJobs(query, env.SERPAPI_API_KEY)
        for (const job of jobs) {
          allJobs.push({ job, query })
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        summary.errors++
        summary.errorDetails.push(`Query "${query}": ${msg}`)

        // 401 is fatal — API key issue, abort entire run
        if (msg.includes('401')) {
          console.error(`Fatal: SerpAPI 401 — aborting run`)
          break
        }
      }
    }

    summary.totalResults = allJobs.length
    console.log(`SerpAPI: ${summary.queries} queries, ${summary.totalResults} total results`)

    // Deduplicate by job_id (same job appears across multiple queries)
    const seen = new Set<string>()
    const uniqueJobs: typeof allJobs = []
    for (const entry of allJobs) {
      if (!seen.has(entry.job.job_id)) {
        seen.add(entry.job.job_id)
        uniqueJobs.push(entry)
      }
    }

    // Process each unique job
    for (const { job, query } of uniqueJobs) {
      try {
        // Dedup check against D1
        const dedupKey = computeDedupKey(job.company_name, null, job.location)
        const existing = await env.DB.prepare(
          "SELECT 1 FROM lead_signals WHERE org_id = ? AND dedup_key = ? AND source_pipeline = 'job_monitor'"
        )
          .bind(ORG_ID, dedupKey)
          .first()

        if (existing) continue // Already processed

        summary.newJobs++

        // Qualify with Claude
        const qualification = await qualifyJob(job, env.ANTHROPIC_API_KEY)
        if (!qualification) {
          summary.errors++
          summary.errorDetails.push(`Claude failed for "${job.company_name}" — "${job.title}"`)
          continue // Will retry next run (not marked as seen)
        }

        if (!qualification.qualified) {
          summary.disqualified++
          continue // Disqualified — do not write to D1
        }

        summary.qualified++

        // Write to D1
        const result = await createLeadSignal(env.DB, ORG_ID, {
          business_name: qualification.company,
          category: null,
          area: job.location,
          source_pipeline: 'job_monitor',
          pain_score: derivePainScore(qualification),
          top_problems: qualification.problems_signaled,
          evidence_summary: qualification.evidence,
          outreach_angle: qualification.outreach_angle,
          source_metadata: {
            job_hash: job.job_id,
            job_url: job.apply_options?.[0]?.link ?? null,
            job_title: job.title,
            query_term: query,
            confidence: qualification.confidence,
            company_size_estimate: qualification.company_size_estimate,
          },
          date_found: new Date().toISOString().split('T')[0],
        })

        if (result.status === 'created') {
          summary.written++
        }
      } catch (err) {
        summary.errors++
        const msg = err instanceof Error ? err.message : String(err)
        summary.errorDetails.push(`Job "${job.company_name}": ${msg}`)
      }
    }

    // Log summary
    console.log(
      `Run complete: ${summary.newJobs} new, ${summary.qualified} qualified, ` +
        `${summary.disqualified} disqualified, ${summary.written} written, ${summary.errors} errors`
    )

    // Alert on failure
    if (summary.written === 0 && summary.errors > 0 && env.RESEND_API_KEY) {
      ctx.waitUntil(sendFailureAlert(summary, env.RESEND_API_KEY))
    }
  },
} satisfies ExportedHandler<Env>
