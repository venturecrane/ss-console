/**
 * Email templates for magic link, portal, and booking confirmations.
 *
 * All templates produce self-contained HTML emails with inline styles.
 * No external CSS or image dependencies.
 */
import { BRAND_NAME } from '../config/brand'

const PORTAL_CARD_WIDTH = '480px'
const BOOKING_CARD_WIDTH = '520px'

export function escapeEmailHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function emailDocument(input: {
  body: string
  title?: string
  subtitle?: string
  width?: string
  align?: 'center' | 'left'
  footer?: boolean
}): string {
  const width = input.width ?? PORTAL_CARD_WIDTH
  const align = input.align ?? 'center'
  const title = input.title
    ? `<h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">${input.title}</h1>`
    : ''
  const subtitle = input.subtitle
    ? `<p style="font-size:14px;color:#64748b;margin:0 0 24px;">${input.subtitle}</p>`
    : ''
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:${width};margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:${align};">
      ${title}
      ${subtitle}
${input.body}
    </div>
${input.footer === false ? '' : emailFooter()}
  </div>
</body>
</html>`
}

function emailFooter(): string {
  return `    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} ${BRAND_NAME} &middot; Phoenix, AZ
      </p>
    </div>`
}

export function portalDocument(body: string): string {
  return emailDocument({ title: BRAND_NAME, subtitle: 'Client Portal', body })
}

export function paragraph(html: string, margin = '0 0 24px', size = '15px'): string {
  return `      <p style="font-size:${size};color:#334155;margin:${margin};">
        ${html}
      </p>`
}

export function mutedParagraph(html: string, margin = '24px 0 0'): string {
  return `      <p style="font-size:12px;color:#94a3b8;margin:${margin};">
        ${html}
      </p>`
}

export function greeting(clientName: string): string {
  const name = clientName ? ` ${escapeEmailHtml(clientName)}` : ''
  return `Hi${name},`
}

export function actionButton(url: string, label: string, padding = '12px 32px'): string {
  const href = escapeEmailHtml(url)
  return `      <a href="${href}"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:${padding};border-radius:6px;">
        ${label}
      </a>`
}

export function detailPanel(label: string, value: string, margin = '0 0 24px'): string {
  return `      <div style="background:#f1f5f9;border-radius:6px;padding:16px;margin:${margin};">
        <p style="font-size:13px;color:#64748b;margin:0 0 4px;text-transform:uppercase;letter-spacing:0.05em;">${label}</p>
        <p style="font-size:16px;color:#0f172a;font-weight:600;margin:0;">${value}</p>
      </div>`
}

function joinCallPanel(meetUrl: string | null): string {
  if (!meetUrl) return ''
  const href = escapeEmailHtml(meetUrl)
  return `      <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:16px;margin:0 0 24px;">
        <p style="font-size:13px;color:#1e40af;margin:0 0 8px;font-weight:600;">Join the call</p>
        <a href="${href}" style="font-size:14px;color:#1e40af;word-break:break-all;">${href}</a>
      </div>`
}

/**
 * Build the magic link URL for token verification.
 */
export function buildMagicLinkUrl(baseUrl: string, token: string): string {
  const url = new URL('/auth/verify', baseUrl)
  url.searchParams.set('token', token)
  return url.toString()
}

/**
 * Email sent when a client requests a magic link from the portal login page.
 */
export function magicLinkEmailHtml(clientName: string, magicLinkUrl: string): string {
  return portalDocument(
    [
      paragraph(greeting(clientName), '0 0 8px'),
      paragraph('Click the button below to sign in to your portal.'),
      actionButton(magicLinkUrl, 'Sign in to Portal'),
      mutedParagraph('This link expires in 15 minutes and can only be used once.'),
      mutedParagraph("If you didn't request this, you can safely ignore this email.", '8px 0 0'),
    ].join('\n')
  )
}

/**
 * Email sent when an invoice is sent to a client via the portal.
 * Links them to the portal to view and pay the invoice.
 */
export function invoiceSentEmailHtml(
  clientName: string,
  amount: string,
  portalUrl: string
): string {
  return portalDocument(
    [
      paragraph(greeting(clientName), '0 0 8px'),
      paragraph(
        `Your invoice from ${BRAND_NAME} for ${escapeEmailHtml(amount)} is ready. Sign in to your portal to view the details and make a payment.`
      ),
      actionButton(portalUrl, 'View Invoice'),
      mutedParagraph('If you have any questions, reply directly to this email.'),
    ].join('\n')
  )
}

/**
 * Email sent when a payment is received for an invoice.
 */
export function paymentConfirmationEmailHtml(clientName: string, amount: string): string {
  return portalDocument(
    [
      paragraph(greeting(clientName), '0 0 8px'),
      paragraph(`We've received your payment of ${escapeEmailHtml(amount)}. Thank you!`),
      paragraph('If you have any questions about your engagement, our team is here to help.'),
      mutedParagraph('If you have any questions, reply directly to this email.'),
    ].join('\n')
  )
}

