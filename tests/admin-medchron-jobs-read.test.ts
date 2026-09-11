/**
 * Chronology-package jobs read (routine 11, ss#2614). The four properties the
 * admin page rests on: a dark read path performs no read, a malformed row is
 * dropped rather than invented, an unreachable seat reports unreachable, and
 * the month's totals count delivered jobs only.
 */

import { describe, it, expect } from 'vitest'
import {
  allowanceFromPersonas,
  loadMedchronJobsView,
  monthTotals,
  parseJobRow,
} from '../src/lib/admin/medchron-jobs-read'
import { cycleWindow } from '../src/lib/admin/cycle-window'

/** The calendar-month window for a `YYYY-MM`, which is what these cases mean:
 * an unanchored seat meters exactly as it always did. The cycle cases live in
 * tests/cycle-window.test.ts against the shared fixture. */
const monthOf = (ym: string) => cycleWindow(`${ym}-15T12:00:00.000Z`)

const ACTOR = { actor: 'captain@smd.services', actorRole: 'admin' }
const noopAudit = { record: async () => {} }

function row(over: Record<string, unknown> = {}) {
  return {
    id: '01J',
    created_at: '2026-08-29T10:00:00.000Z',
    updated_at: '2026-08-29T14:00:00.000Z',
    state: 'delivered',
    matter_number: '2026-PI-102',
    documents: 40,
    pages: 900,
    cents: 4100,
    reason: null,
    folder_id: 'f-1',
    ...over,
  }
}

describe('parseJobRow', () => {
  it('parses a well-formed ledger row', () => {
    const r = parseJobRow(row())
    expect(r?.state).toBe('delivered')
    expect(r?.documents).toBe(40)
    expect(r?.folderId).toBe('f-1')
  })

  it('drops a row without an id or with an unknown state, and zeroes junk counts', () => {
    expect(parseJobRow(row({ id: '' }))).toBeNull()
    expect(parseJobRow(row({ state: 'done' }))).toBeNull()
    expect(parseJobRow('nope')).toBeNull()
    expect(parseJobRow(row({ pages: 'many', cents: -5 }))?.pages).toBe(0)
    expect(parseJobRow(row({ pages: 'many', cents: -5 }))?.cents).toBe(0)
  })
})

describe('monthTotals', () => {
  it('counts delivered jobs only, in the month asked for', () => {
    const jobs = [
      parseJobRow(row())!,
      parseJobRow(row({ id: '02', state: 'held', reason: 'seat paused', documents: 99 }))!,
      parseJobRow(row({ id: '03', created_at: '2026-07-02T00:00:00.000Z', documents: 7 }))!,
    ]
    const m = monthTotals(jobs, monthOf('2026-08'))
    expect(m).toEqual({
      month: '2026-08',
      cycleStart: '2026-08-01T00:00:00.000Z',
      cycleEnd: '2026-09-01T00:00:00.000Z',
      cycleAnchored: false,
      jobs: 2,
      delivered: 1,
      held: 1,
      documents: 40,
      pages: 900,
      cents: 4100,
      // The held job also recorded cents, so it debits the month even though
      // it delivered nothing.
      pagesUsed: 1800,
      centsUsed: 8200,
    })
  })

  // 2026-09-09: the tiles read pagesUsed/centsUsed, which follow the BROKER's
  // debit rule. Counting delivered jobs only would let a run that read 3,000
  // pages and spent real money show as zero on the page a person checks the
  // month against, because it held after the money had moved.
  it('counts pages and spend for every job that recorded a cost, not delivered ones only', () => {
    const jobs = [
      parseJobRow(row({ id: '01', pages: 100, cents: 500 }))!,
      parseJobRow(
        row({ id: '02', state: 'held', pages: 3000, cents: 2000, reason: 'per_job_cap_usd: ...' })
      )!,
      parseJobRow(row({ id: '03', state: 'failed', pages: 40, cents: 90 }))!,
      parseJobRow(
        row({ id: '04', state: 'held', pages: 9000, cents: 0, reason: 'the matter is too big' })
      )!,
    ]
    const m = monthTotals(jobs, monthOf('2026-08'))
    expect(m.pagesUsed, 'a held or failed job that spent money still debits the month').toBe(3140)
    expect(m.centsUsed).toBe(2590)
    // A hold at zero cents read nothing and spent nothing: not a debit.
    expect(m.pagesUsed).not.toBe(12140)
    // The delivered-only figures are still what actually reached the firm.
    expect(m.pages).toBe(100)
    expect(m.cents).toBe(500)
  })

  // The keying half of the debit rule, shared with the broker. A month-of-
  // charge key would live in a ledger column that is not in the broker's
  // PROJECTION, and PROJECTION's shape is pinned by the overlay this release,
  // so this surface could never see it: the seat would debit one month and the
  // console would show the other. `createdAt` is a column both surfaces have.
  it('counts a job against the month it was created in, not the month it finished', () => {
    const spanning = parseJobRow(
      row({
        id: '01',
        created_at: '2026-08-31T23:50:00.000Z',
        updated_at: '2026-09-01T04:20:00.000Z',
        pages: 420,
        cents: 1500,
      })
    )!
    expect(monthTotals([spanning], monthOf('2026-08')).pagesUsed).toBe(420)
    expect(monthTotals([spanning], monthOf('2026-08')).centsUsed).toBe(1500)
    expect(monthTotals([spanning], monthOf('2026-09')).pagesUsed).toBe(0)
    expect(monthTotals([spanning], monthOf('2026-09')).centsUsed).toBe(0)
  })
})

