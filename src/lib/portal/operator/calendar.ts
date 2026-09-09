/**
 * Operator calendar list — typed contract + read resolver.
 *
 * Per #872, the calendar surface shows two kinds of items the Operator
 * is tracking against the customer's external calendar (MS Graph / Google
 * Calendar via the connector tracked in #822):
 *
 *   ai_scheduled — committed events the AI added to calendar
 *                  (already sync'd to the customer's external calendar)
 *   ai_proposed  — drafts the AI suggests for scheduling; awaiting
 *                  reviewer approval before they sync out
 *
 * Both kinds render in a single chronological agenda. The reviewer sees
 * at a glance what is already on the calendar vs. what is waiting on a
 * decision, plus any time-conflicts the AI surfaced.
 *
 * Data source: per-customer Hermes Machine D1 (ADR 0007 + 0009). The
 * portal Worker can NOT bind to a per-customer D1 directly; reads go
 * through an internal Hermes bridge (PR #907 architecture, runtime
 * wiring tracked in #821). Until that bridge ships AND the MS Graph /
 * Google Calendar connector lands (#822), this resolver returns an
 * empty list with the correct typed shape so the page machinery
 * (filter bar, agenda render, conflict detection) works end-to-end
 * without fabricating events. No mock data. No placeholder copy. See
 * docs/style/empty-state-pattern.md.
 *
 * Drag-drop reschedule is deferred to a follow-on issue. This module
 * owns the read path only — it does not (yet) expose mutation helpers.
 *
 * This module owns:
 *   - The CalendarItem row shape consumed by the agenda UI
 *   - The CalendarItemType vocabulary (ai_scheduled / ai_proposed)
 *   - Filter / sort / pagination parameter parsing from URLSearchParams
 *   - The pure detectConflicts function over an item list
 *   - The (currently empty) data fetch
 *
 * When the bridge lands, only `fetchCalendarItemsFromHermes` changes.
 * Filter parsing, conflict detection, formatters, and the page UI
 * stay put.
 */

import type { SubscriptionRow } from '../product-access'
import { type Page, paginate } from './pagination'

/**
 * The two kinds of items that land in the calendar surface. The
 * vocabulary is closed; adding a third value requires a PRD amendment
 * and matching label/tone updates in the row component.
 *
 *   ai_scheduled — Already on the customer's external calendar via the
 *                  connector. Reviewer's job: confirm it stays.
 *   ai_proposed  — Awaiting reviewer approval. Reviewer's job: approve
 *                  or reject before the AI syncs it out.
 */
export type CalendarItemType = 'ai_scheduled' | 'ai_proposed'

export const CALENDAR_ITEM_TYPES: readonly CalendarItemType[] = [
  'ai_scheduled',
  'ai_proposed',
] as const

/**
 * Sort options exposed via `?sort=`. Default is `start_asc` (chronological,
 * earliest first) — agenda surfaces read top-to-bottom as "what's next."
 * `start_desc` is for reviewing recent / past scheduling activity. The
 * Drafts surface defaults to `age_desc`; we deliberately diverge because
 * the calendar question is "what's coming up," not "what's new."
 */
export type CalendarSort = 'start_asc' | 'start_desc'

export const CALENDAR_SORTS: readonly CalendarSort[] = ['start_asc', 'start_desc'] as const

/**
 * One row in the calendar agenda. Shape mirrors what the Hermes bridge
 * will return when #821 + #822 land. Field semantics:
 *
 *   id              — Stable item identifier. Forms the detail-page URL.
 *   type            — ai_scheduled vs ai_proposed. Drives the tone of
 *                     the chip and the action affordances.
 *   title           — Short title of the calendar entry (e.g.,
 *                     "Deposition: Acme v. Beta").
 *   startsAt        — ISO 8601 timestamp (UTC) of the item's start. The
 *                     UI converts to the customer's tz at render time.
 *   endsAt          — ISO 8601 timestamp (UTC) of the item's end. Must
 *                     be > startsAt; the resolver validates.
 *   skill           — Slug of the capability that produced the item
 *                     (e.g., "deposition-scheduling", "hearing-prep").
 *                     Drives the source label and skill filter.
 *   relatedMatterId — Optional link to a matter (per #871). When set,
 *                     the row renders a "Matter" cell linking out to
 *                     the matter detail page. null when the item has
 *                     no matter context.
 *   relatedMatterTitle — Optional display title for the matter cell.
 *                     null when relatedMatterId is null.
 *   location        — Optional free-text location (room, address, video
 *                     link). null when not applicable.
 */
