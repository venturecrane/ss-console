/**
 * Tests for the fleet alert-feed view-model (src/lib/admin/fleet-alerts.ts) —
 * admin Operator console §4.2.
 *
 * All pure derivations over the shared cost_anomaly_alerts row shape: severity
 * derivation per source, deep-link target, detail line, filtering, counts, and
 * sort order. No DB — the reader (listOpenAlerts) is the frozen seam.
 */

import { describe, it, expect } from 'vitest'
import type { CostAnomalyAlertRow } from '../src/lib/admin/cost-anomaly'
import {
  alertSeverity,
  alertLink,
  alertDetail,
  severityBadge,
  filterAlerts,
  countBySeverity,
  sortAlerts,
  distinctCustomers,
} from '../src/lib/admin/fleet-alerts'

function row(overrides: Partial<CostAnomalyAlertRow> = {}): CostAnomalyAlertRow {
  return {
    entity_id: 'ent-1',
    customer_slug: 'acme',
    alert_date: '2026-06-08',
    driver: '',
    source: 'cost',
    daily_cents: 5000,
    rolling_avg_cents: 2000,
    ratio_bps: 25000,
    threshold_bps: 15000,
    summary: null,
    details_json: null,
    detected_at: '2026-06-08T12:00:00Z',
    snoozed_until: null,
    acknowledged_at: null,
    acknowledged_by: null,
    ...overrides,
  }
}

const usd = (cents: number) => `$${(cents / 100).toFixed(2)}`

describe('alertSeverity', () => {
  it('audit-integrity drift is always critical', () => {
    expect(alertSeverity(row({ source: 'audit_integrity' }))).toBe('critical')
  })

  it('cost is critical only when over threshold, else warning', () => {
    expect(alertSeverity(row({ source: 'cost', ratio_bps: 25000, threshold_bps: 15000 }))).toBe(
      'critical'
    )
    expect(alertSeverity(row({ source: 'cost', ratio_bps: 10000, threshold_bps: 15000 }))).toBe(
      'warning'
    )
  })

  it('operational sources are warnings', () => {
    expect(alertSeverity(row({ source: 'sentry' }))).toBe('warning')
    expect(alertSeverity(row({ source: 'healthchecks' }))).toBe('warning')
  })
})

describe('alertLink', () => {
  it('cost alerts deep-link to the cost drill-in', () => {
    expect(alertLink(row({ source: 'cost', customer_slug: 'acme' }))).toBe(
      '/admin/operator/costs/acme'
    )
  })

  it('non-cost alerts deep-link to the operator overview', () => {
    expect(alertLink(row({ source: 'sentry', customer_slug: 'beta' }))).toBe('/admin/operator/beta')
  })

  it('encodes the slug', () => {
    expect(alertLink(row({ source: 'sentry', customer_slug: 'a b' }))).toBe('/admin/operator/a%20b')
  })
})

describe('alertDetail', () => {
  it('renders the numeric breach for cost rows', () => {
    const detail = alertDetail(row({ source: 'cost' }), usd)
    expect(detail).toContain('$50.00')
    expect(detail).toContain('$20.00')
  })

  it('uses the authored summary for non-cost rows and never fabricates', () => {
    expect(alertDetail(row({ source: 'sentry', summary: '12 new errors' }), usd)).toBe(
      '12 new errors'
    )
    expect(alertDetail(row({ source: 'sentry', summary: null }), usd)).toBe('(no detail recorded)')
  })
})

describe('filterAlerts', () => {
  const rows = [
    row({ customer_slug: 'acme', source: 'cost', ratio_bps: 25000, threshold_bps: 15000 }),
    row({ customer_slug: 'beta', source: 'sentry' }),
    row({ customer_slug: 'acme', source: 'audit_integrity' }),
  ]

  it('empty / all filters match everything', () => {
    expect(filterAlerts(rows, {})).toHaveLength(3)
    expect(filterAlerts(rows, { source: 'all', severity: 'all', customer: 'all' })).toHaveLength(3)
    expect(filterAlerts(rows, { source: '', customer: null })).toHaveLength(3)
  })

  it('filters by source, customer, and derived severity', () => {
    expect(filterAlerts(rows, { source: 'cost' })).toHaveLength(1)
    expect(filterAlerts(rows, { customer: 'acme' })).toHaveLength(2)
    expect(filterAlerts(rows, { severity: 'critical' })).toHaveLength(2) // cost-over + audit
    expect(filterAlerts(rows, { severity: 'warning' })).toHaveLength(1) // sentry
  })

  it('combines filters (AND)', () => {
    expect(filterAlerts(rows, { customer: 'acme', severity: 'critical' })).toHaveLength(2)
    expect(filterAlerts(rows, { customer: 'beta', severity: 'critical' })).toHaveLength(0)
  })
})

