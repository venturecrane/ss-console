import { describe, it, expect } from 'vitest'
import {
  computeBillingRollup,
  engagementToServiceRow,
  clientStageBadge,
} from '../src/lib/admin/client-hub'
import type { EntityStage } from '../src/lib/db/entities'
import type { Invoice } from '../src/lib/db/invoices'
import type { Engagement } from '../src/lib/db/engagements'
import type { Quote } from '../src/lib/db/quotes'

function inv(amount: number, status: string): Invoice {
  return {
    id: `i-${amount}-${status}`,
    org_id: 'o',
    engagement_id: null,
    entity_id: 'e',
    type: 'deposit',
    amount,
    description: null,
    status,
    stripe_invoice_id: null,
    stripe_hosted_url: null,
    due_date: null,
    sent_at: null,
    paid_at: null,
    payment_method: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

describe('computeBillingRollup', () => {
  it('counts sent/paid/overdue toward invoiced; excludes draft and void', () => {
    const r = computeBillingRollup([
      inv(5000, 'paid'),
      inv(3000, 'sent'),
      inv(1000, 'overdue'),
      inv(9999, 'draft'),
      inv(8888, 'void'),
    ])
    expect(r.invoiced).toBe(9000)
    expect(r.paid).toBe(5000)
    expect(r.outstanding).toBe(4000) // sent + overdue
    expect(r.hasOverdue).toBe(true)
  })

  it('is all-zero with no countable invoices', () => {
    const r = computeBillingRollup([inv(100, 'draft')])
    expect(r).toEqual({ invoiced: 0, paid: 0, outstanding: 0, hasOverdue: false })
  })
})

describe('engagementToServiceRow', () => {
  const baseEng = {
    id: 'eng-1',
    quote_id: 'q-1',
    scope_summary: 'Operations cleanup engagement',
    status: 'safety_net',
    safety_net_end: '2026-06-16T00:00:00Z',
    handoff_date: '2026-06-02T00:00:00Z',
    estimated_end: null,
    start_date: '2026-05-01T00:00:00Z',
  } as unknown as Engagement

  const quote = { id: 'q-1', total_price: 10500 } as unknown as Quote

  it('draws value from the matching quote and labels the status', () => {
    const row = engagementToServiceRow(baseEng, [quote])
    expect(row.title).toBe('Operations cleanup engagement')
    expect(row.value).toBe('$10,500')
    expect(row.statusLabel).toBe('Safety Net')
    expect(row.tone).toBe('attention')
    expect(row.keyDateKind).toBe('Safety-net ends')
    expect(row.keyDateIso).toBe('2026-06-16T00:00:00Z')
  })

  it('returns null value when no priced quote matches (no fabrication)', () => {
    const row = engagementToServiceRow(baseEng, [])
    expect(row.value).toBeNull()
  })

  it('falls back to a generic title when scope_summary is empty', () => {
    const row = engagementToServiceRow({ ...baseEng, scope_summary: null }, [quote])
    expect(row.title).toBe('Consulting engagement')
  })
})

describe('clientStageBadge (Clients surface, ADR 0077)', () => {
  it('maps meetings to Assessment and engaged to Client', () => {
    expect(clientStageBadge('meetings').label).toBe('Assessment')
    expect(clientStageBadge('engaged').label).toBe('Client')
  })

  it('marks engaged/delivered/ongoing as signed and everything else as not', () => {
    const signed: EntityStage[] = ['engaged', 'delivered', 'ongoing']
    const unsigned: EntityStage[] = ['signal', 'prospect', 'meetings', 'proposing', 'lost']
    for (const s of signed) expect(clientStageBadge(s).signed, s).toBe(true)
    for (const s of unsigned) expect(clientStageBadge(s).signed, s).toBe(false)
  })

  it('gives every stage a non-empty label (total over the enum)', () => {
    const all: EntityStage[] = [
      'signal',
      'prospect',
      'meetings',
      'proposing',
      'engaged',
      'delivered',
      'ongoing',
      'lost',
    ]
    for (const s of all) expect(clientStageBadge(s).label.length, s).toBeGreaterThan(0)
  })
})