export interface CalendarItem {
  id: string
  type: CalendarItemType
  title: string
  startsAt: string
  endsAt: string
  skill: string
  relatedMatterId: string | null
  relatedMatterTitle: string | null
  location: string | null
}

/**
 * Filter / sort / pagination parameters parsed from URLSearchParams.
 * All optional — the page renders an unfiltered list when nothing is
 * passed. Mirrors the contract used in drafts.ts so reviewers who learn
 * one surface know the other.
 *
 * types         — Multi-select of CalendarItemType. Empty array means
 *                 "all types".
 * fromIso       — Lower bound (inclusive) on item start. null = no lower
 *                 bound. Validated as a finite ISO date string; invalid
 *                 values fall back to null rather than throwing.
 * toIso         — Upper bound (inclusive) on item start. null = no upper
 *                 bound. Validated as a finite ISO date string.
 * sort          — One of CALENDAR_SORTS. Defaults to 'start_asc'.
 * page          — 1-indexed page number. Defaults to 1.
 * pageSize      — Defaults to 50 (matches drafts). Capped at 200 so a
 *                 hostile query string can't drag a Worker over CPU limit.
 */
export interface CalendarListParams {
  types: readonly CalendarItemType[]
  fromIso: string | null
  toIso: string | null
  sort: CalendarSort
  page: number
  pageSize: number
}

export const DEFAULT_CALENDAR_PAGE_SIZE = 50
export const MAX_CALENDAR_PAGE_SIZE = 200

function isValidIsoDate(value: string): boolean {
  const ms = Date.parse(value)
  return Number.isFinite(ms)
}

/**
 * Parse params from URLSearchParams. Defensive — every field is
 * validated against the closed vocabularies and date semantics above;
 * unknown values fall back to safe defaults instead of throwing. Keeps
 * the surface stable under user-typed URLs and bookmark drift.
 *
 * `type` supports comma-separated values for multi-select
 * (`?type=ai_scheduled,ai_proposed`) and repeated params
 * (`?type=ai_scheduled&type=ai_proposed`). Any unknown value in the set
 * is silently dropped — the page falls back to "all types" rather than
 * 400-ing on a typo'd bookmark.
 */
export function parseCalendarListParams(searchParams: URLSearchParams): CalendarListParams {
  const knownTypes = new Set<CalendarItemType>(CALENDAR_ITEM_TYPES)
  const rawTypeParams = searchParams.getAll('type')
  const types = Array.from(
    new Set(
      rawTypeParams
        .flatMap((value) => value.split(','))
        .map((value) => value.trim())
        .filter((value): value is CalendarItemType => knownTypes.has(value as CalendarItemType))
    )
  )

  const rawFrom = searchParams.get('from')?.trim() ?? ''
  const fromIso = rawFrom.length > 0 && isValidIsoDate(rawFrom) ? rawFrom : null

  const rawTo = searchParams.get('to')?.trim() ?? ''
  const toIso = rawTo.length > 0 && isValidIsoDate(rawTo) ? rawTo : null

  const rawSort = searchParams.get('sort')
  const sort: CalendarSort = CALENDAR_SORTS.includes(rawSort as CalendarSort)
    ? (rawSort as CalendarSort)
    : 'start_asc'

  const rawPage = Number(searchParams.get('page'))
  const page = Number.isFinite(rawPage) && rawPage >= 1 ? Math.floor(rawPage) : 1

  const rawPageSize = Number(searchParams.get('pageSize'))
  const pageSize =
    Number.isFinite(rawPageSize) && rawPageSize >= 1
      ? Math.min(Math.floor(rawPageSize), MAX_CALENDAR_PAGE_SIZE)
      : DEFAULT_CALENDAR_PAGE_SIZE

  return { types, fromIso, toIso, sort, page, pageSize }
}

/**
 * Apply filters to a CalendarItem list in-memory. Exposed for the
 * resolver below and for unit tests. The Hermes bridge will eventually
 * push filtering server-side; for now this is the source of truth so
 * the empty-list contract has tested filter semantics.
 */
