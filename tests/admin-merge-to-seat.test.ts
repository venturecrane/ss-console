/**
 * Merge to seat (the admin Fleet page's card). Pins the properties that make
 * the number trustworthy: it measures to the LAST client seat, a release
 * missing a merge time or a client seat is never given a number, SMD's own
 * seats never count, a malformed row is dropped rather than coerced, and a
 * week with nothing measured has no median.
 */

import { describe, it, expect } from 'vitest'
import {
  clientSeats,
  parseRow,
  summarizeMergeToSeat,
  phoenixTime,
  weekLabel,
  type MergeToSeatRow,
} from '../src/lib/admin/merge-to-seat'
import type { SeatPurposeEntry } from '../src/lib/operator/seat-purposes.generated'

const SEATS = ['client-a', 'client-b']
// Wednesday 2026-11-04; its ISO week starts Monday 2026-11-02.
const NOW = new Date('2026-11-04T12:00:00Z')

function row(over: Partial<MergeToSeatRow>): MergeToSeatRow {
  return {
    releaseId: 'img-202611040000-aaaaaaaa',
    ssConsoleSha: 'a'.repeat(40),
    createdAt: '2026-11-04T00:10:00Z',
    mergedAt: '2026-11-04T00:00:00Z',
    seat: null,
    finishedAt: null,
    ...over,
  }
}

/** One release whose two client seats finished `aMin` and `bMin` after the merge at `merged`. */
function release(id: string, merged: string, aMin: number, bMin: number): MergeToSeatRow[] {
  const at = (m: number) =>
    new Date(Date.parse(merged) + m * 60000).toISOString().replace(/\.\d{3}Z$/, 'Z')
  const base = { releaseId: id, mergedAt: merged, createdAt: at(5) }
  return [
    row({ ...base, seat: 'client-a', finishedAt: at(aMin) }),
    row({ ...base, seat: 'client-b', finishedAt: at(bMin) }),
  ]
}

describe('summarizeMergeToSeat', () => {
  it('measures merge and image build to the LAST client seat done', () => {
    const { releases } = summarizeMergeToSeat(
      release('img-1', '2026-11-04T00:00:00Z', 20, 45),
      SEATS,
      NOW
    )
    expect(releases).toEqual([
      {
        releaseId: 'img-1',
        shortSha: 'aaaaaaaa',
        mergedAt: '2026-11-04T00:00:00Z',
        imageBuiltAt: '2026-11-04T00:05:00Z',
        allClientsDoneAt: '2026-11-04T00:45:00Z',
        minutesMergeToClients: 45,
        minutesImageToClients: 40,
      },
    ])
  })

  it('a release with no merge time has no merge minutes but keeps its image minutes', () => {
    const rows = release('img-1', '2026-11-04T00:00:00Z', 20, 45).map((r) => ({
      ...r,
      mergedAt: null,
    }))
    const [r] = summarizeMergeToSeat(rows, SEATS, NOW).releases
    expect(r.mergedAt).toBeNull()
    expect(r.minutesMergeToClients).toBeNull()
    expect(r.minutesImageToClients).toBe(40)
  })

  it('a release with one client seat not done has no all-clients time', () => {
    const rows = release('img-1', '2026-11-04T00:00:00Z', 20, 45).slice(0, 1)
    const [r] = summarizeMergeToSeat(rows, SEATS, NOW).releases
    expect(r.allClientsDoneAt).toBeNull()
    expect(r.minutesMergeToClients).toBeNull()
    expect(r.minutesImageToClients).toBeNull()
  })

  it('a release no seat has finished still appears, with nothing measured', () => {
    const { releases, weeks } = summarizeMergeToSeat([row({})], SEATS, NOW)
    expect(releases).toHaveLength(1)
    expect(releases[0].allClientsDoneAt).toBeNull()
    expect(weeks).toEqual([])
  })

  it('a non-client seat finishing last does not move the number', () => {
    const rows = [
      ...release('img-1', '2026-11-04T00:00:00Z', 20, 45),
      row({ releaseId: 'img-1', seat: 'smd-staging', finishedAt: '2026-11-04T09:00:00Z' }),
    ]
    expect(summarizeMergeToSeat(rows, SEATS, NOW).releases[0].minutesMergeToClients).toBe(45)
  })

  it('a seat rolled out twice counts its latest done', () => {
    const rows = [
      ...release('img-1', '2026-11-04T00:00:00Z', 20, 45),
      row({ releaseId: 'img-1', seat: 'client-a', finishedAt: '2026-11-04T01:30:00Z' }),
    ]
    expect(summarizeMergeToSeat(rows, SEATS, NOW).releases[0].minutesMergeToClients).toBe(90)
  })

  it('no client seats means no release is measured (never a vacuous zero)', () => {
    const { releases } = summarizeMergeToSeat(
      release('img-1', '2026-11-04T00:00:00Z', 20, 45),
      [],
      NOW
    )
    expect(releases[0].allClientsDoneAt).toBeNull()
  })

  it('weekly medians: odd and even counts, newest week first, an empty week absent', () => {
    const rows = [
      // Week of 2026-11-02: 30, 45, 60 -> median 45.
      ...release('img-a', '2026-11-02T10:00:00Z', 10, 30),
      ...release('img-b', '2026-11-03T10:00:00Z', 10, 45),
      ...release('img-c', '2026-11-04T10:00:00Z', 10, 60),
      // Week of 2026-10-26 has nothing. Week of 2026-10-19: 20, 50 -> median 35.
      ...release('img-d', '2026-10-20T10:00:00Z', 10, 20),
      ...release('img-e', '2026-10-21T10:00:00Z', 10, 50),
      // Nine weeks back: outside the window.
      ...release('img-f', '2026-09-01T10:00:00Z', 10, 999),
    ]
    expect(summarizeMergeToSeat(rows, SEATS, NOW).weeks).toEqual([
      { weekStart: '2026-11-02', medianMinutes: 45, releases: 3 },
      { weekStart: '2026-10-19', medianMinutes: 35, releases: 2 },
    ])
  })

  it('a release with no merge time is left out of its week', () => {
    const rows = [
      ...release('img-a', '2026-11-02T10:00:00Z', 10, 30),
      ...release('img-b', '2026-11-03T10:00:00Z', 10, 45).map((r) => ({ ...r, mergedAt: null })),
    ]
    expect(summarizeMergeToSeat(rows, SEATS, NOW).weeks).toEqual([
      { weekStart: '2026-11-02', medianMinutes: 30, releases: 1 },
    ])
  })

  it('a release finishing on Sunday belongs to the week that started the Monday before', () => {
    const rows = release('img-a', '2026-11-01T22:00:00Z', 10, 30) // Sunday 2026-11-01
    expect(summarizeMergeToSeat(rows, SEATS, NOW).weeks[0].weekStart).toBe('2026-10-26')
  })

  it('orders releases newest image first', () => {
    const rows = [
      ...release('img-old', '2026-11-02T10:00:00Z', 10, 30),
      ...release('img-new', '2026-11-03T10:00:00Z', 10, 30),
    ]
    expect(summarizeMergeToSeat(rows, SEATS, NOW).releases.map((r) => r.releaseId)).toEqual([
      'img-new',
      'img-old',
    ])
  })
})

