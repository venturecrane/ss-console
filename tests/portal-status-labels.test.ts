import { describe, it, expect } from 'vitest'
import {
  STAMP_VOCABULARY,
  resolveQuoteStampLabel,
  resolveInvoiceStampLabel,
  resolveEngagementStampLabel,
  resolveMilestoneStampLabel,
  type StampLabel,
} from '../src/lib/portal/status'

/**
 * Stamp-label round-trip: every DB enum value must resolve to a label in
 * the closed Plainspoken stamp vocabulary. This locks the StatusPill and
 * the resolver so the pill can never grow its own copy constant or emit
 * a word outside the shared vocabulary. See src/lib/portal/status.ts for
 * the rationale behind each mapping.
 */

const VOCAB = new Set<StampLabel>(STAMP_VOCABULARY)

// Enum source-of-truth arrays — if a DB enum gains a new value, it must
// be added here AND a stamp mapping must be added to status.ts. The round-
// trip assertion then enforces that the new value resolves inside the
// vocabulary.

const QUOTE_STATUSES = ['draft', 'sent', 'accepted', 'declined', 'expired', 'superseded']

const INVOICE_STATUSES = ['draft', 'sent', 'paid', 'overdue', 'void']

const ENGAGEMENT_STATUSES = [
  'scheduled',
  'active',
  'handoff',
  'safety_net',
  'completed',
  'cancelled',
]

const MILESTONE_STATUSES = ['pending', 'in_progress', 'completed', 'skipped']

describe('portal status: stamp vocabulary is closed', () => {
  it('STAMP_VOCABULARY has exactly 12 labels', () => {
    expect(STAMP_VOCABULARY).toHaveLength(12)
  })

  it('STAMP_VOCABULARY entries are all-caps (no lowercase drift)', () => {
    for (const label of STAMP_VOCABULARY) {
      expect(label).toBe(label.toUpperCase())
    }
  })
})

describe('portal status: quote stamp round-trip', () => {
  for (const status of QUOTE_STATUSES) {
    it(`quote.${status} resolves to a vocabulary label`, () => {
      const stamp = resolveQuoteStampLabel(status)
      expect(VOCAB.has(stamp)).toBe(true)
    })
  }

  it('unknown quote status falls back to ARCHIVED (internal-safe default)', () => {
    expect(resolveQuoteStampLabel('nonsense')).toBe('ARCHIVED')
  })

  it('quote.sent stamps as PENDING (action-required register)', () => {
    expect(resolveQuoteStampLabel('sent')).toBe('PENDING')
  })

  it('quote.accepted stamps as ACCEPTED', () => {
    expect(resolveQuoteStampLabel('accepted')).toBe('ACCEPTED')
  })
})

describe('portal status: invoice stamp round-trip', () => {
  for (const status of INVOICE_STATUSES) {
    it(`invoice.${status} resolves to a vocabulary label`, () => {
      const stamp = resolveInvoiceStampLabel(status)
      expect(VOCAB.has(stamp)).toBe(true)
    })
  }

  it('unknown invoice status falls back to ARCHIVED', () => {
    expect(resolveInvoiceStampLabel('nonsense')).toBe('ARCHIVED')
  })

  it('invoice.sent stamps as DUE (awaiting payment)', () => {
    expect(resolveInvoiceStampLabel('sent')).toBe('DUE')
  })

  it('invoice.paid stamps as PAID', () => {
    expect(resolveInvoiceStampLabel('paid')).toBe('PAID')
  })

  it('invoice.overdue stamps as OVERDUE', () => {
    expect(resolveInvoiceStampLabel('overdue')).toBe('OVERDUE')
  })
})

describe('portal status: engagement stamp round-trip', () => {
  for (const status of ENGAGEMENT_STATUSES) {
    it(`engagement.${status} resolves to a vocabulary label`, () => {
      const stamp = resolveEngagementStampLabel(status)
      expect(VOCAB.has(stamp)).toBe(true)
    })
  }

  it('engagement.active stamps as UNDERWAY', () => {
    expect(resolveEngagementStampLabel('active')).toBe('UNDERWAY')
  })

  it('engagement.safety_net stamps as IN PROG (stabilization compressed to vocab)', () => {
    expect(resolveEngagementStampLabel('safety_net')).toBe('IN PROG')
  })
})

describe('portal status: milestone stamp round-trip', () => {
  for (const status of MILESTONE_STATUSES) {
    it(`milestone.${status} resolves to a vocabulary label`, () => {
      const stamp = resolveMilestoneStampLabel(status)
      expect(VOCAB.has(stamp)).toBe(true)
    })
  }

  it('milestone.in_progress stamps as IN PROG', () => {
    expect(resolveMilestoneStampLabel('in_progress')).toBe('IN PROG')
  })

  it('milestone.completed stamps as COMPLETED', () => {
    expect(resolveMilestoneStampLabel('completed')).toBe('COMPLETED')
  })
})
