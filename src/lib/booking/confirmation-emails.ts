/**
 * Booking confirmation + admin-notification emails for POST /api/booking/reserve.
 *
 * Extracted from reserve.ts to keep that handler under the file-size ceiling
 * and to isolate the email orchestration. Both sends go through the shared
 * booking-email wrappers (which return a SendResult), and the guest send is
 * result-checked: a Resend rejection returns { success: false } rather than
 * throwing, so an ignored result would silently swallow a guest who never
 * received their confirmation. A failed guest send is routed to the booking
 * alert system (entity timeline + team email) so it is visible, not lost.
 */

import { env } from 'cloudflare:workers'
import { BOOKING_CONFIG } from './config'
import { buildIcs, icsToBase64 } from './ics'
import { buildAdminUrl } from '../config/app-url'
import { recordBookingError } from './alerts'
import { sendBookingConfirmation, sendBookingAdminNotification } from '../email/booking-emails'
import { formatSlotLabelLong } from './reserve-helpers'

/** Narrow structural views of the reserve handler's validated input + commit result. */
export interface ConfirmationEmailInput {
  name: string
  email: string
  businessName: string
  slotStartUtc: string
  guestTimezone: string | null
}

export interface ConfirmationEmailDbResult {
  scheduleId: string
  entityId: string
  meetingId: string
  intakeLines: string[]
}

export interface SendConfirmationArgs {
  input: ConfirmationEmailInput
  dbResult: ConfirmationEmailDbResult
  googleMeetUrl: string
  manageUrl: string
}

function buildConfirmationIcs(
  args: SendConfirmationArgs
): { filename: string; content: string; content_type: string } | null {
  const { input, dbResult, googleMeetUrl, manageUrl } = args
  try {
    const icsResult = buildIcs({
      scheduleId: dbResult.scheduleId,
      sequence: 0,
      method: 'REQUEST',
      startUtc: input.slotStartUtc,
      durationMinutes: BOOKING_CONFIG.slot_minutes,
      title: `${BOOKING_CONFIG.meeting_label} — SMD Services`,
      description: `Assessment call with SMD Services for ${input.businessName}.\n\nManage your booking: ${manageUrl}`,
      location: googleMeetUrl,
      organizerName: BOOKING_CONFIG.consultant.name,
      organizerEmail: BOOKING_CONFIG.consultant.email,
      guestName: input.name,
      guestEmail: input.email,
    })
    return {
      filename: 'invite.ics',
      content: icsToBase64(icsResult.ics),
      content_type: icsResult.contentType,
    }
  } catch (icsErr) {
    console.error('[api/booking/reserve] ICS generation failed:', icsErr)
    return null
  }
}

async function alertGuestEmailFailure(
  details: { entityId: string; scheduleId: string },
  message: string
): Promise<void> {
  try {
    await recordBookingError(env.DB, env.RESEND_API_KEY, 'guest_email_delivery_failed', {
      entityId: details.entityId,
      scheduleId: details.scheduleId,
      message,
    })
  } catch (alertErr) {
    console.error('[api/booking/reserve] Failed to record guest email alert:', alertErr)
  }
}

export async function sendConfirmationEmails(args: SendConfirmationArgs): Promise<void> {
  const { input, dbResult, googleMeetUrl, manageUrl } = args
  const { name, email, businessName, slotStartUtc, guestTimezone } = input
  const { scheduleId, intakeLines, entityId, meetingId } = dbResult

  const displayTz = guestTimezone || BOOKING_CONFIG.consultant.timezone
  const slotLabel = formatSlotLabelLong(slotStartUtc, displayTz)
  const consultantTzLabel = formatSlotLabelLong(slotStartUtc, BOOKING_CONFIG.consultant.timezone)
  const icsAttachment = buildConfirmationIcs(args)

  try {
    const guestResult = await sendBookingConfirmation(env.RESEND_API_KEY, {
      guestName: name,
      businessName,
      slotLabel,
      meetUrl: googleMeetUrl,
      manageUrl,
      meetingLabel: BOOKING_CONFIG.meeting_label,
      guestEmail: email,
      icsAttachment,
      meetingId,
    })
    if (!guestResult.success) {
      console.error('[api/booking/reserve] Confirmation email NOT sent:', guestResult.error)
      await alertGuestEmailFailure(
        { entityId, scheduleId },
        `Confirmation email to ${email} was rejected: ${guestResult.error ?? 'unknown error'}`
      )
    }
  } catch (emailErr) {
    console.error('[api/booking/reserve] Confirmation email failed:', emailErr)
    await alertGuestEmailFailure(
      { entityId, scheduleId },
      `Confirmation email to ${email} threw: ${
        emailErr instanceof Error ? emailErr.message : String(emailErr)
      }`
    )
  }

  try {
    const adminResult = await sendBookingAdminNotification(env.RESEND_API_KEY, {
      guestName: name,
      guestEmail: email,
      businessName,
      slotLabel: consultantTzLabel,
      intakeLines,
      entityAdminUrl: buildAdminUrl(env, `/admin/entities/${entityId}`),
      replyTo: email,
      subjectSlotLabel: consultantTzLabel,
    })
    if (!adminResult.success) {
      console.error('[api/booking/reserve] Admin notification NOT sent:', adminResult.error)
    }
  } catch (emailErr) {
    console.error('[api/booking/reserve] Admin notification email failed:', emailErr)
  }
}
