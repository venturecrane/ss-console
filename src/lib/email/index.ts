/**
 * Email module — re-exports for convenience.
 */

export { sendEmail } from './resend'
export type { EmailPayload, SendResult } from './resend'
export { buildMagicLinkUrl, magicLinkEmailHtml, portalInvitationEmailHtml } from './templates'
export {
  sendBookingConfirmation,
  sendBookingReschedule,
  sendBookingCancellation,
  sendBookingAdminNotification,
} from './booking-emails'
export type {
  SendBookingConfirmationInput,
  SendBookingRescheduleInput,
  SendBookingCancellationInput,
  SendBookingAdminNotificationInput,
} from './booking-emails'
