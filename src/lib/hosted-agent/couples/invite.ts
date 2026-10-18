/**
 * Send ONE couples partner's portal invitation (ADR 0090 decision 4).
 *
 * SMD sends its own welcome invitation email (src/lib/email/couples-templates.ts)
 * and Clerk sends nothing. Clerk Organization invitations have no parameter to
 * suppress Clerk's email (CreateOrganizationInvitationParams in @clerk/backend;
 * https://clerk.com/docs/reference/backend/organization/create-organization-invitation),
 * so this uses a Clerk APPLICATION invitation with `notify: false`, which is
 * documented to skip Clerk's email and returns the accept `url`
 * (https://clerk.com/docs/reference/backend/invitations/create-invitation).
 *
 *   1. The partner's local `users` row is pre-bound to the couple's entity
 *      (bindPartnerUser). That replaces the Organization membership the old
 *      org invitation granted: on first sign-in the bridge links the row by
 *      verified email, the entity resolves by `users.entity_id`, and the
 *      couples gate claims the partner row (src/lib/portal/couples-access.ts).
 *   2. A partner with no Clerk account gets an application invitation
 *      (notify: false); its `url` is the button in SMD's email. A partner who
 *      already has a Clerk account gets no invitation (Clerk refuses one for an
 *      existing address); the button is the portal, where they sign in.
 *   3. `resend` revokes the partner's previous invitation (an org invitation
 *      from the old flow, or an application invitation from this one) and
 *      issues a fresh one with SMD's email.
 *
 * Two callers, one behavior: the admin-console button
 * (/api/admin/hosted-agent/couples/:id/invite) and the SMD agent route
 * (/api/internal/agent/couples/:id/invite). The invitee's address is never
 * logged.
 */

import type { APIContext } from 'astro'
import type { D1Database } from '@cloudflare/workers-types'
import { env } from 'cloudflare:workers'
import { clerkClient } from '@clerk/astro/server'
import {
  bindPartnerUser,
  getCouplesInstance,
  getPersonById,
  markPersonInvited,
  partnerPublicView,
  type CouplesInstanceRow,
  type CouplesPersonRow,
} from '../../db/couples-onboarding'
import { getPortalBaseUrl } from '../../config/app-url'
import { captureError } from '../../observability/sentry'
import { COUPLES_LANDING_PATH } from '../../portal/couples-access'
import { normalizeEmail } from '../../identity/email'
import { sendEmail } from '../../email/resend'
import { couplesWelcomeEmail } from '../../email/couples-templates'

export type CouplesInviteResult =
  | 'invited'
  | 'resent'
  | 'already_member'
  | 'already_invited'
  | 'account_conflict'
  | 'invite_failed'
  | 'email_failed'
  | 'invalid'

/** Which surface sent the invitation; tags the error report, never the invitee. */
export type CouplesInviteCaller = 'admin' | 'agent'

const CAPTURE_TAG: Record<CouplesInviteCaller, string> = {
  admin: 'admin.couples.invite',
  agent: 'agent.couples.invite',
}

/** Replies to SMD's welcome invitation land with the team. */
export const COUPLES_INVITE_REPLY_TO = 'team@smd.services'

/** Clerk's id prefix for an Organization invitation (the pre-SMD-email flow). */
const ORG_INVITATION_PREFIX = 'orginv_'

export interface CouplesInviteOutcome {
  result: CouplesInviteResult
  /** Clerk invitation id, present when an invitation was created. */
  invitationId?: string
}

/** The local user's Clerk user id, or null when the row has none. */
export async function clerkUserIdFor(db: D1Database, userId: string): Promise<string | null> {
  const row = await db
    .prepare('SELECT clerk_user_id FROM users WHERE id = ?')
    .bind(userId)
    .first<{ clerk_user_id: string | null }>()
  return row?.clerk_user_id ?? null
}

type Clerk = ReturnType<typeof clerkClient>

/** Clerk's email filter is a partial match, so confirm an exact address. */
async function clerkAccountExists(clerk: Clerk, email: string): Promise<boolean> {
  const target = normalizeEmail(email)
  const list = await clerk.users.getUserList({ emailAddress: [email] })
  return list.data.some((u) =>
    u.emailAddresses.some((a) => normalizeEmail(a.emailAddress) === target)
  )
}

/** Best effort: a revoked, accepted, or expired invitation is simply gone. */
async function revokePrevious(
  clerk: Clerk,
  instance: CouplesInstanceRow,
  invitationId: string | null,
  requestingUserId: string
): Promise<void> {
  if (!invitationId) return
  try {
    if (invitationId.startsWith(ORG_INVITATION_PREFIX)) {
      if (!instance.clerk_org_id) return
      await clerk.organizations.revokeOrganizationInvitation({
        organizationId: instance.clerk_org_id,
        invitationId,
        requestingUserId,
      })
    } else {
      await clerk.invitations.revokeInvitation(invitationId)
    }
  } catch {
    // Nothing to revoke is the expected failure; the new invitation still goes.
  }
}

