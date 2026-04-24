/**
 * Health check handler.
 *
 * Writes a heartbeat row to worker_heartbeats so deploy monitoring
 * tooling can confirm the cron is alive and running. Also prunes
 * heartbeat rows older than 7 days to prevent unbounded growth.
 */

interface HandlerResult {
  name: string
  ok: boolean
  durationMs: number
  detail?: string
  error?: string
}

const WORKER_NAME = 'follow-up-processor'
const RETENTION_DAYS = 7

/**
 * Record a heartbeat and prune old rows. The summary includes results
 * from all handlers that ran before this one (health-check runs last).
 */
export async function runHealthCheck(
  db: D1Database,
  priorResults: HandlerResult[]
): Promise<string> {
  const totalDuration = priorResults.reduce((sum, r) => sum + r.durationMs, 0)
  const summary = priorResults.map((r) => `${r.name}:${r.ok ? 'ok' : 'fail'}`).join(', ')

  const id = crypto.randomUUID()

  await db
    .prepare(
      `INSERT INTO worker_heartbeats (id, worker_name, duration_ms, summary)
       VALUES (?, ?, ?, ?)`
    )
    .bind(id, WORKER_NAME, totalDuration, summary)
    .run()

  // Prune old heartbeat rows
  const pruneResult = await db
    .prepare(
      `DELETE FROM worker_heartbeats
       WHERE worker_name = ? AND ran_at < datetime('now', ?)`
    )
    .bind(WORKER_NAME, `-${RETENTION_DAYS} days`)
    .run()
  const pruned = pruneResult.meta?.changes ?? 0

  return `heartbeat written, pruned=${pruned}`
}
