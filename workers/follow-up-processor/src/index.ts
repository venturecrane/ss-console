/**
 * Follow-Up Processor Worker
 *
 * Unified cron-driven Worker for all lifecycle background automation.
 * Runs hourly. Each handler is called sequentially and errors are isolated
 * so one failing handler doesn't block the rest.
 *
 * Schedule: Hourly at :00 UTC
 * Handlers:
 *   - booking-enrichment: enrich booking entities that skipped inline enrichment
 *   - booking-cleanup: prune expired holds and OAuth states
 *   - health-check: write heartbeat row for deploy monitoring
 *
 * Future handlers (separate issues):
 *   - Scheduled follow-up sending
 *   - Invoice overdue escalation
 *   - Re-engagement surfacing
 *   - Safety net auto-completion
 */

import { runBookingEnrichment } from './handlers/booking-enrichment.js'
import { runBookingCleanup } from './handlers/booking-cleanup.js'
import { runHealthCheck } from './handlers/health-check.js'

export interface Env {
  DB: D1Database
  GOOGLE_PLACES_API_KEY?: string
  ANTHROPIC_API_KEY?: string
  OUTSCRAPER_API_KEY?: string
  SERPAPI_API_KEY?: string
}

interface HandlerResult {
  name: string
  ok: boolean
  durationMs: number
  detail?: string
  error?: string
}

async function runHandler(name: string, fn: () => Promise<string>): Promise<HandlerResult> {
  const start = Date.now()
  try {
    const detail = await fn()
    return { name, ok: true, durationMs: Date.now() - start, detail }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.error(`[${name}] Error: ${msg}`)
    return { name, ok: false, durationMs: Date.now() - start, error: msg }
  }
}

async function run(env: Env): Promise<HandlerResult[]> {
  const results: HandlerResult[] = []

  // Run handlers sequentially — order matters for health-check (it records
  // the full run duration, so it goes last).
  results.push(await runHandler('booking-enrichment', () => runBookingEnrichment(env)))
  results.push(await runHandler('booking-cleanup', () => runBookingCleanup(env.DB)))
  results.push(await runHandler('health-check', () => runHealthCheck(env.DB, results)))

  for (const r of results) {
    const status = r.ok ? 'OK' : 'FAIL'
    console.log(`[${r.name}] ${status} (${r.durationMs}ms)${r.detail ? ` — ${r.detail}` : ''}`)
  }

  return results
}

export default {
  async scheduled(
    _controller: ScheduledController,
    env: Env,
    _ctx: ExecutionContext
  ): Promise<void> {
    await run(env)
  },

  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)

    if (url.pathname === '/health') {
      const latest = await env.DB.prepare(
        `SELECT ran_at, summary FROM worker_heartbeats
         WHERE worker_name = 'follow-up-processor'
         ORDER BY ran_at DESC LIMIT 1`
      ).first<{ ran_at: string; summary: string }>()

      return new Response(
        JSON.stringify({
          status: 'ok',
          worker: 'follow-up-processor',
          last_heartbeat: latest?.ran_at ?? null,
          last_summary: latest?.summary ?? null,
        }),
        { headers: { 'Content-Type': 'application/json' } }
      )
    }

    if (url.pathname === '/run' && request.method === 'POST') {
      const results = await run(env)
      return new Response(JSON.stringify(results, null, 2), {
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return new Response('Not Found', { status: 404 })
  },
} satisfies ExportedHandler<Env>