describe('allowanceFromPersonas', () => {
  const personas = (settings: unknown, over: Record<string, unknown> = {}) =>
    JSON.stringify([
      { slug: 'other', skills: [{ name: 'client-verification-tracker' }] },
      { slug: 'operator', skills: [{ name: 'medical-chronology-maintainer', settings, ...over }] },
    ])

  it('reads the authored page allowance', () => {
    expect(
      allowanceFromPersonas(personas({ chronology_package_page_allowance_per_month: 15000 }))
    ).toBe(15000)
  })

  it('fails closed rather than inventing a denominator', () => {
    // The pre-rename key is NOT a fallback: metering 2000 documents as 2000
    // pages would show a firm a denominator it never authored.
    expect(
      allowanceFromPersonas(personas({ chronology_package_document_allowance_per_month: 2000 }))
    ).toBeNull()
    expect(allowanceFromPersonas(personas({}))).toBeNull()
    expect(allowanceFromPersonas(personas(null))).toBeNull()
    expect(
      allowanceFromPersonas(
        personas({ chronology_package_page_allowance_per_month: 15000 }, { enabled: false })
      )
    ).toBeNull()
    expect(
      allowanceFromPersonas(personas({ chronology_package_page_allowance_per_month: '15000' }))
    ).toBeNull()
    expect(allowanceFromPersonas('not json')).toBeNull()
    expect(allowanceFromPersonas(undefined)).toBeNull()
    expect(allowanceFromPersonas('{}')).toBeNull()
  })
})

describe('loadMedchronJobsView', () => {
  it('returns not_enabled without reading when the seam is dark', async () => {
    let reads = 0
    const transport = {
      read: async () => {
        reads += 1
        return { data: { entries: [row()] } }
      },
    }
    const result = await loadMedchronJobsView(
      { transport, audit: noopAudit },
      'example',
      ACTOR,
      false
    )
    expect(result).toEqual({ status: 'not_enabled' })
    expect(reads).toBe(0)
  })

  it('reads the kind once, drops malformed rows, sorts newest first', async () => {
    const transport = {
      read: async (_slug: string, query: { kind: string }) => {
        expect(query.kind).toBe('medchron_jobs')
        return {
          data: {
            entries: [
              row({ id: 'old', created_at: '2026-08-01T00:00:00.000Z' }),
              { id: 'broken' },
              row({ id: 'new', created_at: '2026-08-29T00:00:00.000Z', state: 'running' }),
            ],
          },
        }
      },
    }
    const result = await loadMedchronJobsView(
      { transport, audit: noopAudit },
      'example',
      ACTOR,
      true,
      monthOf('2026-08')
    )
    expect(result.status).toBe('items')
    if (result.status !== 'items') return
    expect(result.jobs.map((j) => j.id)).toEqual(['new', 'old'])
    expect(result.month.jobs).toBe(2)
    expect(result.month.delivered).toBe(1)
  })

  it('reports unreachable rather than an empty table when the seat cannot be read', async () => {
    const transport = {
      read: async () => {
        throw new Error('boom')
      },
    }
    const result = await loadMedchronJobsView(
      { transport, audit: noopAudit },
      'example',
      ACTOR,
      true
    )
    expect(result.status).toBe('unreachable')
  })

  it('is empty when the ledger has no rows', async () => {
    const transport = { read: async () => ({ data: { entries: [] } }) }
    const result = await loadMedchronJobsView(
      { transport, audit: noopAudit },
      'example',
      ACTOR,
      true
    )
    expect(result).toEqual({ status: 'empty' })
  })
})
