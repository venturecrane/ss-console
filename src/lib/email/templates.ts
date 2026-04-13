/**
 * Email templates for magic link, portal invitation, and booking confirmations.
 *
 * All templates produce self-contained HTML emails with inline styles.
 * No external CSS or image dependencies.
 */

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
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${clientName ? ` ${clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Click the button below to sign in to your portal.
      </p>

      <a href="${magicLinkUrl}"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:12px 32px;border-radius:6px;">
        Sign in to Portal
      </a>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        This link expires in 15 minutes and can only be used once.
      </p>
      <p style="font-size:12px;color:#94a3b8;margin:8px 0 0;">
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

/**
 * Email sent when a quote/proposal is sent to a client via the portal.
 * Links them to the portal to review their proposal.
 */
export function quoteSentEmailHtml(clientName: string, portalUrl: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${clientName ? ` ${clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your proposal from SMD Services is ready for review. Sign in to your portal to view the details.
      </p>

      <a href="${portalUrl}"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:12px 32px;border-radius:6px;">
        View Your Proposal
      </a>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        If you have any questions, reply directly to this email.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

/**
 * Email sent when an admin first sends a quote to a client (portal invitation).
 */
/**
 * Email sent when an invoice is sent to a client via the portal.
 * Links them to the portal to view and pay the invoice.
 */
export function invoiceSentEmailHtml(
  clientName: string,
  amount: string,
  portalUrl: string
): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${clientName ? ` ${clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your invoice from SMD Services for ${amount} is ready. Sign in to your portal to view the details and make a payment.
      </p>

      <a href="${portalUrl}"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:12px 32px;border-radius:6px;">
        View Invoice
      </a>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        If you have any questions, reply directly to this email.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

/**
 * Email sent when a payment is received for an invoice.
 */
export function paymentConfirmationEmailHtml(clientName: string, amount: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${clientName ? ` ${clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        We've received your payment of ${amount}. Thank you!
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        If you have any questions about your engagement, our team is here to help.
      </p>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        If you have any questions, reply directly to this email.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

/**
 * Email sent when an admin first sends a quote to a client (portal invitation).
 */
/**
 * Email sent with the scorecard PDF report after assessment completion.
 */
export function scorecardReportEmailHtml(
  firstName: string,
  overallScore: number,
  overallDisplayLabel: string
): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Operations Health Scorecard</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${firstName ? ` ${firstName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your Operations Health Report is attached. You scored <strong>${overallScore}</strong> out of 100 (${overallDisplayLabel}).
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        The report breaks down how you scored across 6 areas of your operations, with specific observations based on your answers. Take a look and see what stands out.
      </p>

      <a href="https://smd.services/book"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:12px 32px;border-radius:6px;">
        Book an Assessment Call
      </a>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        The real value comes from a conversation. We walk through your day together and figure out exactly what to focus on first.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

export function portalInvitationEmailHtml(clientName: string, magicLinkUrl: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${clientName ? ` ${clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        We've prepared a proposal for you. Your client portal is ready — click below to sign in and review it.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your portal is where you'll find your proposal, project updates, and everything related to our work together.
      </p>

      <a href="${magicLinkUrl}"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:12px 32px;border-radius:6px;">
        View Your Proposal
      </a>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        This link expires in 15 minutes. You can request a new link anytime from the portal login page.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

// ===========================================================================
// Portal welcome email (SOW signed — user provisioned)
// ===========================================================================

/**
 * Welcome email sent when a client's portal user is provisioned after SOW signing.
 *
 * Unlike portalInvitationEmailHtml (which embeds a 15-minute magic link),
 * this links to the portal login page with the client's email pre-filled.
 * The client clicks "Send sign-in link" to get a fresh token on demand.
 * No expiry concern — the email stays valid indefinitely.
 */
export function portalWelcomeEmailHtml(clientName: string, loginUrl: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;text-align:center;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">SMD Services</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">Client Portal</p>

      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${clientName ? ` ${clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Your client portal is ready. This is where you'll find your project details, milestones, invoices, and everything related to our work together.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Click below to sign in. We'll send a secure link to your email.
      </p>

      <a href="${loginUrl}"
         style="display:inline-block;background-color:#1e40af;color:#ffffff;
                font-size:14px;font-weight:600;text-decoration:none;
                padding:12px 32px;border-radius:6px;">
        Sign In to Your Portal
      </a>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        You can access your portal anytime at portal.smd.services
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
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
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
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
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">You're booked.</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">SMD Services &middot; ${meetingLabel}</p>

      <p style="font-size:15px;color:#334155;margin:0 0 16px;">Hi ${guestName},</p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Your assessment call for <strong>${businessName}</strong> is confirmed.
      </p>

      <div style="background:#f1f5f9;border-radius:6px;padding:16px;margin:0 0 24px;">
        <p style="font-size:13px;color:#64748b;margin:0 0 4px;text-transform:uppercase;letter-spacing:0.05em;">When</p>
        <p style="font-size:16px;color:#0f172a;font-weight:600;margin:0;">${slotLabel}</p>
      </div>

      ${
        input.meetUrl
          ? `<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:16px;margin:0 0 24px;">
              <p style="font-size:13px;color:#1e40af;margin:0 0 8px;font-weight:600;">Join the call</p>
              <a href="${escapeBookingHtml(input.meetUrl)}" style="font-size:14px;color:#1e40af;word-break:break-all;">${escapeBookingHtml(input.meetUrl)}</a>
            </div>`
          : ''
      }

      <p style="font-size:14px;color:#334155;margin:0 0 8px;">
        Need to reschedule or cancel?
      </p>
      <p style="font-size:14px;color:#334155;margin:0 0 24px;">
        <a href="${manageUrl}" style="color:#1e40af;">Manage your booking →</a>
      </p>

      <p style="font-size:13px;color:#64748b;margin:0;">
        Looking forward to talking,<br>
        Scott Durgan &middot; SMD Services
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
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
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">Your call has been rescheduled.</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">SMD Services &middot; ${meetingLabel}</p>

      <p style="font-size:15px;color:#334155;margin:0 0 16px;">Hi ${guestName},</p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Your assessment call for <strong>${businessName}</strong> has moved.
      </p>

      <div style="background:#f1f5f9;border-radius:6px;padding:16px;margin:0 0 16px;">
        <p style="font-size:12px;color:#94a3b8;margin:0 0 4px;text-decoration:line-through;">${oldSlot}</p>
        <p style="font-size:16px;color:#0f172a;font-weight:600;margin:0;">${newSlot}</p>
      </div>

      ${
        input.meetUrl
          ? `<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;padding:16px;margin:0 0 24px;">
              <p style="font-size:13px;color:#1e40af;margin:0 0 8px;font-weight:600;">Join the call</p>
              <a href="${escapeBookingHtml(input.meetUrl)}" style="font-size:14px;color:#1e40af;word-break:break-all;">${escapeBookingHtml(input.meetUrl)}</a>
            </div>`
          : ''
      }

      <p style="font-size:13px;color:#64748b;margin:0 0 16px;">
        <strong>Heads up:</strong> if you use Outlook or Apple Calendar, you may see
        both the old and new entry side-by-side. You can safely remove the old one.
      </p>

      <p style="font-size:14px;color:#334155;margin:0;">
        Need to make another change? <a href="${manageUrl}" style="color:#1e40af;">Manage your booking →</a>
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
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
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">Your call has been cancelled.</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">SMD Services</p>

      <p style="font-size:15px;color:#334155;margin:0 0 16px;">Hi ${guestName},</p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Your assessment call for <strong>${businessName}</strong>
        scheduled for <strong>${slotLabel}</strong> has been cancelled.
      </p>

      <p style="font-size:14px;color:#334155;margin:0 0 24px;">
        Whenever you're ready, you can <a href="${rebookUrl}" style="color:#1e40af;">book a new time →</a>
      </p>

      <p style="font-size:13px;color:#64748b;margin:0;">
        — Scott Durgan &middot; SMD Services
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} SMD Services &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}