/**
 * Sent to the client after their SOW is signed (SOW-signed outbox job).
 */
export function signatureConfirmationEmailHtml(businessName: string): string {
  return emailDocument({
    title: BRAND_NAME,
    subtitle: 'Client Portal',
    footer: false,
    body: [
      paragraph(`Hi ${escapeEmailHtml(businessName)},`, '0 0 8px'),
      paragraph(
        "Your Statement of Work has been signed successfully. We're excited to get started working together."
      ),
    ].join('\n'),
  })
}

export function portalWelcomeEmailHtml(clientName: string, loginUrl: string): string {
  return portalDocument(
    [
      paragraph(greeting(clientName), '0 0 8px'),
      paragraph(
        "Your client portal is ready. This is where you'll find your project details, milestones, invoices, and everything related to our work together.",
        '0 0 8px'
      ),
      paragraph("Click below to sign in. We'll send a secure link to your email."),
      actionButton(loginUrl, 'Sign In to Your Portal'),
      mutedParagraph('You can access your portal anytime at portal.smd.services'),
    ].join('\n')
  )
}

// ===========================================================================
// Booking emails (Calendly replacement — added with migration 0011)
// ===========================================================================

/**
 * HTML escaping helper used by all booking templates. Prevents intake form
 * data (business name, challenge text, etc.) from injecting markup into
 * the email body.
 */
function escapeBookingHtml(str: string): string {
  return escapeEmailHtml(str)
}

export interface BookingConfirmationEmailInput {
  guestName: string
  businessName: string
  /** Localized slot label, e.g., "Tuesday, April 14 at 9:00 AM (Phoenix)". */
  slotLabel: string
  /** Video call URL (e.g. Zoom personal meeting link). */
  meetUrl: string | null
  manageUrl: string
  meetingLabel: string
}

/**
 * Sent to the guest immediately after a successful POST /api/booking/reserve.
 * The email includes the ICS attachment via the Resend `attachments` field.
 */
export function bookingConfirmationEmailHtml(input: BookingConfirmationEmailInput): string {
  const guestName = escapeBookingHtml(input.guestName)
  const businessName = escapeBookingHtml(input.businessName)
  const slotLabel = escapeBookingHtml(input.slotLabel)
  const meetingLabel = escapeBookingHtml(input.meetingLabel)
  const manageUrl = escapeBookingHtml(input.manageUrl)
  return emailDocument({
    width: BOOKING_CARD_WIDTH,
    align: 'left',
    title: "You're booked.",
    subtitle: `${BRAND_NAME} &middot; ${meetingLabel}`,
    body: [
      paragraph(`Hi ${guestName},`, '0 0 16px'),
      paragraph(
        `Your assessment call for <strong>${businessName}</strong> is confirmed.`,
        '0 0 16px'
      ),
      detailPanel('When', slotLabel),
      joinCallPanel(input.meetUrl),
      paragraph('Need to reschedule or cancel?', '0 0 8px', '14px'),
      paragraph(
        `<a href="${manageUrl}" style="color:#1e40af;">Manage your booking →</a>`,
        '0 0 24px',
        '14px'
      ),
      `      <p style="font-size:13px;color:#64748b;margin:0;">
        Looking forward to talking,<br>
        ${BRAND_NAME}
      </p>`,
    ].join('\n'),
  })
}

