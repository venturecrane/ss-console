import { beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import { createEntity } from '../../src/lib/db/entities'
import { createContact } from '../../src/lib/db/contacts'
import { appendContext } from '../../src/lib/db/context'
import { createMeetingWithLegacyAssessment } from '../../src/lib/db/meetings'
import { createScheduleStatement } from '../../src/lib/booking/schedule'
import { createMeetingScheduleStatement } from '../../src/lib/booking/meeting-schedule'
import { acquireHold } from '../../src/lib/booking/holds'
import { rollbackFailedBooking } from '../../src/lib/booking/rollback'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ORG_ID = 'org-rollback'

describe('rollbackFailedBooking', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Rollback Org', 'rollback-org')
      .run()
  })

  it('removes all intake artifacts for a failed standalone booking', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Rollback Plumbing',
      stage: 'prospect',
    })
    const contact = await createContact(db, ORG_ID, entity.id, {
      name: 'Jamie Owner',
      email: 'jamie@example.com',
    })
    const context = await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'intake',
      content: 'What they are trying to accomplish: stabilize bookings',
      source: 'website_booking',
      metadata: { biggest_challenge: 'stabilize bookings' },
    })

    const meeting = await createMeetingWithLegacyAssessment(db, ORG_ID, entity.id, {
      scheduled_at: '2026-05-01T17:00:00.000Z',
      meeting_type: 'assessment',
    })

    const { statement: scheduleStmt, id: scheduleId } = createScheduleStatement(db, {
      assessmentId: meeting.id,
      orgId: ORG_ID,
      slotStartUtc: '2026-05-01T17:00:00.000Z',
      slotEndUtc: '2026-05-01T17:30:00.000Z',
      durationMinutes: 30,
      timezone: 'America/Phoenix',
      guestName: 'Jamie Owner',
      guestEmail: 'jamie@example.com',
      manageTokenHash: 'hash-1',
      manageTokenExpiresAt: '2026-05-03T17:30:00.000Z',
    })
    await scheduleStmt.run()

    const { statement: meetingScheduleStmt, id: meetingScheduleId } =
      createMeetingScheduleStatement(db, {
        meetingId: meeting.id,
        orgId: ORG_ID,
        slotStartUtc: '2026-05-01T17:00:00.000Z',
        slotEndUtc: '2026-05-01T17:30:00.000Z',
        durationMinutes: 30,
        timezone: 'America/Phoenix',
        guestName: 'Jamie Owner',
        guestEmail: 'jamie@example.com',
        manageTokenHash: 'hash-2',
        manageTokenExpiresAt: '2026-05-03T17:30:00.000Z',
      })
    await meetingScheduleStmt.run()

    const hold = await acquireHold(db, ORG_ID, '2026-05-01T17:00:00.000Z', 'jamie@example.com')
    expect(hold.acquired).toBe(true)

    await rollbackFailedBooking(db, {
      orgId: ORG_ID,
      holdId: hold.id!,
      scheduleId,
      meetingScheduleId,
      assessmentId: meeting.id,
      meetingId: meeting.id,
      preserveBookingRows: false,
      previousAssessmentScheduledAt: null,
      previousMeetingScheduledAt: null,
      entityId: entity.id,
      entityCreated: true,
      contactId: contact.id,
      contactCreated: true,
      contextId: context.id,
    })

    const counts = await Promise.all([
      db
        .prepare(`SELECT COUNT(*) as c FROM entities WHERE id = ?`)
        .bind(entity.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM contacts WHERE id = ?`)
        .bind(contact.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM context WHERE id = ?`)
        .bind(context.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM assessments WHERE id = ?`)
        .bind(meeting.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM meetings WHERE id = ?`)
        .bind(meeting.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM assessment_schedule WHERE id = ?`)
        .bind(scheduleId)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM meeting_schedule WHERE id = ?`)
        .bind(meetingScheduleId)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM booking_holds WHERE id = ?`)
        .bind(hold.id!)
        .first<{ c: number }>(),
    ])

    for (const row of counts) {
      expect(row?.c).toBe(0)
    }
  })

  it('preserves pre-seeded booking rows and restores scheduled_at on sync failure', async () => {
    const entity = await createEntity(db, ORG_ID, {
      name: 'Existing Prospect',
      stage: 'meetings',
    })
    await createContact(db, ORG_ID, entity.id, {
      name: 'Dana Owner',
      email: 'dana@example.com',
    })
    const createdContact = await createContact(db, ORG_ID, entity.id, {
      name: 'Dana Backup',
      email: 'backup@example.com',
    })
    const context = await appendContext(db, ORG_ID, {
      entity_id: entity.id,
      type: 'intake',
      content: 'Vertical: home services',
      source: 'admin_booking_link',
      metadata: { vertical: 'home_services' },
    })

    const meeting = await createMeetingWithLegacyAssessment(db, ORG_ID, entity.id, {
      scheduled_at: null,
      meeting_type: 'discovery',
    })

    await db
      .prepare('UPDATE assessments SET scheduled_at = ? WHERE id = ?')
      .bind('2026-05-01T17:00:00.000Z', meeting.id)
      .run()
    await db
      .prepare('UPDATE meetings SET scheduled_at = ? WHERE id = ?')
      .bind('2026-05-01T17:00:00.000Z', meeting.id)
      .run()

    const { statement: scheduleStmt, id: scheduleId } = createScheduleStatement(db, {
      assessmentId: meeting.id,
      orgId: ORG_ID,
      slotStartUtc: '2026-05-01T17:00:00.000Z',
      slotEndUtc: '2026-05-01T17:30:00.000Z',
      durationMinutes: 30,
      timezone: 'America/Phoenix',
      guestName: 'Dana Owner',
      guestEmail: 'dana@example.com',
      manageTokenHash: 'hash-3',
      manageTokenExpiresAt: '2026-05-03T17:30:00.000Z',
    })
    await scheduleStmt.run()

    const { statement: meetingScheduleStmt, id: meetingScheduleId } =
      createMeetingScheduleStatement(db, {
        meetingId: meeting.id,
        orgId: ORG_ID,
        slotStartUtc: '2026-05-01T17:00:00.000Z',
        slotEndUtc: '2026-05-01T17:30:00.000Z',
        durationMinutes: 30,
        timezone: 'America/Phoenix',
        guestName: 'Dana Owner',
        guestEmail: 'dana@example.com',
        manageTokenHash: 'hash-4',
        manageTokenExpiresAt: '2026-05-03T17:30:00.000Z',
      })
    await meetingScheduleStmt.run()

    const hold = await acquireHold(db, ORG_ID, '2026-05-01T17:00:00.000Z', 'dana@example.com')
    expect(hold.acquired).toBe(true)

    await rollbackFailedBooking(db, {
      orgId: ORG_ID,
      holdId: hold.id!,
      scheduleId,
      meetingScheduleId,
      assessmentId: meeting.id,
      meetingId: meeting.id,
      preserveBookingRows: true,
      previousAssessmentScheduledAt: null,
      previousMeetingScheduledAt: null,
      entityId: entity.id,
      entityCreated: false,
      contactId: createdContact.id,
      contactCreated: true,
      contextId: context.id,
    })

    const restoredAssessment = await db
      .prepare('SELECT scheduled_at FROM assessments WHERE id = ?')
      .bind(meeting.id)
      .first<{ scheduled_at: string | null }>()
    const restoredMeeting = await db
      .prepare('SELECT scheduled_at FROM meetings WHERE id = ?')
      .bind(meeting.id)
      .first<{ scheduled_at: string | null }>()

    expect(restoredAssessment?.scheduled_at).toBeNull()
    expect(restoredMeeting?.scheduled_at).toBeNull()

    const counts = await Promise.all([
      db
        .prepare(`SELECT COUNT(*) as c FROM contacts WHERE id = ?`)
        .bind(createdContact.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM contacts WHERE email = ?`)
        .bind('dana@example.com')
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM context WHERE id = ?`)
        .bind(context.id)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM assessment_schedule WHERE id = ?`)
        .bind(scheduleId)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM meeting_schedule WHERE id = ?`)
        .bind(meetingScheduleId)
        .first<{ c: number }>(),
      db
        .prepare(`SELECT COUNT(*) as c FROM booking_holds WHERE id = ?`)
        .bind(hold.id!)
        .first<{ c: number }>(),
    ])

    expect(counts[0]?.c).toBe(0)
    expect(counts[1]?.c).toBe(1)
    expect(counts[2]?.c).toBe(0)
    expect(counts[3]?.c).toBe(0)
    expect(counts[4]?.c).toBe(0)
    expect(counts[5]?.c).toBe(0)
  })
})
