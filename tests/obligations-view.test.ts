/**
 * Pure view derivations for the obligation register (ADR 0088).
 *
 * The assertions that matter are the ones about what the UI must NOT say:
 * `delivered` is not a success tone, an undated row is not "on track", and a
 * row with no evidence pointer gets no "closes when" text at all. Each of those
 * is a place where a plausible-looking default would be a claim the data does
 * not support.
 */

import { describe, expect, it } from 'vitest'
import {
  closesWhen,
  countByUrgency,
  decorate,
  distinctCustomers,
  filterObligations,
  groupByCustomer,
  sortObligations,
  STATE_TONE,
  urgencyOf,
  type ObligationRow,
} from '../src/lib/admin/obligations-view'
import type { Obligation } from '../src/lib/db/obligations'

const NOW = new Date('2026-09-17T12:00:00Z')

function row(overrides: Partial<Obligation> = {}): Obligation {
  return {
    obligation_id: 'o1',
    customer_slug: 'ashton-price',
    entity_id: 'e-ap',
    stable_key: 'k',
    kind: 'deliverable',
    what: 'Send the signature copies.',
    origin: 'captured',
    origin_source: 'letter',
    source_kind: 'letter',
    source_ref: 'correspondence/59.md',
    source_quote: 'we will send the signature copies',
    date_quote: null,
    window_start: null,
    window_end: null,
    due_at: null,
    state: 'open',
    park_reason: null,
    resume_token: null,
    evidence_class: null,
    evidence_surface: null,
    evidence_locator: null,
    evidence_last_verified_at: null,
    reconcile_run_id: null,
    supersedes_obligation_id: null,
    recur_rule: null,
    recur_anchor: null,
    parent_obligation_id: null,
    links_json: null,
    created_by_session: null,
    created_at: '2026-09-01T00:00:00Z',
    last_seen_at: '2026-09-01T00:00:00Z',
    closed_at: null,
    disposition: null,
    ...overrides,
  }
}

describe('urgency', () => {
  it('separates overdue, due-soon and scheduled', () => {
    expect(urgencyOf('2026-09-01', NOW)).toBe('overdue')
    expect(urgencyOf('2026-09-20', NOW)).toBe('due-soon')
    expect(urgencyOf('2026-12-01', NOW)).toBe('scheduled')
  })

  it('keeps undated in its own bucket rather than calling it on track', () => {
    // An undated obligation is real work with no clock. Folding it into
    // "scheduled" would show it as on-track, which the row does not support.
    expect(urgencyOf(null, NOW)).toBe('undated')
    expect(urgencyOf('not-a-date', NOW)).toBe('undated')
  })

  it('reports how many days late, and nothing when not late', () => {
    const decorated = decorate([row({ due_at: '2026-09-10' }), row({ due_at: '2026-12-01' })], NOW)
    expect(decorated[0].daysOverdue).toBe(7)
    expect(decorated[1].daysOverdue).toBeNull()
  })
})

describe('state presentation', () => {
  it('does not show delivered as a success', () => {
    // The medchron gap: "we believe it is done" and "a probe confirmed it" are
    // different claims, and a green pill on the first erases the difference.
    expect(STATE_TONE.delivered).toBe('warn')
    expect(STATE_TONE.verified).toBe('good')
  })

  it('shows parked as bad but never as terminal wording', () => {
    expect(STATE_TONE.parked).toBe('bad')
    const decorated = decorate([row({ state: 'parked', park_reason: 'x' })], NOW)
    expect(decorated[0].stateLabel).toBe('Parked')
  })
})

describe('closesWhen', () => {
  it('renders nothing when the row carries no evidence pointer', () => {
    // Falsifier for the no-fabrication rule: a placeholder here would read as
    // a statement about how the obligation gets discharged.
    expect(closesWhen(row())).toBeNull()
  })

  it('says plainly that CI cannot see Smokeball', () => {
    const value = closesWhen(
      row({ evidence_surface: 'smokeball', evidence_class: 'attested', evidence_locator: 'm/1' })
    )
    expect(value).toMatch(/attested/)
    expect(value).toMatch(/CI cannot see Smokeball/)
  })

  it('names the locator for a probeable surface', () => {
    const value = closesWhen(
      row({
        evidence_surface: 'github',
        evidence_class: 'probeable',
        evidence_locator: 'org/repo#7',
      })
    )
    expect(value).toContain('org/repo#7')
  })
})

describe('list derivations', () => {
  const rows = (): ObligationRow[] =>
    decorate(
      [
        row({ obligation_id: 'a', due_at: '2026-09-01', kind: 'deliverable' }),
        row({ obligation_id: 'b', due_at: '2026-09-20', kind: 'renewal' }),
        row({
          obligation_id: 'c',
          due_at: null,
          kind: 'request',
          customer_slug: 'pilot-smokeball',
        }),
      ],
      NOW
    )

  it('sorts the already-late to the top', () => {
    expect(sortObligations(rows()).map((r) => r.obligation_id)).toEqual(['a', 'b', 'c'])
  })

  it('counts every bucket, with a total that is the sum', () => {
    const counts = countByUrgency(rows())
    expect(counts).toEqual({ overdue: 1, dueSoon: 1, scheduled: 0, undated: 1, total: 3 })
    expect(counts.overdue + counts.dueSoon + counts.scheduled + counts.undated).toBe(counts.total)
  })

  it('treats empty and "all" filter values as no filter', () => {
    expect(filterObligations(rows(), { customer: '' })).toHaveLength(3)
    expect(filterObligations(rows(), { customer: 'all' })).toHaveLength(3)
    expect(filterObligations(rows(), { customer: 'ashton-price' })).toHaveLength(2)
    expect(filterObligations(rows(), { kind: 'renewal' })).toHaveLength(1)
    expect(filterObligations(rows(), { urgency: 'overdue' })).toHaveLength(1)
  })

  it('groups by client for the fleet view', () => {
    const grouped = groupByCustomer(sortObligations(rows()))
    expect([...grouped.keys()].sort()).toEqual(['ashton-price', 'pilot-smokeball'])
    expect(grouped.get('ashton-price')).toHaveLength(2)
  })

  it('lists the clients present, for the filter control', () => {
    expect(distinctCustomers(rows())).toEqual(['ashton-price', 'pilot-smokeball'])
  })
})