export function applyCalendarFilters(
  rows: readonly CalendarItem[],
  params: CalendarListParams
): CalendarItem[] {
  let result = rows.slice()

  if (params.types.length > 0) {
    const wanted = new Set(params.types)
    result = result.filter((row) => wanted.has(row.type))
  }

  if (params.fromIso !== null) {
    const fromMs = Date.parse(params.fromIso)
    result = result.filter((row) => {
      const startMs = Date.parse(row.startsAt)
      return Number.isFinite(startMs) && startMs >= fromMs
    })
  }

  if (params.toIso !== null) {
    const toMs = Date.parse(params.toIso)
    result = result.filter((row) => {
      const startMs = Date.parse(row.startsAt)
      return Number.isFinite(startMs) && startMs <= toMs
    })
  }

  return result
}

/**
 * Sort a (pre-filtered) CalendarItem list according to params.sort.
 * Items with unparseable startsAt sort last (their parse result is NaN
 * — Number comparisons return false either way, so they end up at the
 * tail of either direction). That matches the empty-state design: the
 * resolver should never emit unparseable dates, and if it ever does
 * (bridge bug, future schema drift), the visual signal is "this row is
 * out of place" rather than "this row is silently first."
 */
export function applyCalendarSort(
  rows: readonly CalendarItem[],
  sort: CalendarSort
): CalendarItem[] {
  const sorted = rows.slice()
  sorted.sort((a, b) => {
    const aMs = Date.parse(a.startsAt)
    const bMs = Date.parse(b.startsAt)
    const aFinite = Number.isFinite(aMs)
    const bFinite = Number.isFinite(bMs)
    if (!aFinite && !bFinite) return 0
    if (!aFinite) return 1
    if (!bFinite) return -1
    return sort === 'start_asc' ? aMs - bMs : bMs - aMs
  })
  return sorted
}

/** Pagination return value. See {@link Page} (pagination.ts). */
export type CalendarListPage = Page<CalendarItem>

/**
 * Apply offset-based pagination to a sorted+filtered list. Thin wrapper over
 * the shared {@link paginate}; kept named for call-site + test stability.
 */
export function paginateCalendarItems(
  rows: readonly CalendarItem[],
  page: number,
  pageSize: number
): CalendarListPage {
  return paginate(rows, page, pageSize)
}

/**
 * Compose filter → sort → paginate over an in-memory CalendarItem list.
 * Useful for unit tests; the page calls listCalendarItems below.
 */
export function buildCalendarListPage(
  rows: readonly CalendarItem[],
  params: CalendarListParams
): CalendarListPage {
  const filtered = applyCalendarFilters(rows, params)
  const sorted = applyCalendarSort(filtered, params.sort)
  return paginateCalendarItems(sorted, params.page, params.pageSize)
}

/**
 * Detect time-conflicts across a CalendarItem list.
 *
 * Returns a Map keyed by item id; each value is the list of OTHER item
 * ids that overlap with the keyed item in time. An item with no
 * conflicts is omitted from the map (the caller should treat a missing
 * key as "no conflicts" — same shape as a Set/Map of "violators").
 *
 * Overlap rule: two items conflict if their half-open intervals
 * [startsAt, endsAt) overlap. Touching at a single instant
 * (a.endsAt === b.startsAt) does NOT count as a conflict — that's a
 * back-to-back schedule, which is intentional, not a clash.
 *
 * Items with malformed or zero-length ranges (NaN starts / ends, or
 * start >= end) are silently excluded from conflict checks rather than
 * matched against everything. The empty-state contract guarantees the
 * resolver never emits such rows; this is a defensive zero-impact path
 * if the bridge ever does.
 *
 * Algorithmic note: this runs in O(n log n + k) where n is the input
 * length and k is the number of overlapping pairs — sort by start,
 * sweep, keep a min-heap-of-ends would be the textbook O(n log n)
 * version. For agenda surfaces with n in the dozens-to-hundreds, the
 * simpler O(n²) sweep below is faster in practice (no allocator
 * pressure, tight loop) and easier to read. If the calendar ever
 * surfaces thousands of items on one page, swap this for the sweep.
 */
