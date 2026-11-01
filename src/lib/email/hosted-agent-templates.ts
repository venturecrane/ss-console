/**
 * Self-serve Operator transactional emails (ADR 0067 offering; ADR 0100: one
 * product, so the buyer reads "Operator", never a second product name). Separate module (rather
 * than templates.ts) to respect that file's 500-line ceiling; composes the
 * same shared portal shell helpers so the emails render identically.
 */

import {
  actionButton,
  detailPanel,
  escapeEmailHtml,
  greeting,
  mutedParagraph,
  paragraph,
  portalDocument,
} from './templates'

/**
 * Sent to the buyer when their self-serve checkout completes. Status
 * framing only: no timeline promises, no outreach promises (Pattern A
 * guards). The portal is the single place setup progresses.
 */
export function hostedAgentWelcomeEmailHtml(clientName: string, portalUrl: string): string {
  return portalDocument(
    [
      paragraph(greeting(clientName), '0 0 8px'),
      paragraph('Your Operator subscription is active. Setup is underway.', '0 0 8px'),
      paragraph(
        'Next step: complete the short setup questionnaire in your portal. It covers what to call your agent, what you want it working on, and which senders it should accept email from.'
      ),
      actionButton(portalUrl, 'Complete Setup'),
      mutedParagraph(
        'Your portal shows setup status at every step. Questions any time: reply to this email.'
      ),
    ].join('\n')
  )
}

export interface HostedAgentOrderNotificationInput {
  entityName: string
  buyerEmail: string
  plan: 'founding' | 'standard'
  entityId: string
  adminQueueUrl: string
}

/** Operational alert to team@ when a self-serve purchase lands. */
export function hostedAgentOrderNotificationEmailHtml(
  input: HostedAgentOrderNotificationInput
): string {
  return portalDocument(
    [
      paragraph('A self-serve Operator subscription was purchased.', '0 0 8px'),
      detailPanel('Customer', escapeEmailHtml(input.entityName)),
      detailPanel('Buyer email', escapeEmailHtml(input.buyerEmail)),
      detailPanel('Plan', input.plan === 'founding' ? 'Founding ($49/mo)' : 'Standard ($79/mo)'),
      detailPanel('Entity', escapeEmailHtml(input.entityId)),
      paragraph('The concierge work item is in the admin queue.'),
      actionButton(input.adminQueueUrl, 'Open the Queue'),
    ].join('\n')
  )
}

export interface HostedAgentIntakeNotificationInput {
  entityName: string
  agentName: string | null
  adminQueueUrl: string
}

/** Operational alert to team@ when a customer submits the setup
 * questionnaire — the "ready to provision" baton. */
export function hostedAgentIntakeNotificationEmailHtml(
  input: HostedAgentIntakeNotificationInput
): string {
  return portalDocument(
    [
      paragraph('A self-serve setup questionnaire was submitted.', '0 0 8px'),
      detailPanel('Customer', escapeEmailHtml(input.entityName)),
      detailPanel('Agent name', escapeEmailHtml(input.agentName ?? 'not provided')),
      paragraph('Next concierge steps: assign the customer slug, then provision.'),
      actionButton(input.adminQueueUrl, 'Open the Queue'),
    ].join('\n')
  )
}

/**
 * Sent by the Captain's activate action when the agent goes live. Channel
 * details are authored per customer in the admin activate form, never
 * templated: this wrapper renders exactly what was authored.
 */
export function hostedAgentLiveEmailHtml(
  clientName: string,
  authoredChannelDetailsHtml: string,
  portalUrl: string
): string {
  return portalDocument(
    [
      paragraph(greeting(clientName), '0 0 8px'),
      paragraph('Your agent is live.', '0 0 8px'),
      paragraph(authoredChannelDetailsHtml),
      actionButton(portalUrl, 'Open Your Portal'),
      mutedParagraph('Your portal shows your agent status and settings.'),
    ].join('\n')
  )
}