function mapClerkError(err: unknown): CouplesInviteResult {
  const message = err instanceof Error ? err.message : String(err)
  if (/already invited|duplicate/i.test(message)) return 'already_invited'
  return 'invite_failed'
}

async function sendWelcome(
  instance: CouplesInstanceRow,
  person: CouplesPersonRow,
  acceptUrl: string,
  existingAccount: boolean
): Promise<boolean> {
  const other = await partnerPublicView(env.DB, instance.id, person.id)
  const email = couplesWelcomeEmail({
    agentName: instance.agent_name,
    partnerName: person.name,
    otherPartnerName: other?.name ?? null,
    acceptUrl,
    existingAccount,
  })
  try {
    const sent = await sendEmail(env.RESEND_API_KEY, {
      to: person.email,
      subject: email.subject,
      html: email.html,
      text: email.text,
      reply_to: COUPLES_INVITE_REPLY_TO,
      tags: [{ name: 'kind', value: 'couples_invite' }],
    })
    return sent.success
  } catch {
    return false
  }
}

interface InviteInput {
  instanceId: string
  personId: string
  inviterClerkUserId: string
  source: CouplesInviteCaller
  /** Re-send to a partner already invited: revoke the old invitation, issue a new one. */
  resend?: boolean
}

type Issued =
  | { ok: true; invitationId: string | null; acceptUrl: string; existingAccount: boolean }
  | { ok: false; result: CouplesInviteResult; error?: unknown }

/**
 * The Clerk half: revoke the previous invitation on resend, then issue an
 * application invitation with Clerk's email suppressed, unless the partner
 * already has a Clerk account (Clerk refuses one; they sign in instead).
 */
async function issueInvitation(
  clerk: Clerk,
  instance: CouplesInstanceRow,
  person: CouplesPersonRow,
  opts: { resend: boolean; knownAccount: boolean; landing: string; requestingUserId: string }
): Promise<Issued> {
  try {
    if (opts.resend) {
      await revokePrevious(clerk, instance, person.clerk_invitation_id, opts.requestingUserId)
    }
    const existingAccount = opts.knownAccount || (await clerkAccountExists(clerk, person.email))
    if (existingAccount) {
      return { ok: true, invitationId: null, acceptUrl: opts.landing, existingAccount }
    }
    const invitation = await clerk.invitations.createInvitation({
      emailAddress: person.email,
      redirectUrl: opts.landing,
      notify: false,
      ...(opts.resend ? { ignoreExisting: true } : {}),
    })
    if (!invitation.url) {
      await revokePrevious(clerk, instance, invitation.id, opts.requestingUserId)
      return { ok: false, result: 'invite_failed', error: new Error('invitation has no url') }
    }
    return { ok: true, invitationId: invitation.id, acceptUrl: invitation.url, existingAccount }
  } catch (err) {
    return { ok: false, result: mapClerkError(err), error: err }
  }
}

export async function inviteCouplesPartner(
  context: APIContext,
  input: InviteInput
): Promise<CouplesInviteOutcome> {
  const instance = await getCouplesInstance(env.DB, input.instanceId)
  if (!instance) return { result: 'invalid' }
  const person = await getPersonById(env.DB, input.personId)
  if (!person || person.intake_id !== instance.id) return { result: 'invalid' }
  if (person.user_id) return { result: 'already_member' }
  const resend = input.resend === true
  if (person.invited_at && !resend) return { result: 'already_invited' }

  const report = (err: unknown, step: string): void => {
    console.error('[couples/invite] invitation failed', {
      caller: input.source,
      intake_id: instance.id,
      person: person.person_key,
      step,
    })
    captureError(err, CAPTURE_TAG[input.source])
  }

  const binding = await bindPartnerUser(env.DB, person, instance.entity_id)
  if (binding.kind === 'conflict') return { result: 'account_conflict' }

  const clerk = clerkClient(context)
  const issued = await issueInvitation(clerk, instance, person, {
    resend,
    knownAccount: binding.clerkUserId !== null,
    landing: `${getPortalBaseUrl(env) ?? 'https://portal.smd.services'}${COUPLES_LANDING_PATH}`,
    requestingUserId: input.inviterClerkUserId,
  })
  if (!issued.ok) {
    if (issued.result === 'invite_failed') report(issued.error, 'clerk')
    return { result: issued.result }
  }

  if (!(await sendWelcome(instance, person, issued.acceptUrl, issued.existingAccount))) {
    report(new Error('couples welcome email failed'), 'email')
    await revokePrevious(clerk, instance, issued.invitationId, input.inviterClerkUserId)
    return { result: 'email_failed' }
  }

  await markPersonInvited(env.DB, person.id, issued.invitationId)
  return {
    result: resend && person.invited_at ? 'resent' : 'invited',
    ...(issued.invitationId ? { invitationId: issued.invitationId } : {}),
  }
}
