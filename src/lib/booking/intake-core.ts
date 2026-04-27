/**
 * Core intake processing — shared between POST /api/intake (standalone) and
 * POST /api/booking/reserve (booking flow).
 *
 * Handles entity dedup, contact creation, optional meeting creation,
 * and context append. Callers decide what notifications to send.
 */

import { findOrCreateEntity, getEntity } from '../db/entities'
import { createContact } from '../db/contacts'
import { updateAssessment, getAssessment } from '../db/assessments'
import { createMeetingWithLegacyAssessment, ensureMeetingForAssessment } from '../db/meetings'
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

/**
 * Optional pre-seeded identifiers produced by the admin "Send booking link"
 * flow (#467). When present, the intake bypasses entity dedup and reuses the
 * pre-created `scheduled` assessment row instead of creating a new one.
 */
export interface PreSeededIntake {
  entityId: string
  assessmentId: string
  meetingType?: string | null
  /**
   * When the admin identified a primary contact at send-link time, pass the
   * id here so we can reuse it rather than creating a duplicate contact when
   * the guest books with the same email.
   */
  contactId?: string | null
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
  /** Whether this intake created a new contact row. */
  contactCreated: boolean
  /** Context row id for compensating rollback on booking failures. */
  contextId: string | null
  /** Whether this intake created a new legacy assessment row. */
  assessmentCreated: boolean
  /** Whether this intake created a new canonical meeting row. */
  meetingCreated: boolean
  /** Previous legacy assessment scheduled_at before any booking mutation. */
  previousAssessmentScheduledAt: string | null
  /** Previous canonical meeting scheduled_at before any booking mutation. */
  previousMeetingScheduledAt: string | null
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
  source?: string,
  preSeeded?: PreSeededIntake | null
): Promise<IntakeResult> {
  const pipeline = source ?? 'website_booking'

  // 1. Entity resolution.
  //
  // When the booking came from a signed admin link (`preSeeded.entityId`),
  // we anchor to that pre-existing entity rather than running slug dedup —
  // the admin explicitly chose the target and we must not fan out to a
  // different row based on the guest's typed business name.
  let entityId: string
  let entityCreated = false
  if (preSeeded?.entityId) {
    const entity = await getEntity(db, orgId, preSeeded.entityId)
    if (!entity) {
      throw new Error(
        `Pre-seeded entity not found: ${preSeeded.entityId}. The booking link may reference a deleted entity.`
      )
    }
    entityId = entity.id
  } else {
    const { status, entity } = await findOrCreateEntity(db, orgId, {
      name: input.businessName,
      stage: 'prospect',
      source_pipeline: pipeline,
    })
    entityId = entity.id
    entityCreated = status === 'created'
  }

  // 2. Contact resolution. If the admin linked a specific contact and the
  //    guest's email matches that contact (or no new email info), reuse it;
  //    otherwise fall back to email-based dedup and creation so we never
  //    drop the guest's real contact details on the floor.
  let contactId: string
  let contactCreated = false
  const existingContact = await db
    .prepare('SELECT id FROM contacts WHERE org_id = ? AND email = ? LIMIT 1')
    .bind(orgId, input.email)
    .first<{ id: string }>()

  if (existingContact) {
    contactId = existingContact.id
  } else if (preSeeded?.contactId) {
    // The admin seeded a contact but the guest used a different email. Still
    // create a new contact row so we capture the guest's identity — don't
    // overwrite or silently drop it.
    const contact = await createContact(db, orgId, entityId, {
      name: input.name,
      email: input.email,
    })
    contactId = contact.id
    contactCreated = true
  } else {
    const contact = await createContact(db, orgId, entityId, {
      name: input.name,
      email: input.email,
    })
    contactId = contact.id
    contactCreated = true
  }

  // 3. Meeting + legacy assessment row.
  //    Post-integration of #502 (send-booking-link pre-seeds an assessment) and
  //    #504 (meetings table generalizes assessments). Two paths:
  //      (a) Pre-seeded: admin clicked "Send booking link", which created an
  //          assessment row in `scheduled` status. Reuse and set scheduled_at.
  //          TODO: extend send-booking-link to dual-write a meeting row so the
  //          pre-seeded path also populates the canonical `meetings` table.
  //      (b) Standalone: no pre-seed. Write the meeting as canonical and seed
  //          the legacy assessments row with the same id for FK compatibility.
  //    Monitoring window: keep dual-write until the assessments drop migration
  //    lands (follow-up issue #503).
  let meetingId: string | null = null
  let assessmentId: string | null = null
  let assessmentCreated = false
  let meetingCreated = false
  let previousAssessmentScheduledAt: string | null = null
  let previousMeetingScheduledAt: string | null = null
  if (preSeeded?.assessmentId && scheduledAt) {
    const existing = await getAssessment(db, orgId, preSeeded.assessmentId)
    if (!existing) {
      throw new Error(
        `Pre-seeded assessment not found: ${preSeeded.assessmentId}. The booking link may be stale.`
      )
    }
    if (existing.entity_id !== entityId) {
      throw new Error(
        `Pre-seeded assessment ${preSeeded.assessmentId} does not belong to entity ${entityId}.`
      )
    }
    previousAssessmentScheduledAt = existing.scheduled_at
    await updateAssessment(db, orgId, preSeeded.assessmentId, { scheduled_at: scheduledAt })
    assessmentId = preSeeded.assessmentId
    const ensured = await ensureMeetingForAssessment(db, orgId, entityId, {
      assessmentId: preSeeded.assessmentId,
      scheduled_at: scheduledAt,
      meeting_type: preSeeded.meetingType,
    })
    meetingId = ensured.meeting.id
    meetingCreated = ensured.created
    previousMeetingScheduledAt = ensured.previousScheduledAt
  } else if (scheduledAt) {
    const meeting = await createMeetingWithLegacyAssessment(db, orgId, entityId, {
      scheduled_at: scheduledAt,
      meeting_type: 'assessment',
    })
    meetingId = meeting.id
    assessmentId = meeting.id
    assessmentCreated = true
    meetingCreated = true
  }

  // 4. Append intake context
  const intakeLines: string[] = []
  if (input.vertical) intakeLines.push(`Vertical: ${input.vertical}`)
  if (input.employeeCount) intakeLines.push(`Employees: ${input.employeeCount}`)
  if (input.yearsInBusiness) intakeLines.push(`Years in business: ${input.yearsInBusiness}`)
  if (input.biggestChallenge)
    intakeLines.push(`What they're trying to accomplish: ${input.biggestChallenge}`)
  if (input.howHeard) intakeLines.push(`How they found us: ${input.howHeard}`)

  let contextId: string | null = null
  if (intakeLines.length > 0) {
    const contextEntry = await appendContext(db, orgId, {
      entity_id: entityId,
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
    contextId = contextEntry.id
  }

  return {
    entityId,
    contactId,
    assessmentId,
    meetingId,
    entityCreated,
    contactCreated,
    contextId,
    assessmentCreated,
    meetingCreated,
    previousAssessmentScheduledAt,
    previousMeetingScheduledAt,
    intakeLines,
  }
}
