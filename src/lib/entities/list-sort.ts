/**
 * Per-stage in-memory re-sort for the entity list. The DAL returns rows
 * ordered by `tier → pain_score → updated_at` — fine for stages without
 * an explicit operator workflow, but several stages have a sharper
 * sort signal hidden in hydrated row data:
 *
 *   prospect : "inert" rows (no outreach drafted yet) cluster at the top —
 *              these are the ones the operator most likely dropped on
 *              the floor. Within each cluster, oldest next_action_at first
 *              (most overdue at the top), nulls trailing.
 *   meetings : sub-state priority — "completed-awaiting-proposal" first
 *              (hottest, "draft now"), then past-due, then upcoming, then
 *              awaiting-booking. Within "upcoming" we order by scheduled
 *              date ascending.
 *   lost     : most-recently-lost first — operators almost always want
 *              "what just fell out" rather than the tiered ordering.
 *
 * Other stages keep the DAL order. Each helper is pure and stable: rows
 * tied on the primary key fall back to the array's incoming order.
 */

import type { Entity } from '../db/entities'
import type { Meeting } from '../db/meetings'
import type { Quote } from '../db/quotes'
import type { ContextEntry } from '../db/context'
import { getMeetingSubstate, type MeetingSubstate } from './meeting-substate'

const MEETING_SUBSTATE_RANK: Record<MeetingSubstate, number> = {
  'completed-awaiting-proposal': 0,
  'past-due': 1,
  upcoming: 2,
  'awaiting-booking': 3,
  other: 4,
}

/**
 * Stable sort by a key extractor. `Array#sort` in V8 is already stable as
 * of ES2019, but this helper keeps the call sites self-documenting and
 * avoids accidentally returning non-numeric comparator results.
 */
function stableSortBy<T>(arr: T[], key: (item: T) => number): T[] {
  return [...arr]
    .map((item, idx) => ({ item, idx, k: key(item) }))
    .sort((a, b) => a.k - b.k || a.idx - b.idx)
    .map((x) => x.item)
}

export function sortProspectRows(
  entities: Entity[],
  outreachByEntityId: Map<string, ContextEntry>
): Entity[] {
  // Two-pass: split inert vs not-inert (preserving DAL order within
  // each), then sort each by next_action_at ASC NULLS LAST, then
  // concatenate.
  const inert: Entity[] = []
  const active: Entity[] = []
  for (const e of entities) {
    if (outreachByEntityId.has(e.id)) active.push(e)
    else inert.push(e)
  }

  const byNextActionAsc = (e: Entity): number => {
    if (!e.next_action_at) return Number.MAX_SAFE_INTEGER
    return new Date(e.next_action_at).getTime()
  }

  return [...stableSortBy(inert, byNextActionAsc), ...stableSortBy(active, byNextActionAsc)]
}

export function sortMeetingsRows(
  entities: Entity[],
  meetingsByEntityId: Map<string, Meeting[]>,
  quotesByEntityId: Map<string, Quote[]>,
  now: Date = new Date()
): Entity[] {
  const rank = (e: Entity): number => {
    const meetings = meetingsByEntityId.get(e.id) ?? []
    const quotes = quotesByEntityId.get(e.id) ?? []
    const sub = getMeetingSubstate(meetings, quotes, now)
    if (!sub) return MEETING_SUBSTATE_RANK.other + 1
    return MEETING_SUBSTATE_RANK[sub]
  }

  // Within "upcoming" rows, secondary order by scheduled_at ASC.
  // We compute a composite key: rank * LARGE + (date offset).
  const PRIORITY_WEIGHT = 1e15
  const composite = (e: Entity): number => {
    const r = rank(e)
    if (r !== MEETING_SUBSTATE_RANK.upcoming) return r * PRIORITY_WEIGHT
    const meetings = meetingsByEntityId.get(e.id) ?? []
    const earliestFuture = meetings
      .filter((m) => m.status === 'scheduled' && m.scheduled_at)
      .map((m) => new Date(m.scheduled_at as string).getTime())
      .filter((t) => t >= now.getTime())
      .sort((a, b) => a - b)[0]
    return r * PRIORITY_WEIGHT + (earliestFuture ?? 0)
  }

  return stableSortBy(entities, composite)
}

export function sortLostRows(entities: Entity[]): Entity[] {
  // Most recently lost first — string comparison works because
  // stage_changed_at is ISO 8601.
  return [...entities].sort((a, b) => b.stage_changed_at.localeCompare(a.stage_changed_at))
}
