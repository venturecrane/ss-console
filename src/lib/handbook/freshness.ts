/**
 * Handbook freshness and reading order: pure functions the page renderer calls.
 *
 * `last_reviewed` is set only by a deliberate check of a page's body against its
 * sources, never by an edit. A page without it is "not yet reviewed", which is
 * the honest state for a page nobody has checked; an old date is not.
 *
 * Dates are compared as UTC calendar days. Frontmatter dates are date-only, so
 * they parse as UTC midnight; comparing whole UTC days keeps a Phoenix evening
 * (already the next UTC day) from shifting the count by one in either direction.
 */

import { HANDBOOK_SECTIONS, type HandbookSectionKey } from './sections'

const MS_PER_DAY = 86_400_000

function utcDayNumber(d: Date): number {
  return Math.floor(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) / MS_PER_DAY)
}

export type ReviewStatus =
  | { kind: 'unreviewed' }
  | { kind: 'current'; daysSince: number; dueInDays: number }
  | { kind: 'overdue'; daysSince: number; overdueByDays: number }

export function reviewStatus(
  lastReviewed: Date | undefined,
  reviewEvery: number | undefined,
  now: Date
): ReviewStatus {
  if (!lastReviewed || Number.isNaN(lastReviewed.getTime())) return { kind: 'unreviewed' }
  const daysSince = utcDayNumber(now) - utcDayNumber(lastReviewed)
  if (!reviewEvery || reviewEvery <= 0) {
    return { kind: 'current', daysSince, dueInDays: Number.POSITIVE_INFINITY }
  }
  const remaining = reviewEvery - daysSince
  if (remaining < 0) return { kind: 'overdue', daysSince, overdueByDays: -remaining }
  return { kind: 'current', daysSince, dueInDays: remaining }
}

export interface OrderedEntry {
  id: string
  title: string
  section: HandbookSectionKey
  order?: number
}

/** All entries in reading order: section display order, then `order`, then title. */
export function readingOrder<T extends OrderedEntry>(entries: readonly T[]): T[] {
  const rank = new Map(HANDBOOK_SECTIONS.map((s, i) => [s.key, i]))
  return [...entries].sort(
    (a, b) =>
      (rank.get(a.section) ?? 99) - (rank.get(b.section) ?? 99) ||
      (a.order ?? 999) - (b.order ?? 999) ||
      a.title.localeCompare(b.title)
  )
}

/** The pages before and after `id` in reading order. */
export function neighbours<T extends OrderedEntry>(
  entries: readonly T[],
  id: string
): { prev?: T; next?: T } {
  const ordered = readingOrder(entries)
  const i = ordered.findIndex((e) => e.id === id)
  if (i < 0) return {}
  return { prev: ordered[i - 1], next: ordered[i + 1] }
}
