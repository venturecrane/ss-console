/**
 * The couples partner welcome invitation (ADR 0090 decision 4).
 *
 * SMD sends this itself, in place of Clerk's bare "you've been invited to
 * join <org>" email, which the first real invitees could not make sense of.
 * Every variable is authored data: the agent name SMD authored on the
 * instance, and the two partners' names SMD authored on their rows. Nothing
 * here promises a date, a duration of service, or a price.
 */

import {
  actionButton,
  escapeEmailHtml,
  greeting,
  mutedParagraph,
  paragraph,
  portalDocument,
} from './templates'

export interface CouplesWelcomeInput {
  /** The instance's authored agent name; null renders a generic line. */
  agentName: string | null
  /** The invitee's own authored name. */
  partnerName: string
  /** The other partner's authored name; null renders "your partner". */
  otherPartnerName: string | null
  /** Where the button goes: a Clerk invitation URL, or the portal sign-in. */
  acceptUrl: string
  /**
   * True when the invitee already has an account (no invitation was created):
   * step one says sign in rather than create an account.
   */
  existingAccount: boolean
}

export interface CouplesWelcomeEmail {
  subject: string
  html: string
  text: string
}

/** First word of an authored name: "Alex Rivera" greets as "Alex". */
export function firstName(name: string | null): string | null {
  const first = (name ?? '').trim().split(/\s+/)[0]
  return first ? first : null
}

function lines(input: CouplesWelcomeInput) {
  const agent = input.agentName?.trim() || null
  const other = firstName(input.otherPartnerName) ?? 'your partner'
  const what = agent
    ? `${agent} is a private assistant the two of you share. It works by email for now, and helps with your shared calendar, plans, travel, ideas and check-ins.`
    : 'You are invited to a private assistant the two of you share. It works by email for now, and helps with your shared calendar, plans, travel, ideas and check-ins.'
  return {
    subject: agent
      ? `${agent}: your invitation to set up your couples assistant`
      : 'Your invitation to set up your couples assistant',
    greetingName: firstName(input.partnerName) ?? '',
    what,
    privacy: `It is private by default. Nothing you tell it alone is shared with ${other}.`,
    steps: [
      input.existingAccount
        ? 'Click the button below and sign in with this email address.'
        : 'Click the button below and create your account with this email address.',
      'Confirm your details and consent. It takes a couple of minutes.',
      `You and ${other} each confirm the shared settings. Nothing turns on until you both have.`,
    ],
    button: 'Set up your assistant',
    questions: 'Questions? Reply to this email.',
  }
}

export function couplesWelcomeEmail(input: CouplesWelcomeInput): CouplesWelcomeEmail {
  const l = lines(input)
  const steps = l.steps
    .map((s) => `          <li style="margin:0 0 8px;">${escapeEmailHtml(s)}</li>`)
    .join('\n')
  const html = portalDocument(
    [
      paragraph(greeting(l.greetingName), '0 0 16px'),
      paragraph(escapeEmailHtml(l.what), '0 0 12px'),
      paragraph(escapeEmailHtml(l.privacy), '0 0 20px'),
      paragraph('Here is what to do:', '0 0 8px'),
      `      <ol style="font-size:15px;color:#334155;margin:0 0 24px;padding-left:20px;text-align:left;">
${steps}
      </ol>`,
      actionButton(input.acceptUrl, l.button, '14px 36px'),
      mutedParagraph(l.questions),
    ].join('\n')
  )
  const text = [
    l.greetingName ? `Hi ${l.greetingName},` : 'Hi,',
    '',
    l.what,
    '',
    l.privacy,
    '',
    'Here is what to do:',
    ...l.steps.map((s, i) => `${i + 1}. ${s}`),
    '',
    `${l.button}: ${input.acceptUrl}`,
    '',
    l.questions,
  ].join('\n')
  return { subject: l.subject, html, text }
}
