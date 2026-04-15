/**
 * Booking email send functions.
 *
 * Composes HTML from the booking templates in `./templates.ts` and sends
 * via Resend using the shared `sendEmail` helper. Each function returns
 * a SendResult. All functions are fire-and-forget safe — callers should
 * try/catch and log failures without blocking the booking flow.
 */

import { BRAND_NAME } from '../config/brand'
import { sendEmail } from './resend'
import type { SendResult, EmailAttachment } from './resend'
import {
  bookingConfirmationEmailHtml,
  bookingRescheduledEmailHtml,
  bookingCancelledEmailHtml,
  bookingAdminNotificationEmailHtml,
} from './templates'
import type {
  BookingConfirmationEmailInput,
  BookingRescheduledEmailInput,
  BookingCancelledEmailInput,
  BookingAdminNotificationInput,
} from './templates'

const NOTIFY_EMAIL = 'team@smd.services'

// ---------------------------------------------------------------------------
// Confirmation (sent to guest after successful reserve)
// ---------------------------------------------------------------------------

export interface SendBookingConfirmationInput extends BookingConfirmationEmailInput {
  guestEmail: string
  /** ICS attachment, or null if ICS generation failed. */
  icsAttachment: EmailAttachment | null
}

export async function sendBookingConfirmation(
  apiKey: string | undefined,
  input: SendBookingConfirmationInput
): Promise<SendResult> {
  const html = bookingConfirmationEmailHtml(input)
  const attachments: EmailAttachment[] = []
  if (input.icsAttachment) {
    attachments.push(input.icsAttachment)
  }

  return sendEmail(apiKey, {
    to: input.guestEmail,
    subject: `Confirmed: ${input.meetingLabel} with ${BRAND_NAME}`,
    html,
    ...(attachments.length > 0 ? { attachments } : {}),
  })
}

// ---------------------------------------------------------------------------
// Reschedule (sent to guest after successful reschedule)
// ---------------------------------------------------------------------------

export interface SendBookingRescheduleInput extends BookingRescheduledEmailInput {
  guestEmail: string
  /** ICS attachment with bumped SEQUENCE, or null if ICS generation failed. */
  icsAttachment: EmailAttachment | null
}

export async function sendBookingReschedule(
  apiKey: string | undefined,
  input: SendBookingRescheduleInput
): Promise<SendResult> {
  const html = bookingRescheduledEmailHtml(input)
  const attachments: EmailAttachment[] = []
  if (input.icsAttachment) {
    attachments.push(input.icsAttachment)
  }

  return sendEmail(apiKey, {
    to: input.guestEmail,
    subject: `Rescheduled: ${input.meetingLabel} with ${BRAND_NAME}`,
    html,
    ...(attachments.length > 0 ? { attachments } : {}),
  })
}

// ---------------------------------------------------------------------------
// Cancellation (sent to guest after cancellation)
// ---------------------------------------------------------------------------

export interface SendBookingCancellationInput extends BookingCancelledEmailInput {
  guestEmail: string
  /** ICS CANCEL attachment, or null if ICS generation failed. */
  icsAttachment: EmailAttachment | null
}

export async function sendBookingCancellation(
  apiKey: string | undefined,
  input: SendBookingCancellationInput
): Promise<SendResult> {
  const html = bookingCancelledEmailHtml(input)
  const attachments: EmailAttachment[] = []
  if (input.icsAttachment) {
    attachments.push(input.icsAttachment)
  }

  return sendEmail(apiKey, {
    to: input.guestEmail,
    subject: `Cancelled: Assessment call with ${BRAND_NAME}`,
    html,
    ...(attachments.length > 0 ? { attachments } : {}),
  })
}

// ---------------------------------------------------------------------------
// Admin notification (sent to team on every new booking)
// ---------------------------------------------------------------------------

export interface SendBookingAdminNotificationInput extends BookingAdminNotificationInput {
  /** Reply-to address (the guest's email). */
  replyTo: string
  /** Formatted slot label for the subject line. */
  subjectSlotLabel: string
}

export async function sendBookingAdminNotification(
  apiKey: string | undefined,
  input: SendBookingAdminNotificationInput
): Promise<SendResult> {
  const html = bookingAdminNotificationEmailHtml(input)

  return sendEmail(apiKey, {
    to: NOTIFY_EMAIL,
    reply_to: input.replyTo,
    subject: `New booking: ${input.businessName} — ${input.subjectSlotLabel}`,
    html,
  })
}
