/**
 * Email templates for follow-up cadence emails.
 *
 * All templates use "we" voice per Decision #20.
 * Templates produce self-contained HTML emails with inline styles.
 * No external CSS or image dependencies.
 */
import { BRAND_NAME } from '../config/brand'

export interface FollowUpEmailData {
  clientName: string
  businessName: string
  portalUrl: string
}

export interface FollowUpEmail {
  subject: string
  html: string
}

/**
 * Shared email wrapper for consistent styling.
 */
function emailWrapper(body: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:480px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 24px;text-align:center;">${BRAND_NAME}</h1>
${body}
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} ${BRAND_NAME} &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

/**
 * Proposal Day 2 — Confirm receipt. Short, no pressure.
 * Per Decision #19: "We sent over the scope yesterday — wanted to make sure it landed."
 */
export function proposalDay2Email(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        We sent over the scope for ${data.businessName} recently and wanted to make sure it landed. You can review it anytime in your portal.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        No rush — just let us know if you have any questions.
      </p>
      <div style="text-align:center;">
        <a href="${data.portalUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 32px;border-radius:6px;">
          View Your Proposal
        </a>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        If you have any questions, reply directly to this email.
      </p>`

  return {
    subject: `Following up on your proposal — ${data.businessName}`,
    html: emailWrapper(body),
  }
}

/**
 * Proposal Day 5 — Value add. One specific observation from the call.
 * Per Decision #19: Shows the team was already working the problem.
 */
export function proposalDay5Email(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        We've been thinking about what we discussed during our conversation about ${data.businessName}. The challenges you described are common at your stage of growth, and we're confident the plan we outlined will make a real difference.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your proposal is still available in your portal. We'd love to get started whenever the timing works for you.
      </p>
      <div style="text-align:center;">
        <a href="${data.portalUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 32px;border-radius:6px;">
          Review Proposal
        </a>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        If you have any questions, reply directly to this email.
      </p>`

  return {
    subject: `Thinking about ${data.businessName}`,
    html: emailWrapper(body),
  }
}

/**
 * Proposal Day 7 — Soft deadline. Offer to reschedule. No pressure.
 * Per Decision #19: Reference slot hold, clean yes or reschedule.
 */
export function proposalDay7Email(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        We wanted to check in one more time about the proposal for ${data.businessName}. We've been holding a slot in our schedule, and we want to make sure we're being respectful of your time.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        If the timing isn't right, no problem at all — just let us know and we can revisit down the road. If you're ready to move forward, we're here to get started.
      </p>
      <div style="text-align:center;">
        <a href="${data.portalUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 32px;border-radius:6px;">
          View Proposal
        </a>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        Reply to this email anytime — we're happy to chat.
      </p>`

  return {
    subject: `Checking in — ${data.businessName} proposal`,
    html: emailWrapper(body),
  }
}

/**
 * Review request — 2 days post-handoff.
 * Per Decision #26: Google review link + LinkedIn, framed as easy and appreciated.
 */
export function reviewRequestEmail(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        We really enjoyed working with you and the team at ${data.businessName}. Now that things are up and running, we'd genuinely appreciate it if you could leave us a quick review.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Either a Google review or a LinkedIn recommendation works great — whichever is easier for you. It means a lot to a small team like ours.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Your portal is always available if you need to reference anything from the engagement.
      </p>
      <div style="text-align:center;">
        <a href="${data.portalUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 32px;border-radius:6px;">
          Visit Your Portal
        </a>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        Thank you for trusting us with ${data.businessName}.
      </p>`

  return {
    subject: `It was great working with ${data.businessName}`,
    html: emailWrapper(body),
  }
}

/**
 * Referral ask — at handoff.
 * Per Decision #23: Explicit ask, no formal incentive. Easy introduction.
 */
export function referralAskEmail(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Now that we've wrapped up the engagement for ${data.businessName}, we wanted to say thanks for being great to work with.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        If you know another business owner dealing with the same kind of operational growing pains, we'd genuinely appreciate the introduction. We make it easy for them — it starts with a conversation, no commitment.
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Just reply to this email with their name and we'll take it from there.
      </p>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        Thank you for choosing ${BRAND_NAME}.
      </p>`

  return {
    subject: `Know someone who could use the same help?`,
    html: emailWrapper(body),
  }
}

/**
 * Safety net check-in — handoff + 7 days.
 * Quick check that everything is holding up in real-world use.
 */
export function safetyNetCheckinEmail(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        We wanted to check in and see how things are going at ${data.businessName} since we wrapped up. Has the team settled into the new workflows?
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        If anything needs adjusting or questions have come up, we're still here.
        Reply to this email or reach out anytime.
      </p>
      <div style="text-align:center;">
        <a href="${data.portalUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 32px;border-radius:6px;">
          Visit Your Portal
        </a>
      </div>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        We're here if you need us.
      </p>`

  return {
    subject: `How's everything going at ${data.businessName}?`,
    html: emailWrapper(body),
  }
}

/**
 * 30-day feedback survey — handoff + 30 days.
 * Per Decision #29: 4 questions, question 4 doubles as referral prompt.
 */
export function feedback30DayEmail(data: FollowUpEmailData): FollowUpEmail {
  const body = `      <p style="font-size:15px;color:#334155;margin:0 0 8px;">
        Hi${data.clientName ? ` ${data.clientName}` : ''},
      </p>
      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        It's been about a month since we wrapped up at ${data.businessName}, and we'd love to hear how things are going. A few quick questions:
      </p>
      <ol style="font-size:15px;color:#334155;margin:0 0 16px;padding-left:20px;">
        <li style="margin-bottom:8px;">Are the solutions we built still being used day-to-day?</li>
        <li style="margin-bottom:8px;">What's the most noticeable change in how your team operates?</li>
        <li style="margin-bottom:8px;">Any friction points that came up after we left?</li>
        <li style="margin-bottom:8px;">Would you refer us to another business owner? If so, who comes to mind?</li>
      </ol>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Just reply to this email — a few sentences is all we need. Your feedback helps us get better, and we genuinely appreciate it.
      </p>
      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;text-align:center;">
        Thank you for working with ${BRAND_NAME}.
      </p>`

  return {
    subject: `30-day check-in — how's ${data.businessName} doing?`,
    html: emailWrapper(body),
  }
}

/**
 * Get the email template function for a given follow-up type.
 */
export function getFollowUpTemplate(
  type: string
): ((data: FollowUpEmailData) => FollowUpEmail) | null {
  const templates: Record<string, (data: FollowUpEmailData) => FollowUpEmail> = {
    proposal_day2: proposalDay2Email,
    proposal_day5: proposalDay5Email,
    proposal_day7: proposalDay7Email,
    review_request: reviewRequestEmail,
    referral_ask: referralAskEmail,
    safety_net_checkin: safetyNetCheckinEmail,
    feedback_30day: feedback30DayEmail,
  }

  return templates[type] ?? null
}