export interface BookingAdminNotificationInput {
  guestName: string
  guestEmail: string
  businessName: string
  slotLabel: string
  intakeLines: string[]
  entityAdminUrl: string
}

/**
 * Sent to team@smd.services on every successful reserve. Replaces the
 * legacy intake notification (which was tied to the Calendly+intake flow).
 */
export function bookingAdminNotificationEmailHtml(input: BookingAdminNotificationInput): string {
  const guestName = escapeBookingHtml(input.guestName)
  const guestEmail = escapeBookingHtml(input.guestEmail)
  const businessName = escapeBookingHtml(input.businessName)
  const slotLabel = escapeBookingHtml(input.slotLabel)
  const entityAdminUrl = escapeBookingHtml(input.entityAdminUrl)
  const intakeHtml = input.intakeLines.map((line) => `<p>${escapeBookingHtml(line)}</p>`).join('')
  return `<p><strong>${guestName}</strong> &lt;${guestEmail}&gt; from <strong>${businessName}</strong> just booked an assessment call.</p>
<p><strong>When:</strong> ${slotLabel}</p>
<hr>
${intakeHtml}
<hr>
<p><a href="${entityAdminUrl}">View in admin →</a></p>`
}

export interface BookingRescheduledEmailInput {
  guestName: string
  businessName: string
  oldSlotLabel: string
  newSlotLabel: string
  meetUrl: string | null
  manageUrl: string
  meetingLabel: string
}

/**
 * Sent to the guest after a successful reschedule. The body explicitly
 * tells multi-calendar users (Outlook/Apple) to remove the old event,
 * since SEQUENCE-bumped UPDATEs don't always auto-replace cleanly.
 */
export function bookingRescheduledEmailHtml(input: BookingRescheduledEmailInput): string {
  const guestName = escapeBookingHtml(input.guestName)
  const businessName = escapeBookingHtml(input.businessName)
  const oldSlot = escapeBookingHtml(input.oldSlotLabel)
  const newSlot = escapeBookingHtml(input.newSlotLabel)
  const manageUrl = escapeBookingHtml(input.manageUrl)
  const meetingLabel = escapeBookingHtml(input.meetingLabel)
  const businessClause = businessName ? ` for <strong>${businessName}</strong>` : ''
  return emailDocument({
    width: BOOKING_CARD_WIDTH,
    align: 'left',
    title: 'Your call has been rescheduled.',
    subtitle: `${BRAND_NAME} &middot; ${meetingLabel}`,
    body: [
      paragraph(`Hi ${guestName},`, '0 0 16px'),
      paragraph(`Your assessment call${businessClause} has moved.`, '0 0 16px'),
      `      <div style="background:#f1f5f9;border-radius:6px;padding:16px;margin:0 0 16px;">
        <p style="font-size:12px;color:#94a3b8;margin:0 0 4px;text-decoration:line-through;">${oldSlot}</p>
        <p style="font-size:16px;color:#0f172a;font-weight:600;margin:0;">${newSlot}</p>
      </div>`,
      joinCallPanel(input.meetUrl),
      `      <p style="font-size:13px;color:#64748b;margin:0 0 16px;">
        <strong>Heads up:</strong> if you use Outlook or Apple Calendar, you may see
        both the old and new entry side-by-side. You can safely remove the old one.
      </p>`,
      paragraph(
        `Need to make another change? <a href="${manageUrl}" style="color:#1e40af;">Manage your booking →</a>`,
        '0',
        '14px'
      ),
    ].join('\n'),
  })
}

