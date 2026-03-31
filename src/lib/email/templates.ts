/**
 * Email templates for magic link and portal invitation emails.
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
