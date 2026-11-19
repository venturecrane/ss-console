/**
 * Merge to seat, the data side: rows are parsed (never cast), timestamps in
 * every form the fleet writers store read the same, anything else reads as
 * missing, a failed query is an error the page states, and the seat groups
 * come from the authored seat purposes.
 */

import { describe, it, expect } from 'vitest'
import {
  clientSeats,
  fetchFleetHistory,
  parseHold,
  parseRelease,
  parseRollout,
  parseUtc,
  phoenixTime,
  seatGroups,
  weekLabel,
} from '../src/lib/admin/merge-to-seat'
import type { SeatPurposeEntry } from '../src/lib/operator/seat-purposes.generated'

const AT = Date.UTC(2026, 10, 4, 0, 10, 0)

describe('parseUtc', () => {
  it('reads every form the fleet writers store as the same instant', () => {
    for (const form of [
      '2026-11-04T00:10:00Z',
      '2026-11-04 00:10:00',
      '2026-11-04T00:10:00.000Z',
      '2026-11-04T00:10:00+00:00',
    ]) {
      expect(parseUtc(form)).toBe(AT)
    }
  })

  it('reads a zoneless T form, an offset, a bad date or a non-string as missing', () => {
    for (const bad of [
      '2026-11-04T00:10:00',
      '2026-11-04T00:10:00-07:00',
      '2026-02-30T00:00:00Z',
      'yesterday',
      42,
      null,
    ]) {
      expect(parseUtc(bad)).toBeNull()
    }
  })
})

describe('row parsers', () => {
  const release = {
    release_id: 'img-202611040010-aaaaaaaa',
    kind: 'image',
    ss_console_sha: 'a'.repeat(40),
    notes: 'merge aaaaaaaa (CI)',
    created_at: '2026-11-04T00:10:00Z',
    merged_at: '2026-11-04 00:00:00',
  }

  it('parses a release, keeping a missing merge time missing', () => {
    expect(parseRelease(release)).toEqual({
      releaseId: release.release_id,
      kind: 'image',
      sha: release.ss_console_sha,
      notes: release.notes,
      createdAtMs: AT,
      mergedAtMs: AT - 10 * 60_000,
    })
    expect(parseRelease({ ...release, merged_at: null })?.mergedAtMs).toBeNull()
  })

  it('drops a release with no id, an unknown kind or no build time', () => {
    expect(parseRelease({ ...release, release_id: '' })).toBeNull()
    expect(parseRelease({ ...release, kind: 'firmware' })).toBeNull()
    expect(parseRelease({ ...release, created_at: 'soon' })).toBeNull()
    expect(parseRelease('not a row')).toBeNull()
  })

  it('parses a rollout and a hold, dropping one that cannot be placed', () => {
    const rollout = {
      release_id: 'img-1',
      customer_slug: 'client-a',
      state: 'waiting_idle',
      started_at: '2026-11-04T00:10:00Z',
      finished_at: null,
    }
    expect(parseRollout(rollout)).toEqual({
      releaseId: 'img-1',
      seat: 'client-a',
      state: 'waiting_idle',
      startedAtMs: AT,
      finishedAtMs: null,
    })
    expect(parseRollout({ ...rollout, started_at: null })).toBeNull()
    expect(parseHold({ customer_slug: 'client-a', held_at: '2026-11-04T00:10:00Z' })).toEqual({
      seat: 'client-a',
      heldAtMs: AT,
    })
    expect(parseHold({ customer_slug: 'client-a', held_at: 'now' })).toBeNull()
  })
})

describe('fetchFleetHistory', () => {
  function fakeDb(fail: string | null): D1Database {
    const rows: Record<string, unknown[]> = {
      fleet_releases: [{ release_id: 'img-1', kind: 'image', created_at: '2026-11-04T00:10:00Z' }],
      fleet_rollouts: [],
      fleet_holds: [{ customer_slug: 'client-a', held_at: '2026-11-04T00:10:00Z' }],
    }
    const table = (sql: string) =>
      sql.startsWith('SELECT o.')
        ? 'fleet_rollouts'
        : sql.includes('fleet_holds')
          ? 'fleet_holds'
          : 'fleet_releases'
    const stmt = (sql: string) => {
      const all = async () => {
        if (table(sql) === fail) throw new Error('D1_ERROR: no such table')
        return { results: rows[table(sql)] }
      }
      return { bind: () => ({ all }), all }
    }
    return { prepare: stmt } as unknown as D1Database
  }

  it('reads all three tables', async () => {
    const { history, error } = await fetchFleetHistory(fakeDb(null), 10)
    expect(error).toBeNull()
    expect(history.releases).toHaveLength(1)
    expect(history.holds).toEqual([{ seat: 'client-a', heldAtMs: AT }])
  })

  it('a failed query is an error naming the table, with nothing measured', async () => {
    const { history, error } = await fetchFleetHistory(fakeDb('fleet_holds'), 10)
    expect(error).toContain('fleet_holds query failed')
    expect(history).toEqual({ releases: [], rollouts: [], holds: [] })
  })
})

describe('seat groups', () => {
  const entry = (
    purpose: SeatPurposeEntry['purpose'],
    provisioned = true,
    twinOf: string | null = null
  ): SeatPurposeEntry => ({ purpose, twinOf, provisioned })

  const purposes = {
    'b-staging': entry('release-twin', true, 'b'),
    b: entry('client'),
    c: entry('client'),
    later: entry('client', false),
    demo: entry('demo'),
    smd: entry('internal'),
  }

  it('client seats are provisioned seats whose purpose is client; an unprovisioned one is not waited for', () => {
    expect(clientSeats(purposes)).toEqual(['b', 'c'])
    expect(clientSeats({})).toEqual([])
  })

  it('splits client seats by whether a release twin gates them', () => {
    expect(seatGroups(purposes)).toEqual({
      twinned: ['b'],
      untwinned: ['c'],
      twins: { b: 'b-staging' },
    })
  })

  it('the live inventory: ashton-price is twinned by ap-staging; no twin, demo or SMD seat is a client', () => {
    const groups = seatGroups()
    expect(groups.twins['ashton-price']).toBe('ap-staging')
    const all = [...groups.twinned, ...groups.untwinned]
    for (const seat of ['ap-staging', 'pilot-smokeball', 'smd-guide']) {
      expect(all).not.toContain(seat)
    }
  })
})

describe('display', () => {
  it('shows times in Phoenix (UTC-7) and weeks by their Monday', () => {
    expect(phoenixTime(Date.UTC(2026, 10, 5, 4, 14, 5))).toBe('Nov 4, 9:14 PM')
    expect(weekLabel('2026-11-02')).toBe('Nov 2')
  })
})