export interface BookingCancelledEmailInput {
  guestName: string
  businessName: string
  slotLabel: string
  rebookUrl: string
}

/**
 * Sent to the guest after a successful cancellation. Includes a link
 * to /book so they can rebook if they want to.
 */
export function bookingCancelledEmailHtml(input: BookingCancelledEmailInput): string {
  const guestName = escapeBookingHtml(input.guestName)
  const businessName = escapeBookingHtml(input.businessName)
  const slotLabel = escapeBookingHtml(input.slotLabel)
  const rebookUrl = escapeBookingHtml(input.rebookUrl)
  const businessClause = businessName ? ` for <strong>${businessName}</strong>` : ''
  return emailDocument({
    width: BOOKING_CARD_WIDTH,
    align: 'left',
    title: 'Your call has been cancelled.',
    subtitle: BRAND_NAME,
    body: [
      paragraph(`Hi ${guestName},`, '0 0 16px'),
      paragraph(
        `Your assessment call${businessClause} scheduled for <strong>${slotLabel}</strong> has been cancelled.`,
        '0 0 16px'
      ),
      paragraph(
        `Whenever you're ready, you can <a href="${rebookUrl}" style="color:#1e40af;">book a new time →</a>`,
        '0 0 24px',
        '14px'
      ),
      `      <p style="font-size:13px;color:#64748b;margin:0;">
        — ${BRAND_NAME}
      </p>`,
    ].join('\n'),
  })
}

// ===========================================================================
// Admin-issued booking link invitation (#467)
// ===========================================================================

export interface BookingLinkInviteEmailInput {
  /**
   * Display name of the recipient (primary contact). May be null when no
   * authored name is on file — the template renders a neutral "Hi," in that
   * case rather than fabricating a placeholder ("Hi Owner," / "Hi there,").
   */
  contactName: string | null
  /**
   * Recipient business name from the entity record. Used for subject + body
   * context. Always present (entity.name is required).
   */
  businessName: string
  /**
   * The signed `/book?t=<token>` URL. Already absolute when APP_BASE_URL is
   * configured; relative when the build is misconfigured. Tracking pixels
   * and link rewriting are added by Resend server-side; we do not inject
   * our own.
   */
  bookingUrl: string
}

/**
 * HTML email sent by the admin "Send booking link" action.
 *
 * Voice: "we" — the SMD Services team is the speaker, never an individual
 * consultant. CLAUDE.md / Decision #20.
 *
 * Forbidden: response-time promises (specific business-day reply windows),
 * named consultants attributed as the speaker, uncontracted next-step
 * commitments (proposals, deliverables, post-call behavior). The body is
 * deliberately narrow — pick a time, link.
 */
export function bookingLinkInviteEmailHtml(input: BookingLinkInviteEmailInput): string {
  const businessName = escapeBookingHtml(input.businessName)
  const bookingUrl = escapeBookingHtml(input.bookingUrl)
  const greetingText = input.contactName ? `Hi ${escapeBookingHtml(input.contactName)},` : 'Hi,'
  return emailDocument({
    width: BOOKING_CARD_WIDTH,
    align: 'left',
    body: [
      paragraph(greetingText, '0 0 16px'),
      paragraph(
        `Following up on <strong>${businessName}</strong>. When it works for you, pick a time for a quick call so we can learn how things run and where you're trying to go.`,
        '0 0 16px'
      ),
      `      <p style="margin:24px 0;">
${actionButton(bookingUrl, 'Pick a time', '12px 28px')}
      </p>`,
      `      <p style="font-size:13px;color:#64748b;margin:0 0 16px;word-break:break-all;">
        Or paste this link into your browser:<br>
        <a href="${bookingUrl}" style="color:#1e40af;">${bookingUrl}</a>
      </p>`,
      `      <p style="font-size:13px;color:#64748b;margin:24px 0 0;">
        — ${BRAND_NAME}
      </p>`,
    ].join('\n'),
  })
}
