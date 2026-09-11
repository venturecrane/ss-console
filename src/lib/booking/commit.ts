/**
 * The booking commit: intake row, schedule sidecars, manage token.
 *
 * Phase 2 of `POST /api/booking/reserve`. Lived inside that route until
 * 2026-09-10, when the route was the domain layer (code review 2026-09-10,
 * Architecture 3): the multi-table write is business logic, so it sits here
 * and the route gates, validates, and delegates.
 */

import { env } from 'cloudflare:workers'
import { ORG_ID } from '../constants'
import { BOOKING_CONFIG } from './config'
import { generateManageToken, hashManageToken, computeManageTokenExpiry } from './tokens'
import { processIntakeSubmission, type PreSeededIntake } from './intake-core'
import type { AdAttribution } from '../marketing/attribution'
import { createScheduleStatement } from './schedule'
import { createMeetingScheduleStatement } from './meeting-schedule'

/** The reserve request after the route has validated it. */
export interface ReserveInput {
  name: string
  email: string
  businessName: string
  phone: string | null
  slotStartUtc: string
  slotEndUtc: string
  website: string | null
  userMessage: string | null
  vertical: string | null
  employeeCount: number | null
  yearsInBusiness: number | null
  biggestChallenge: string | null
  howHeard: string | null
  guestTimezone: string | null
  prefillTokenRaw: string | null
}

export interface BookingCommitArgs {
  input: ReserveInput
  preSeeded: PreSeededIntake | null
  /** First-touch ad attribution from the ss_attr cookie (ADR 0066 gate 1). */
  attribution: AdAttribution | null
}

export interface BookingCommitResult {
  assessmentId: string
  meetingId: string
  entityId: string
  scheduleId: string
  meetingScheduleId: string
  manageToken: string
  intakeLines: string[]
  entityCreated: boolean
  contactCreated: boolean
  contactId: string | undefined
  contextId: string | null
  previousAssessmentScheduledAt: string | null
  previousMeetingScheduledAt: string | null
}

interface SidecarParams {
  assessmentId: string
  meetingId: string
  name: string
  email: string
  slotStartUtc: string
  slotEndUtc: string
  guestTimezone: string | null
  manageTokenHash: string
  manageTokenExpiresAt: string
}

async function seedScheduleSidecars(
  a: SidecarParams
): Promise<{ scheduleId: string; meetingScheduleId: string }> {
  // Both are seeded during the monitoring window so existing manage-token
  // consumers continue to resolve whichever table they query.
  // When the drop migration lands the legacy assessment_schedule write goes away.
  const common = {
    orgId: ORG_ID,
    slotStartUtc: a.slotStartUtc,
    slotEndUtc: a.slotEndUtc,
    durationMinutes: BOOKING_CONFIG.slot_minutes,
    timezone: BOOKING_CONFIG.consultant.timezone,
    guestTimezone: a.guestTimezone,
    guestName: a.name,
    guestEmail: a.email,
    manageTokenHash: a.manageTokenHash,
    manageTokenExpiresAt: a.manageTokenExpiresAt,
  }
  const { statement: scheduleStmt, id: scheduleId } = createScheduleStatement(env.DB, {
    assessmentId: a.assessmentId,
    ...common,
  })
  await scheduleStmt.run()

  const { statement: meetingScheduleStmt, id: meetingScheduleId } = createMeetingScheduleStatement(
    env.DB,
    { meetingId: a.meetingId, ...common }
  )
  await meetingScheduleStmt.run()

  return { scheduleId, meetingScheduleId }
}

async function mintManageToken(
  slotEndUtc: string
): Promise<{ manageToken: string; manageTokenHash: string; manageTokenExpiresAt: string }> {
  const manageToken = generateManageToken()
  return {
    manageToken,
    manageTokenHash: await hashManageToken(manageToken),
    manageTokenExpiresAt: computeManageTokenExpiry(
      slotEndUtc,
      BOOKING_CONFIG.manage_token_ttl_hours_after_slot
    ),
  }
}

export async function commitBookingToDb(args: BookingCommitArgs): Promise<BookingCommitResult> {
  const { input, preSeeded, attribution } = args
  const {
    name,
    email,
    businessName,
    phone,
    slotStartUtc,
    slotEndUtc,
    website,
    userMessage,
    vertical,
    employeeCount,
    yearsInBusiness,
    biggestChallenge,
    howHeard,
    guestTimezone,
  } = input

  const intakeResult = await processIntakeSubmission(
    env.DB,
    ORG_ID,
    {
      name,
      email,
      businessName,
      phone,
      website,
      userMessage,
      vertical,
      employeeCount,
      yearsInBusiness,
      biggestChallenge,
      howHeard,
      attribution,
    },
    {
      scheduledAt: slotStartUtc,
      source: preSeeded ? 'admin_booking_link' : 'website_intake_booking',
      preSeeded,
    }
  )

  // assessmentId is guaranteed non-null when scheduledAt is provided.
  // By intake-core construction, assessmentId == meetingId — the booking
  // flow seeds both tables with the same primary key during the
  // monitoring window (see src/lib/booking/intake-core.ts).
  const assessmentId = intakeResult.assessmentId!
  const meetingId = intakeResult.meetingId!

  const { manageToken, manageTokenHash, manageTokenExpiresAt } = await mintManageToken(slotEndUtc)

  // Create assessment_schedule (legacy) and meeting_schedule (canonical) sidecars.
  const { scheduleId, meetingScheduleId } = await seedScheduleSidecars({
    assessmentId,
    meetingId,
    name,
    email,
    slotStartUtc,
    slotEndUtc,
    guestTimezone,
    manageTokenHash,
    manageTokenExpiresAt,
  })

  return {
    assessmentId,
    meetingId,
    entityId: intakeResult.entityId,
    scheduleId,
    meetingScheduleId,
    manageToken,
    intakeLines: intakeResult.intakeLines,
    entityCreated: intakeResult.entityCreated,
    contactCreated: intakeResult.contactCreated,
    contactId: intakeResult.contactId,
    contextId: intakeResult.contextId,
    previousAssessmentScheduledAt: intakeResult.previousAssessmentScheduledAt,
    previousMeetingScheduledAt: intakeResult.previousMeetingScheduledAt,
  }
}
