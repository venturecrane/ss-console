/**
 * Aggregate queries against the `events` table (migration 0024) for the
 * admin analytics dashboard. Unlike the rest of the analytics module,
 * these queries are NOT org-scoped — events are anonymous marketing-side
 * signals, not tenant data.
 *
 * All queries are parameterized, ranged by timestamp, and return empty
 * arrays / zeros gracefully so the dashboard renders cleanly on a fresh
 * install.
 */

const MS_PER_DAY = 24 * 60 * 60 * 1000

export interface TopPathRow {
  path: string
  views: number
}

export interface TopCtaRow {
  cta: string
  clicks: number
}

export interface DailyUniqueRow {
  day: string
  uniques: number
}

export interface FunnelCounts {
  landing: number
  scorecard_start: number
  book_start: number
  book_complete: number
}

/**
 * Top N paths by page_view count in the last `days` days.
 */
export async function getTopPaths(db: D1Database, days = 7, limit = 10): Promise<TopPathRow[]> {
  const since = Date.now() - days * MS_PER_DAY
  const result = await db
    .prepare(
      `SELECT path, COUNT(*) as views
       FROM events
       WHERE event_name = 'page_view' AND ts >= ? AND path IS NOT NULL
       GROUP BY path
       ORDER BY views DESC
       LIMIT ?`
    )
    .bind(since, limit)
    .all<{ path: string; views: number }>()
  return (result.results ?? []).map((r) => ({ path: r.path, views: r.views }))
}

/**
 * Top N CTAs by cta_click count in the last `days` days.
 * Pulls `cta` out of the JSON metadata blob via SQLite's json_extract.
 */
export async function getTopCtas(db: D1Database, days = 7, limit = 10): Promise<TopCtaRow[]> {
  const since = Date.now() - days * MS_PER_DAY
  const result = await db
    .prepare(
      `SELECT json_extract(metadata, '$.cta') as cta, COUNT(*) as clicks
       FROM events
       WHERE event_name = 'cta_click' AND ts >= ? AND metadata IS NOT NULL
       GROUP BY cta
       HAVING cta IS NOT NULL
       ORDER BY clicks DESC
       LIMIT ?`
    )
    .bind(since, limit)
    .all<{ cta: string; clicks: number }>()
  return (result.results ?? []).map((r) => ({ cta: r.cta, clicks: r.clicks }))
}

/**
 * Daily unique session counts for the last `days` days, oldest first.
 * Zero-fills missing days so the bar chart renders a continuous axis.
 */
export async function getDailyUniques(db: D1Database, days = 30): Promise<DailyUniqueRow[]> {
  const since = Date.now() - days * MS_PER_DAY
  const result = await db
    .prepare(
      `SELECT date(ts / 1000, 'unixepoch') as day, COUNT(DISTINCT session_id) as uniques
       FROM events
       WHERE ts >= ?
       GROUP BY day
       ORDER BY day ASC`
    )
    .bind(since)
    .all<{ day: string; uniques: number }>()

  const byDay = new Map<string, number>()
  for (const row of result.results ?? []) {
    byDay.set(row.day, row.uniques)
  }

  const out: DailyUniqueRow[] = []
  const today = new Date()
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today.getTime() - i * MS_PER_DAY)
    const key = d.toISOString().slice(0, 10)
    out.push({ day: key, uniques: byDay.get(key) ?? 0 })
  }
  return out
}

/**
 * Simple conversion funnel over the last `days` days. Counts distinct
 * sessions that reached each step. Steps are ordered:
 *
 *   landing         — any page_view
 *   scorecard_start — click on scorecard-start-* CTA
 *   book_start      — page_view of /book
 *   book_complete   — click on book-submit CTA (the actual booking POST
 *                     happens server-side but the client CTA is the
 *                     nearest user-visible signal)
 */
export async function getFunnelCounts(db: D1Database, days = 7): Promise<FunnelCounts> {
  const since = Date.now() - days * MS_PER_DAY
  const [landing, scorecardStart, bookStart, bookComplete] = await Promise.all([
    db
      .prepare(
        `SELECT COUNT(DISTINCT session_id) as n
         FROM events
         WHERE event_name = 'page_view' AND ts >= ?`
      )
      .bind(since)
      .first<{ n: number }>(),
    db
      .prepare(
        `SELECT COUNT(DISTINCT session_id) as n
         FROM events
         WHERE event_name = 'cta_click'
           AND ts >= ?
           AND json_extract(metadata, '$.cta') LIKE 'scorecard-start-%'`
      )
      .bind(since)
      .first<{ n: number }>(),
    db
      .prepare(
        `SELECT COUNT(DISTINCT session_id) as n
         FROM events
         WHERE event_name = 'page_view' AND ts >= ? AND path = '/book'`
      )
      .bind(since)
      .first<{ n: number }>(),
    db
      .prepare(
        `SELECT COUNT(DISTINCT session_id) as n
         FROM events
         WHERE event_name = 'cta_click'
           AND ts >= ?
           AND json_extract(metadata, '$.cta') = 'book-submit'`
      )
      .bind(since)
      .first<{ n: number }>(),
  ])

  return {
    landing: landing?.n ?? 0,
    scorecard_start: scorecardStart?.n ?? 0,
    book_start: bookStart?.n ?? 0,
    book_complete: bookComplete?.n ?? 0,
  }
}
