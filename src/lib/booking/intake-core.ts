/**
 * Core intake processing — shared between POST /api/intake (standalone) and
 * POST /api/booking/reserve (booking flow).
 *
 * Handles entity dedup, contact creation, optional meeting creation,
 * and context append. Callers decide what notifications to send.
 */

import { findOrCreateEntity } from '../db/entities'
import { createContact } from '../db/contacts'
import { createAssessment } from '../db/assessments'
import { createMeeting } from '../db/meetings'
import { appendContext } from '../db/context'

export interface IntakeInput {
  name: string
  email: string
  businessName: string
  vertical?: string | null
  employeeCount?: number | null
  yearsInBusiness?: number | null
  biggestChallenge?: string | null
  howHeard?: string | null
}

export interface IntakeResult {
  entityId: string
  contactId: string
  /**
   * Non-null only when scheduledAt was provided (booking flow).
   *
   * By construction meetings.id == assessments.id for the same booking —
   * the booking flow creates the meeting first and seeds the legacy
   * assessments row with the same primary key so live FKs
   * (quotes.assessment_id, assessment_schedule.assessment_id) continue to
   * resolve throughout the monitoring window.
   */
  assessmentId: string | null
  /** Same value as `assessmentId` — meetings are the new canonical entity. */
  meetingId: string | null
  /** Whether the entity was freshly created (vs. found by slug dedup). */
  entityCreated: boolean
  /** Formatted intake lines for use in admin notification emails. */
  intakeLines: string[]
}

/**
 * Process an intake submission: find-or-create entity, create contact (if
 * new email), create meeting, and append intake context.
 *
 * Does NOT send any emails — the caller decides what notifications to fire.
 *
 * @param scheduledAt - Optional ISO 8601 UTC string. When provided a meeting
 *   is created with `scheduled_at` set (used by /reserve). When null/undefined
 *   (standalone intake), no meeting is created — the context entry records
 *   interest without a phantom "scheduled" row.
 * @param source - Pipeline identifier for entity creation and context.
 *   Defaults to `'website_booking'` for backward compatibility.
 */
export async function processIntakeSubmission(
  db: D1Database,
  orgId: string,
  input: IntakeInput,
  scheduledAt?: string | null,
  source?: string
): Promise<IntakeResult> {
  const pipeline = source ?? 'website_booking'

  // 1. Find or create entity (dedup by business name slug)
  const { status, entity } = await findOrCreateEntity(db, orgId, {
    name: input.businessName,
    stage: 'prospect',
    source_pipeline: pipeline,
  })

  // 2. Create contact if one with this email doesn't already exist for this org
  const existingContact = await db
    .prepare('SELECT id FROM contacts WHERE org_id = ? AND email = ? LIMIT 1')
    .bind(orgId, input.email)
    .first<{ id: string }>()

  let contactId: string
  if (existingContact) {
    contactId = existingContact.id
  } else {
    const contact = await createContact(db, orgId, entity.id, {
      name: input.name,
      email: input.email,
    })
    contactId = contact.id
  }

  // 3. Create the meeting only when a call is being booked (scheduledAt provided).
  //    Standalone intakes record interest via the context entry below.
  //
  //    Dual-write note: the new booking flow writes to `meetings` as the
  //    canonical row. We also seed a row in the legacy `assessments` table
  //    with the SAME id so existing foreign keys (quotes.assessment_id,
  //    assessment_schedule.assessment_id) keep working during the monitoring
  //    window. When the drop migration lands this dual-write goes away.
  let meetingId: string | null = null
  if (scheduledAt) {
    const meeting = await createMeeting(db, orgId, entity.id, {
      scheduled_at: scheduledAt,
      meeting_type: 'assessment',
    })
    meetingId = meeting.id

    // Seed legacy row with the same primary key for FK compatibility.
    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, scheduled_at, status, created_at)
         VALUES (?, ?, ?, ?, 'scheduled', datetime('now'))`
      )
      .bind(meeting.id, orgId, entity.id, scheduledAt)
      .run()
  }
  const assessmentId = meetingId

  // 4. Append intake context
  const intakeLines: string[] = []
  if (input.vertical) intakeLines.push(`Vertical: ${input.vertical}`)
  if (input.employeeCount) intakeLines.push(`Employees: ${input.employeeCount}`)
  if (input.yearsInBusiness) intakeLines.push(`Years in business: ${input.yearsInBusiness}`)
  if (input.biggestChallenge)
    intakeLines.push(`What they're trying to accomplish: ${input.biggestChallenge}`)
  if (input.howHeard) intakeLines.push(`How they found us: ${input.howHeard}`)

  if (intakeLines.length > 0) {
    await appendContext(db, orgId, {
      entity_id: entity.id,
      type: 'intake',
      content: intakeLines.join('\n'),
      source: pipeline,
      metadata: {
        name: input.name,
        email: input.email,
        vertical: input.vertical,
        employee_count: input.employeeCount,
        years_in_business: input.yearsInBusiness,
        biggest_challenge: input.biggestChallenge,
        how_heard: input.howHeard,
      },
    })
  }

  return {
    entityId: entity.id,
    contactId,
    assessmentId,
    meetingId,
    entityCreated: status === 'created',
    intakeLines,
  }
}