describe('countBySeverity', () => {
  it('counts derived severities', () => {
    const rows = [
      row({ source: 'audit_integrity' }),
      row({ source: 'cost', ratio_bps: 25000, threshold_bps: 15000 }),
      row({ source: 'sentry' }),
    ]
    expect(countBySeverity(rows)).toEqual({ critical: 2, warning: 1, info: 0, total: 3 })
  })
})

describe('sortAlerts', () => {
  it('orders most-severe first, then freshest', () => {
    const warnOld = row({ source: 'sentry', detected_at: '2026-06-01T00:00:00Z' })
    const critOld = row({ source: 'audit_integrity', detected_at: '2026-06-01T00:00:00Z' })
    const critNew = row({ source: 'audit_integrity', detected_at: '2026-06-08T00:00:00Z' })
    const sorted = sortAlerts([warnOld, critOld, critNew])
    expect(sorted[0].detected_at).toBe('2026-06-08T00:00:00Z') // critical, newest
    expect(sorted[1].source).toBe('audit_integrity') // critical, older
    expect(sorted[2].source).toBe('sentry') // warning last
  })

  it('does not mutate the input', () => {
    const input = [row({ source: 'sentry' }), row({ source: 'audit_integrity' })]
    const before = input.map((r) => r.source)
    sortAlerts(input)
    expect(input.map((r) => r.source)).toEqual(before)
  })
})

describe('distinctCustomers', () => {
  it('returns sorted unique slugs', () => {
    expect(
      distinctCustomers([
        row({ customer_slug: 'beta' }),
        row({ customer_slug: 'acme' }),
        row({ customer_slug: 'beta' }),
      ])
    ).toEqual(['acme', 'beta'])
  })
})

describe('severityBadge', () => {
  it('every severity carries a token-based class and a label', () => {
    expect(severityBadge('critical').classes).toContain('--ss-color-error')
    expect(severityBadge('warning').classes).toContain('--ss-color-attention')
    expect(severityBadge('info').label).toBe('Info')
  })
})

describe('the obligation source (ADR 0088)', () => {
  const obligation = (details: Record<string, unknown> | null) =>
    row({
      source: 'obligation',
      driver: 'obligation:o-1:obligation_overdue',
      summary: 'Overdue 11d: send the signature copies',
      details_json: details === null ? null : JSON.stringify(details),
      // An obligation row carries the same 0-sentinels every non-cost writer
      // uses; severity must not fall out of the cost columns.
      daily_cents: 0,
      rolling_avg_cents: 0,
      ratio_bps: 0,
      threshold_bps: 0,
    })

  it('takes severity from what the reconciler decided, not from the cost columns', () => {
    expect(alertSeverity(obligation({ severity: 'critical' }))).toBe('critical')
    expect(alertSeverity(obligation({ severity: 'warning' }))).toBe('warning')
  })

  it('falls back to warning rather than inventing a severity from a bad payload', () => {
    // Falsifier for the reader above: with the zeroed cost columns, a naive
    // ratio >= threshold comparison would read 0 >= 0 and call every
    // obligation critical.
    expect(alertSeverity(obligation(null))).toBe('warning')
    expect(alertSeverity(obligation({ severity: 'nonsense' }))).toBe('warning')
    expect(alertSeverity(row({ source: 'obligation', details_json: '{oops' }))).toBe('warning')
  })

  it('links to the register filtered to that client, not to the seat', () => {
    expect(alertLink(obligation({ severity: 'warning' }))).toBe('/admin/obligations?customer=acme')
  })

  it('is selectable in the source filter', () => {
    // The gap history flagged: a fifth source added after the feed existed, with
    // the filter list silently falling back to showing everything.
    const rows = [obligation({ severity: 'warning' }), row({ source: 'cost' })]
    expect(filterAlerts(rows, { source: 'obligation' })).toHaveLength(1)
    expect(filterAlerts(rows, { source: 'cost' })).toHaveLength(1)
  })
})
