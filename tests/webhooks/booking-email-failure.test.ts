import { describe, it, expect, vi } from 'vitest'
import { parseBookingEmailFailure } from '../../src/lib/webhooks/booking-email-failure'
import type { ResendWebhookPayload } from '../../src/lib/webhooks/resend-handler'
import type { EmailTag } from '../../src/lib/email/resend'

const sentTags: EmailTag[][] = []
vi.mock('../../src/lib/email/resend', () => ({
  sendEmail: vi.fn(async (_key: string | undefined, payload: { tags?: EmailTag[] }) => {
    sentTags.push(payload.tags ?? [])
    return { success: true, id: 'email-1' }
  }),
}))

const { sendBookingCancellation, sendBookingConfirmation, sendBookingReschedule } =
  await import('../../src/lib/email/booking-emails')

/**
 * Unit coverage for the pure booking-email failure detector, and a round trip
 * proving the booking-email wrappers tag their sends so the detector
 * recognizes a failure of each one. The DB-touching orchestration is exercised end-to-end in
 * tests/resend-webhook.test.ts.
 */

describe('parseBookingEmailFailure', () => {
  it('returns null for a non-failure event', () => {
    const payload: ResendWebhookPayload = {
      type: 'email.delivered',
      data: { tags: { category: 'booking_confirmation' } },
    }
    expect(parseBookingEmailFailure(payload)).toBeNull()
  })

  it('returns null for a failure event whose category is not a booking email', () => {
    const payload: ResendWebhookPayload = {
      type: 'email.suppressed',
      data: { tags: { category: 'outreach' } },
    }
    expect(parseBookingEmailFailure(payload)).toBeNull()
  })

  it('returns null for a failure event with no tags', () => {
    const payload: ResendWebhookPayload = { type: 'email.bounced', data: {} }
    expect(parseBookingEmailFailure(payload)).toBeNull()
  })

  it('parses a suppressed booking confirmation, preferring suppressed.message', () => {
    const payload: ResendWebhookPayload = {
      type: 'email.suppressed',
      data: {
        to: ['scott@smd.services'],
        tags: { category: 'booking_confirmation', meeting_id: 'mtg-1' },
        suppressed: { message: 'on the suppression list', type: 'OnAccountSuppressionList' },
      },
    }
    expect(parseBookingEmailFailure(payload)).toMatchObject({
      eventType: 'email.suppressed',
      category: 'booking_confirmation',
      meetingId: 'mtg-1',
      recipient: 'scott@smd.services',
      reason: 'on the suppression list',
    })
  })

  it('parses a bounced booking reschedule, using bounce.message', () => {
    const payload: ResendWebhookPayload = {
      type: 'email.bounced',
      data: {
        to: ['a@b.com'],
        tags: { category: 'booking_reschedule', meeting_id: 'mtg-2' },
        bounce: { message: 'mailbox does not exist', type: 'Permanent' },
      },
    }
    expect(parseBookingEmailFailure(payload)).toMatchObject({
      category: 'booking_reschedule',
      meetingId: 'mtg-2',
      reason: 'mailbox does not exist',
    })
  })

  it('falls back to a default reason and null meetingId when detail is absent', () => {
    const payload: ResendWebhookPayload = {
      type: 'email.failed',
      data: { tags: { category: 'booking_cancellation' } },
    }
    const parsed = parseBookingEmailFailure(payload)
    expect(parsed?.reason).toBe('no reason provided')
    expect(parsed?.meetingId).toBeNull()
    expect(parsed?.recipient).toBeNull()
  })
})

describe('booking-email wrappers tag their sends so a failure is recognized', () => {
  const guest = {
    guestName: 'Pat Fixture',
    businessName: 'Fixture Co',
    guestEmail: 'pat@example.com',
  }
  const sends = [
    [
      'booking_confirmation',
      () =>
        sendBookingConfirmation(undefined, {
          ...guest,
          slotLabel: 'Tuesday at 9:00 AM',
          meetUrl: null,
          manageUrl: 'https://smd.services/book/manage/x',
          meetingLabel: 'Assessment call',
          icsAttachment: null,
          meetingId: 'mtg-1',
        }),
    ],
    [
      'booking_reschedule',
      () =>
        sendBookingReschedule(undefined, {
          ...guest,
          oldSlotLabel: 'Tuesday at 9:00 AM',
          newSlotLabel: 'Wednesday at 9:00 AM',
          meetUrl: null,
          manageUrl: 'https://smd.services/book/manage/x',
          meetingLabel: 'Assessment call',
          icsAttachment: null,
          meetingId: 'mtg-1',
        }),
    ],
    [
      'booking_cancellation',
      () =>
        sendBookingCancellation(undefined, {
          ...guest,
          slotLabel: 'Tuesday at 9:00 AM',
          rebookUrl: 'https://smd.services/book',
          icsAttachment: null,
          meetingId: 'mtg-1',
        }),
    ],
  ] as const

  for (const [category, send] of sends) {
    it(`${category}: a bounce of the tagged send parses back to its category and meeting`, async () => {
      sentTags.length = 0
      await send()
      expect(sentTags).toHaveLength(1)
      const tags = Object.fromEntries((sentTags[0] ?? []).map((t) => [t.name, t.value]))
      const parsed = parseBookingEmailFailure({
        type: 'email.bounced',
        data: { to: ['pat@example.com'], tags, bounce: { message: 'no such mailbox' } },
      })
      expect(parsed).toMatchObject({ category, meetingId: 'mtg-1' })
    })
  }
})
