import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Booking alerts module — structural tests + export verification.
 *
 * Full behavioral verification (D1 queries, email sending) requires
 * integration test infrastructure. These tests verify the module structure,
 * rate-limiting logic shape, and correct wiring.
 */

const source = () => readFileSync(resolve('src/lib/booking/alerts.ts'), 'utf-8')

describe('booking alerts: exports', () => {
  it('exports recordBookingError as async function', () => {
    expect(source()).toContain('export async function recordBookingError')
  })

  it('exports BookingAlertKind type with all three error kinds', () => {
    const code = source()
    expect(code).toContain("'google_sync_error'")
    expect(code).toContain("'integration_invalid_grant'")
    expect(code).toContain("'freebusy_error'")
  })

  it('exports BookingAlertDetails interface', () => {
    expect(source()).toContain('export interface BookingAlertDetails')
  })
})

describe('booking alerts: context row', () => {
  it('writes context row with type alert via appendContextRaw', () => {
    const code = source()
    expect(code).toContain('appendContextRaw')
    expect(code).toContain("type: 'alert'")
  })

  it('includes kind in context metadata for rate-limit queries', () => {
    const code = source()
    expect(code).toContain('kind')
    expect(code).toContain("source: 'booking_system'")
  })

  it('uses SYSTEM_ENTITY_ID as fallback when no entityId provided', () => {
    expect(source()).toContain('SYSTEM_ENTITY_ID')
  })

  it('uses ORG_ID constant for the org_id column', () => {
    expect(source()).toContain('ORG_ID')
  })
})

describe('booking alerts: rate limiting', () => {
  it('queries context table for recent alerts of the same kind', () => {
    const code = source()
    expect(code).toContain("type = 'alert'")
    expect(code).toContain("json_extract(metadata, '$.kind')")
  })

  it('uses a 30-minute window for rate limiting', () => {
    expect(source()).toContain('RATE_LIMIT_MINUTES = 30')
  })

  it('only sends email when count is 1 (first in window)', () => {
    expect(source()).toContain('count !== 1')
  })

  it('uses parameterized query for the kind value', () => {
    const code = source()
    // The kind should be bound via .bind(), not interpolated
    expect(code).toContain('.bind(kind)')
  })
})

describe('booking alerts: email', () => {
  it('sends email via the shared sendEmail helper', () => {
    expect(source()).toContain('sendEmail')
  })

  it('sends to consultant email from BOOKING_CONFIG', () => {
    expect(source()).toContain('BOOKING_CONFIG.consultant.email')
  })

  it('includes kind in subject line', () => {
    expect(source()).toContain('Booking system alert: ${kind}')
  })

  it('logs email failures without throwing', () => {
    const code = source()
    expect(code).toContain('emailResult.success')
    expect(code).toContain('console.error')
  })

  it('includes actionable descriptions for each alert kind', () => {
    const code = source()
    expect(code).toContain('google_sync_error')
    expect(code).toContain('integration_invalid_grant')
    expect(code).toContain('freebusy_error')
    // Each kind should have title, meaning, and action
    expect(code).toContain('What happened')
    expect(code).toContain('What to do')
  })

  it('escapes HTML in user-supplied values', () => {
    expect(source()).toContain('escapeHtml')
  })
})

describe('booking alerts: alert descriptions', () => {
  it('has descriptions for all three alert kinds', () => {
    const code = source()
    // Verify each kind has a title, meaning, and action entry
    expect(code).toContain('google_sync_error: {')
    expect(code).toContain('integration_invalid_grant: {')
    expect(code).toContain('freebusy_error: {')
  })

  it('google_sync_error mentions calendar out of sync', () => {
    expect(source()).toContain('calendar is out of sync')
  })

  it('integration_invalid_grant mentions re-authorize', () => {
    expect(source()).toContain('re-authorize')
  })

  it('freebusy_error mentions double-bookings risk', () => {
    expect(source()).toContain('double-bookings')
  })
})
