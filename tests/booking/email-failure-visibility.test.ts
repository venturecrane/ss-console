import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Regression guard: guest booking emails must not be sent fire-and-forget.
 *
 * `sendEmail` returns { success: false } on a Resend rejection (e.g. the
 * recipient is on the suppression list) rather than throwing. The original
 * handlers awaited the send but discarded the result, so a guest who never
 * received their confirmation / reschedule / cancellation email was
 * completely invisible — the booking still returned success.
 *
 * These structural checks lock in that each handler inspects the send result
 * and routes a failure to recordBookingError('guest_email_delivery_failed'),
 * which writes an entity-timeline alert row and emails the team to follow up.
 *
 * See: missing-guest-email incident 2026-06-30 (scott@smd.services suppressed).
 */

// The reserve handler delegates its guest-confirmation send to
// confirmation-emails.ts; the manage handlers keep their send logic inline.
const confirmationSrc = readFileSync(resolve('src/lib/booking/confirmation-emails.ts'), 'utf-8')
const rescheduleSrc = readFileSync(
  resolve('src/pages/api/booking/manage/[token]/reschedule.ts'),
  'utf-8'
)
const cancelSrc = readFileSync(resolve('src/pages/api/booking/manage/[token]/cancel.ts'), 'utf-8')

const HANDLERS: Array<{ name: string; code: string }> = [
  { name: 'confirmation-emails.ts', code: confirmationSrc },
  { name: 'reschedule.ts', code: rescheduleSrc },
  { name: 'cancel.ts', code: cancelSrc },
]

describe('booking guest-email failure visibility', () => {
  for (const { name, code } of HANDLERS) {
    describe(name, () => {
      it('imports the booking alert recorder', () => {
        expect(code).toContain('recordBookingError')
      })

      it('inspects the guest send result instead of discarding it', () => {
        // A captured result whose .success is checked — not a bare `await send(...)`.
        expect(code).toMatch(/Result\.success/)
      })

      it('routes a guest-email failure to the guest_email_delivery_failed alert', () => {
        expect(code).toContain("'guest_email_delivery_failed'")
      })
    })
  }
})