export function detectConflicts(items: readonly CalendarItem[]): Map<string, string[]> {
  const ranges = items
    .map((item) => {
      const start = Date.parse(item.startsAt)
      const end = Date.parse(item.endsAt)
      return { id: item.id, start, end }
    })
    .filter(
      (range) =>
        Number.isFinite(range.start) && Number.isFinite(range.end) && range.start < range.end
    )

  const conflicts = new Map<string, Set<string>>()
  for (let i = 0; i < ranges.length; i++) {
    for (let j = i + 1; j < ranges.length; j++) {
      const a = ranges[i]
      const b = ranges[j]
      // Half-open overlap: a.start < b.end AND b.start < a.end. Touching
      // (a.end === b.start) is NOT overlap by this rule.
      if (a.start < b.end && b.start < a.end) {
        const aSet = conflicts.get(a.id) ?? new Set<string>()
        aSet.add(b.id)
        conflicts.set(a.id, aSet)
        const bSet = conflicts.get(b.id) ?? new Set<string>()
        bSet.add(a.id)
        conflicts.set(b.id, bSet)
      }
    }
  }

  // Materialize as Map<string, string[]> per the public signature.
  // Sort each list so the order is deterministic for tests / hashing.
  const result = new Map<string, string[]>()
  for (const [id, set] of conflicts) {
    result.set(id, Array.from(set).sort())
  }
  return result
}

/**
 * Human label for a CalendarItemType value. Closed vocabulary; see
 * CalendarItemType. Used by the row component's type chip.
 */
export function formatCalendarItemType(type: CalendarItemType): string {
  switch (type) {
    case 'ai_scheduled':
      return 'Scheduled'
    case 'ai_proposed':
      return 'Proposed'
  }
}

/**
 * Format an ISO datetime as a compact "date · time range" string for
 * the agenda row, given an end timestamp. Falls back to the raw value
 * if the date cannot be parsed (defensive — empty-state contract says
 * this should never happen, but a bridge bug shouldn't crash the page).
 *
 * Returns three independently-renderable strings so the row component
 * can place them in distinct typography cells. The shape:
 *
 *   datePart — "Tue, May 21"
 *   timePart — "9:00 AM – 10:30 AM"
 *   tzPart   — "PDT" (the resolved tz abbreviation)
 *
 * Time-zone resolution is the runtime's default tz (the customer's
 * local time when rendered on their device — Astro is SSR, so the
 * server renders the Operator's configured business tz once
 * customer-tz is plumbed through; for now we render UTC for stability).
 */
export interface FormattedTimeRange {
  datePart: string
  timePart: string
  tzPart: string
}

export function formatTimeRange(startsAt: string, endsAt: string): FormattedTimeRange {
  const startMs = Date.parse(startsAt)
  const endMs = Date.parse(endsAt)
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) {
    return { datePart: startsAt, timePart: endsAt, tzPart: '' }
  }
  const start = new Date(startMs)
  const end = new Date(endMs)
  // Render in UTC for SSR determinism. When customer-tz is plumbed
  // through the subscription settings (#822 follow-on), thread the
  // resolved tz into Intl options below.
  const dateFmt = new Intl.DateTimeFormat('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
  const timeFmt = new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    timeZone: 'UTC',
  })
  return {
    datePart: dateFmt.format(start),
    timePart: `${timeFmt.format(start)} to ${timeFmt.format(end)}`,
    tzPart: 'UTC',
  }
}

/**
 * Server-side resolver invoked by the calendar list page. Today this
 * returns an empty list with the correct shape — the per-customer
 * Hermes bridge that feeds real items is tracked in #821, and the
 * MS Graph / Google Calendar connector that feeds Hermes is #822. When
 * both land, swap fetchCalendarItemsFromHermes for the bridge call and
 * leave the rest of the page machinery in place.
 *
 * IMPORTANT: do not seed mock rows here. The empty-state pattern is
 * the design contract (docs/style/empty-state-pattern.md) — the page
 * must render its empty state until real data lands, never fabricated
 * placeholders.
 */
export async function listCalendarItems(
  _subscription: SubscriptionRow,
  params: CalendarListParams
): Promise<CalendarListPage> {
  const rows = await fetchCalendarItemsFromHermes(_subscription)
  return buildCalendarListPage(rows, params)
}

/**
 * Hermes bridge stub. Returns an empty list. When #821 (Hermes runtime
 * wiring) and #822 (MS Graph / Google Calendar connector) land, replace
 * the body with the bridge fetch — the subscription row carries the
 * customer identity needed to route to the right Machine D1.
 * Promise.resolve keeps the call shape async so the future swap is
 * body-only.
 */
function fetchCalendarItemsFromHermes(_subscription: SubscriptionRow): Promise<CalendarItem[]> {
  return Promise.resolve([])
}
