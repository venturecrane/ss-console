/**
 * Booking-email delivery-failure alerting from Resend webhooks.
 *
 * Transactional booking emails (confirmation / reschedule / cancellation) are
 * sent with a `category` tag (booking_*) and a `meeting_id` tag. When Resend
 * reports that one of them did NOT reach the guest — `email.suppressed`
 * (recipient on the account suppression list), `email.bounced`,
 * `email.complained`, or `email.failed` — this records a booking alert so the
 * team finds out and can contact the guest by hand. This is the asynchronous
 * counterpart to the synchronous send-result check in the booking handlers:
 * suppression is reported as a later webhook event, not at send time.
 *
 * Why a separate path from the outreach handler: booking emails go through the
 * plain send path and have no `outreach_events` 'sent' row, so the outreach
 * attribution join cannot see them. The originating entity is instead resolved
 * from the `meeting_id` tag.
 */

import { recordBookingError } from '../booking/alerts'
import { getMeeting } from '../db/meetings'
import { ORG_ID } from '../constants'
import type { ResendWebhookPayload } from './resend-handler'
import { captureError } from '../observability/sentry'

const AREA = 'webhook/resend/booking-failure'

/** Resend event types that mean the guest did not receive the email. */
const FAILURE_EVENT_TYPES = new Set([
  'email.bounced',
  'email.complained',
  'email.suppressed',
  'email.failed',
])

const BOOKING_CATEGORY_PREFIX = 'booking_'

export interface ParsedBookingEmailFailure {
  /** Resend event type, e.g. 'email.suppressed'. */
  eventType: string
  /** The booking_* category tag. */
  category: string
  /** Originating meeting/assessment id from the meeting_id tag, if present. */
  meetingId: string | null
  /** Recipient address, if present. */
  recipient: string | null
  /** Human-readable failure reason from the event-specific detail block. */
  reason: string
}

function failureReason(data: ResendWebhookPayload['data']): string {
  return (
    data?.suppressed?.message ??
    data?.bounce?.message ??
    data?.failed?.reason ??
    'no reason provided'
  )
}

/**
 * Pure detection: returns failure details when the payload is a delivery
 * failure for a tagged booking email, else null. No I/O — unit-testable.
 */
export function parseBookingEmailFailure(
  payload: ResendWebhookPayload
): ParsedBookingEmailFailure | null {
  if (!FAILURE_EVENT_TYPES.has(payload.type)) return null

  const data = payload.data
  const tags = data?.tags
  const category = tags?.category
  if (!category) return null
  if (!category.startsWith(BOOKING_CATEGORY_PREFIX)) return null

  return {
    eventType: payload.type,
    category,
    meetingId: tags?.meeting_id ?? null,
    recipient: data?.to?.[0] ?? null,
    reason: failureReason(data),
  }
}

export interface BookingEmailFailureResult {
  handled: boolean
  entityId?: string | null
}

/**
 * Detect + record a booking-email delivery failure. Resolves the originating
 * entity from the meeting_id tag (so the alert lands on the prospect's
 * timeline) and routes to recordBookingError, which writes a context alert
 * row and emails the team (rate-limited). Best-effort: never throws.
 */
export async function handleBookingEmailDeliveryFailure(
  db: D1Database,
  resendApiKey: string | undefined,
  payload: ResendWebhookPayload
): Promise<BookingEmailFailureResult> {
  const failure = parseBookingEmailFailure(payload)
  if (!failure) return { handled: false }

  let entityId: string | undefined
  if (failure.meetingId) {
    try {
      const meeting = await getMeeting(db, ORG_ID, failure.meetingId)
      entityId = meeting?.entity_id
    } catch (err) {
      console.error('[webhook/resend] booking-failure: meeting lookup failed:', err)
      captureError(err, AREA)
    }
  }

  const recipient = failure.recipient ?? 'unknown recipient'
  const message =
    `Booking ${failure.category} email to ${recipient} was not delivered ` +
    `(${failure.eventType}): ${failure.reason}`

  try {
    await recordBookingError(db, resendApiKey, 'guest_email_delivery_failed', {
      entityId,
      assessmentId: failure.meetingId ?? undefined,
      message,
    })
  } catch (err) {
    console.error('[webhook/resend] booking-failure: recordBookingError failed:', err)
    captureError(err, AREA)
  }

  return { handled: true, entityId: entityId ?? null }
}
