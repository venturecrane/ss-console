import { describe, it, expect } from 'vitest'
import { parseScope, parseBusinessHours } from '../src/lib/portal/operator/configure'

describe('parseScope', () => {
  it('parses a full scope blob', () => {
    const s = parseScope({
      email_folders_visible: ['Inbox', 'Matters'],
      email_folders_blind: ['Personal'],
      email_keyword_blocks: ['settlement'],
      domain_blocks: ['opposing.com'],
      matter_blocks: ['M-99'],
    })
    expect(s?.email_folders_visible).toEqual(['Inbox', 'Matters'])
    expect(s?.domain_blocks).toEqual(['opposing.com'])
  })
  it('coerces missing/!array fields to [] and non-objects to null', () => {
    expect(parseScope({})).toEqual({
      email_folders_visible: [],
      email_folders_blind: [],
      email_keyword_blocks: [],
      domain_blocks: [],
      matter_blocks: [],
      inbound_allow_from: [],
      outbound_roster: [],
      admins: [],
      rule_requests_to: [],
      ops_reply_from: [],
    })
    expect(parseScope(null)).toBeNull()
    expect(parseScope('nope')).toBeNull()
  })
  it('drops non-string entries from arrays', () => {
    const s = parseScope({ email_folders_visible: ['Inbox', 3, null, 'Sent'] })
    expect(s?.email_folders_visible).toEqual(['Inbox', 'Sent'])
  })

  it('parses outbound_roster and drops malformed entries (ADR 0075)', () => {
    const s = parseScope({
      outbound_roster: [
        { address: 'jane@gmail.com', class: 'client', note: 'PI client' },
        { address: 'records@radiology.com', class: 'records_vendor' },
        { address: 'x@y.com', class: 'opposing_counsel' }, // bad class → dropped
        { class: 'client' }, // missing address → dropped
        'not-an-object', // dropped
      ],
    })
    expect(s?.outbound_roster).toEqual([
      { address: 'jane@gmail.com', class: 'client', note: 'PI client' },
      { address: 'records@radiology.com', class: 'records_vendor' },
    ])
  })

  it('outbound_roster defaults to [] when absent/non-array', () => {
    expect(parseScope({})?.outbound_roster).toEqual([])
    expect(parseScope({ outbound_roster: 'nope' })?.outbound_roster).toEqual([])
  })
})

describe('parseBusinessHours', () => {
  it('parses a full block', () => {
    const h = parseBusinessHours({
      timezone: 'America/Phoenix',
      days: ['Mon', 'Tue'],
      start: '09:00',
      end: '17:00',
    })
    expect(h).toEqual({
      timezone: 'America/Phoenix',
      days: ['Mon', 'Tue'],
      start: '09:00',
      end: '17:00',
    })
  })
  it('returns null when a required field is missing', () => {
    expect(parseBusinessHours({ days: ['Mon'], start: '09:00', end: '17:00' })).toBeNull()
    expect(parseBusinessHours({ timezone: 'X', start: '09:00' })).toBeNull()
    expect(parseBusinessHours(null)).toBeNull()
  })
})