describe('parseRow', () => {
  const raw = {
    release_id: 'img-202611040000-aaaaaaaa',
    ss_console_sha: 'a'.repeat(40),
    created_at: '2026-11-04T00:10:00Z',
    merged_at: '2026-11-04T00:00:00Z',
    customer_slug: 'client-a',
    finished_at: '2026-11-04T00:40:00Z',
  }

  it('parses a well-formed row', () => {
    expect(parseRow(raw)).toEqual({
      releaseId: raw.release_id,
      ssConsoleSha: raw.ss_console_sha,
      createdAt: raw.created_at,
      mergedAt: raw.merged_at,
      seat: 'client-a',
      finishedAt: raw.finished_at,
    })
  })

  it('drops a row with no release id or no build time', () => {
    expect(parseRow({ ...raw, release_id: null })).toBeNull()
    expect(parseRow({ ...raw, created_at: 'yesterday' })).toBeNull()
    expect(parseRow('not a row')).toBeNull()
  })

  it('reads a malformed or offset merge time as missing, never as a guess', () => {
    expect(parseRow({ ...raw, merged_at: '2026-11-04T00:00:00-07:00' })?.mergedAt).toBeNull()
    expect(parseRow({ ...raw, merged_at: 42 })?.mergedAt).toBeNull()
    expect(parseRow({ ...raw, customer_slug: null, finished_at: null })?.seat).toBeNull()
  })
})

describe('clientSeats', () => {
  const entry = (purpose: SeatPurposeEntry['purpose'], provisioned = true): SeatPurposeEntry => ({
    purpose,
    twinOf: null,
    provisioned,
  })

  it('is every provisioned seat whose purpose is client, whatever its ring', () => {
    expect(
      clientSeats({
        twin: { purpose: 'release-twin', twinOf: 'b', provisioned: true },
        b: entry('client'),
        c: entry('client'),
        demo: entry('demo'),
        smd: entry('internal'),
      })
    ).toEqual(['b', 'c'])
  })

  it('an unprovisioned client seat is not waited for (nothing can be released to it)', () => {
    expect(clientSeats({ a: entry('client'), later: entry('client', false) })).toEqual(['a'])
  })

  it('no client seat means no client seats, never a guess', () => {
    expect(clientSeats({})).toEqual([])
    expect(clientSeats({ s: entry('internal'), d: entry('demo') })).toEqual([])
  })

  it('the live inventory gives the client seats and none of the twin, demo or SMD seats', () => {
    const seats = clientSeats()
    expect(seats).toContain('ashton-price')
    for (const seat of ['ap-staging', 'scott', 'pilot-smokeball', 'smd-guide']) {
      expect(seats).not.toContain(seat)
    }
  })
})

describe('display', () => {
  it('shows times in Phoenix (UTC-7) and weeks by their Monday', () => {
    expect(phoenixTime('2026-11-05T04:14:05Z')).toBe('Nov 4, 9:14 PM')
    expect(weekLabel('2026-11-02')).toBe('Nov 2')
  })
})
