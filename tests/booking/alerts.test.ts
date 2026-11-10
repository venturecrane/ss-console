/**
 * Booking alerts, run against a migrated D1: every error is recorded, and
 * the admin is emailed once per error kind per window, not once per error.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import { migratedDb, seedOrg } from '../_stubs/behavioural'
import { ORG_ID, SYSTEM_ENTITY_ID } from '../../src/lib/constants'

const sent: Array<{ to: string; subject: string; html: string }> = []
vi.mock('../../src/lib/email/resend', () => ({
  sendEmail: vi.fn(async (_key: string | undefined, payload: (typeof sent)[number]) => {
    sent.push(payload)
    return { success: true, id: 'email-1' }
  }),
}))

const { recordBookingError } = await import('../../src/lib/booking/alerts')
const { BOOKING_CONFIG } = await import('../../src/lib/booking/config')

async function alertRows(db: D1Database): Promise<Array<{ entity_id: string; kind: string }>> {
  const { results } = await db
    .prepare(
      "SELECT entity_id, json_extract(metadata, '$.kind') AS kind FROM context WHERE type = 'alert' AND source = 'booking_system'"
    )
    .all<{ entity_id: string; kind: string }>()
  return results
}

describe('recordBookingError', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_ID)
    await db
      .prepare(
        "INSERT OR IGNORE INTO entities (id, org_id, name, slug, stage) VALUES (?, ?, '_system', '_system', 'signal')"
      )
      .bind(SYSTEM_ENTITY_ID, ORG_ID)
      .run()
    sent.length = 0
  })

  it('records the error on the system entity and emails the admin the first time', async () => {
    await recordBookingError(db, 're_test', 'freebusy_error', { message: 'upstream 503' })
    expect(await alertRows(db)).toEqual([{ entity_id: SYSTEM_ENTITY_ID, kind: 'freebusy_error' }])
    expect(sent).toHaveLength(1)
    expect(sent[0]?.to).toBe(BOOKING_CONFIG.consultant.email)
    expect(sent[0]?.subject).toContain('freebusy_error')
  })

  it('a repeat of the same kind inside the window is recorded but not emailed', async () => {
    await recordBookingError(db, 're_test', 'google_sync_error', { message: 'first' })
    await recordBookingError(db, 're_test', 'google_sync_error', { message: 'second' })
    expect(await alertRows(db)).toHaveLength(2)
    expect(sent).toHaveLength(1)
  })

  it('a different kind is its own window', async () => {
    await recordBookingError(db, 're_test', 'google_sync_error', { message: 'a' })
    await recordBookingError(db, 're_test', 'integration_invalid_grant', { message: 'b' })
    expect(sent.map((s) => s.subject)).toHaveLength(2)
  })

  it('escapes the error text in the email body', async () => {
    await recordBookingError(db, 're_test', 'guest_email_delivery_failed', {
      message: '<script>x</script>',
    })
    expect(sent[0]?.html).not.toContain('<script>x</script>')
    expect(sent[0]?.html).toContain('&lt;script&gt;')
  })
})
