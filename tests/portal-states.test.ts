import { describe, it, expect } from 'vitest'
import {
  resolveInvoiceState,
  resolveProposalState,
  resolveContactLink,
} from '../src/lib/portal/states'

describe('resolveInvoiceState', () => {
  it('returns paid when invoice.paid_at is set, regardless of query hint', () => {
    const params = new URLSearchParams('?state=declined')
    const surface = resolveInvoiceState({ paid_at: '2026-04-10T00:00:00Z' }, params, 'Scott')
    expect(surface.state).toBe('paid')
  })

  it('returns declined with Scott-named next step on ?state=declined', () => {
    const surface = resolveInvoiceState(
      { paid_at: null },
      new URLSearchParams('?state=declined'),
      'Scott'
    )
    expect(surface.state).toBe('declined')
    expect(surface.next.toLowerCase()).toContain('scott')
    expect(surface.next.toLowerCase()).toContain('try again')
  })

  it('returns card_expired on ?state=card_expired', () => {
    const surface = resolveInvoiceState(
      { paid_at: null },
      new URLSearchParams('?state=card_expired'),
      'Scott'
    )
    expect(surface.state).toBe('card_expired')
    expect(surface.next.toLowerCase()).toContain('expired')
  })

  it('returns expired on ?state=expired with a refreshed-link call to action', () => {
    const surface = resolveInvoiceState(
      { paid_at: null },
      new URLSearchParams('?state=expired'),
      'Scott'
    )
    expect(surface.state).toBe('expired')
    expect(surface.next.toLowerCase()).toContain('refreshed link')
  })

  it('returns default when no state hint and not paid', () => {
    const surface = resolveInvoiceState({ paid_at: null }, null, 'Scott')
    expect(surface.state).toBe('default')
  })

  it('omits the text-first-name CTA when firstName is null', () => {
    const declined = resolveInvoiceState(
      { paid_at: null },
      new URLSearchParams('?state=declined'),
      null
    )
    expect(declined.state).toBe('declined')
    expect(declined.next.toLowerCase()).not.toContain('text ')

    const expired = resolveInvoiceState(
      { paid_at: null },
      new URLSearchParams('?state=expired'),
      null
    )
    expect(expired.state).toBe('expired')
    expect(expired.next.toLowerCase()).not.toContain('text ')
  })

  it('paid next-step never claims a receipt is attached', () => {
    const surface = resolveInvoiceState({ paid_at: '2026-04-10T00:00:00Z' }, null, 'Scott')
    expect(surface.next.toLowerCase()).not.toContain('receipt attached')
  })

  it('never surfaces raw provider text — copy is portal voice', () => {
    const surface = resolveInvoiceState(
      { paid_at: null },
      new URLSearchParams('?state=declined'),
      'Scott'
    )
    expect(surface.next.toLowerCase()).not.toContain('stripe')
    expect(surface.next.toLowerCase()).not.toContain('error code')
  })
})

describe('resolveProposalState', () => {
  it('returns signed when status=accepted', () => {
    const surface = resolveProposalState(
      { status: 'accepted', accepted_at: '2026-04-10T00:00:00Z', expires_at: null },
      null,
      'Scott'
    )
    expect(surface.state).toBe('signed')
  })

  it('uses engagement-specific nextStepText for signed copy when provided', () => {
    const surface = resolveProposalState(
      { status: 'accepted', accepted_at: '2026-04-10T00:00:00Z', expires_at: null },
      null,
      'Scott',
      'Kickoff next: Migrate the intake workflow.'
    )
    expect(surface.next).toBe('Kickoff next: Migrate the intake workflow.')
  })

  it('falls back to generic kickoff when nextStepText is missing', () => {
    const surface = resolveProposalState(
      { status: 'accepted', accepted_at: '2026-04-10T00:00:00Z', expires_at: null },
      null,
      'Scott'
    )
    expect(surface.next.toLowerCase()).toContain('kickoff')
  })

  it('returns declined with revision call-out', () => {
    const surface = resolveProposalState(
      { status: 'declined', accepted_at: null, expires_at: null },
      null,
      'Scott'
    )
    expect(surface.state).toBe('declined')
    expect(surface.next.toLowerCase()).toContain('revision')
  })

  it('returns superseded when status=superseded', () => {
    const surface = resolveProposalState(
      { status: 'superseded', accepted_at: null, expires_at: null },
      null,
      'Scott'
    )
    expect(surface.state).toBe('superseded')
    expect(surface.next.toLowerCase()).toContain('revised version')
  })

  it('returns expired when server-side expires_at is past', () => {
    const past = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
    const surface = resolveProposalState(
      { status: 'sent', accepted_at: null, expires_at: past },
      null,
      'Scott'
    )
    expect(surface.state).toBe('expired')
  })

  it('returns default for an active sent quote', () => {
    const future = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString()
    const surface = resolveProposalState(
      { status: 'sent', accepted_at: null, expires_at: future },
      null,
      'Scott'
    )
    expect(surface.state).toBe('default')
  })

  it('drops the text-first-name CTA when firstName is null', () => {
    const declined = resolveProposalState(
      { status: 'declined', accepted_at: null, expires_at: null },
      null,
      null
    )
    expect(declined.state).toBe('declined')
    expect(declined.next.toLowerCase()).not.toContain('text ')

    const past = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
    const expired = resolveProposalState(
      { status: 'sent', accepted_at: null, expires_at: past },
      null,
      null
    )
    expect(expired.state).toBe('expired')
    expect(expired.next.toLowerCase()).not.toContain('text ')
  })
})

describe('resolveContactLink', () => {
  it('returns both sms and tel hrefs when phone is present', () => {
    const link = resolveContactLink('+1 (480) 555-0100', 'iPhone')
    expect(link.smsHref).toBe('sms:+14805550100')
    expect(link.telHref).toBe('tel:+14805550100')
    expect(link.isMobile).toBe(true)
  })

  it('detects desktop UA as non-mobile', () => {
    const link = resolveContactLink(
      '+14805550100',
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit'
    )
    expect(link.isMobile).toBe(false)
  })

  it('returns all nulls when phone is absent', () => {
    const link = resolveContactLink(null, 'iPhone')
    expect(link.smsHref).toBeNull()
    expect(link.telHref).toBeNull()
  })

  it('strips non-digit characters except leading plus', () => {
    const link = resolveContactLink('(480) 555-0100 x123', null)
    expect(link.smsHref).toBe('sms:4805550100123')
  })
})
