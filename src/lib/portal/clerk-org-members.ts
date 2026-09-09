/**
 * Clerk Organization member + pending-invitation reader.
 *
 * The Users page (`/portal/products/operator/settings/users`)
 * displays one row per local users record that has a granted role.
 * Without this helper, members who have been invited but not yet
 * signed in (and therefore haven't JIT-created their local users row
 * via the bridge — see `clerk-bridge.ts`) are invisible. Principals
 * see a stale picture of "who's on this account": invited members
 * disappear into limbo until first sign-in.
 *
 * This module reads the per-organization membership list AND the
 * pending-invitation list from Clerk's Backend API, normalizes both
 * into a shared shape, and merges them. The Users page renders the
 * merged list so principals can see invited-but-not-signed-in members
 * alongside fully-onboarded ones.
 *
 * What this does NOT do: it does not grant product roles to invitees
 * before they sign in. Product roles (principal / operator /
 * compliance) are stored in `product_roles` keyed by local users.id,
 * which doesn't exist until the JIT bridge creates the row. Granting
 * roles pre-sign-in is a future slice (carried in the invite's
 * private_metadata, applied by a Clerk webhook on first sign-in).
 *
 * Errors from Clerk (network, auth) are caught and converted to an
 * empty list. The Users page falls back gracefully — the local
 * member list still renders. A console error records the failure so
 * tail-log alerting can surface it.
 */

import type { APIContext } from 'astro'
import { clerkClient } from '@clerk/astro/server'
import { normalizeEmail } from '../identity/email'

export interface ClerkOrgMember {
  kind: 'member'
  email: string
  name: string
  clerkUserId: string
  role: string
  joinedAt: string | null
  invitedAt: null
  expiresAt: null
}

export interface ClerkOrgPendingInvite {
  kind: 'pending_invite'
  email: string
  name: ''
  clerkUserId: null
  role: string
  joinedAt: null
  invitedAt: string | null
  expiresAt: string | null
}

/**
 * Page-cap for the Clerk API call. The principal-managed customer
 * size is small (< 50 paralegals + compliance) so a single page is
 * enough to render the whole list. If a customer ever exceeds this
 * we surface the truncation in the UI (the page checks the result
 * shape's `truncated` flag).
 */
const CLERK_ORG_PARTICIPANT_PAGE_LIMIT = 100

export interface ClerkOrgParticipantsResult {
  members: readonly ClerkOrgMember[]
  pendingInvites: readonly ClerkOrgPendingInvite[]
  /**
   * True when the Clerk-side member or invitation list exceeded
   * CLERK_ORG_PARTICIPANT_PAGE_LIMIT and the result is incomplete.
   * The Users page renders a small notice when set so principals
   * know they're seeing a partial list.
   */
  truncated: boolean
  /**
   * True when the Clerk API call failed entirely. The page renders
   * the local member list only and surfaces a small notice that
   * pending invitations couldn't be loaded. Differentiates a clean
   * "no pending invites" empty state from "we couldn't reach Clerk".
   */
  failed: boolean
}

/**
 * Normalize a single Clerk OrganizationMembership into our shape.
 * Exported so unit tests can exercise the projection without a
 * Clerk mock.
 */
export function projectClerkMember(input: {
  publicUserData?: {
    userId?: string | null
    identifier?: string | null
    firstName?: string | null
    lastName?: string | null
  } | null
  role: string
  createdAt: number | null
}): ClerkOrgMember | null {
  const userId = input.publicUserData?.userId ?? null
  const email = input.publicUserData?.identifier ?? null
  if (userId === null || email === null) return null
  const first = input.publicUserData?.firstName ?? ''
  const last = input.publicUserData?.lastName ?? ''
  const name = `${first} ${last}`.trim()
  return {
    kind: 'member',
    email,
    name,
    clerkUserId: userId,
    role: input.role,
    joinedAt: input.createdAt !== null ? new Date(input.createdAt).toISOString() : null,
    invitedAt: null,
    expiresAt: null,
  }
}

/**
 * Normalize a Clerk OrganizationInvitation into our shape. Exported
 * for unit tests. Only invitations whose `status === 'pending'` are
 * surfaced — accepted invitations are already represented as
 * members, and revoked / expired invitations are noise.
 */
export function projectClerkPendingInvite(input: {
  emailAddress: string
  role: string
  status?: string | null
  createdAt: number | null
  expiresAt: number | null
}): ClerkOrgPendingInvite | null {
  if (input.status !== 'pending') return null
  return {
    kind: 'pending_invite',
    email: input.emailAddress,
    name: '',
    clerkUserId: null,
    role: input.role,
    joinedAt: null,
    invitedAt: input.createdAt !== null ? new Date(input.createdAt).toISOString() : null,
    expiresAt: input.expiresAt !== null ? new Date(input.expiresAt).toISOString() : null,
  }
}

/**
 * Drop pending invitations whose email matches an already-joined
 * member. Clerk leaves the invitation row in the "accepted" status
 * after acceptance, but a fresh "pending" invitation for the same
 * email is possible if the principal re-invites someone who left
 * and came back; the joined record always wins to avoid a confusing
 * double-row in the UI.
 */
export function dedupePendingAgainstMembers(
  pending: readonly ClerkOrgPendingInvite[],
  members: readonly ClerkOrgMember[]
): ClerkOrgPendingInvite[] {
  const joinedEmails = new Set(members.map((m) => normalizeEmail(m.email)))
  return pending.filter((inv) => !joinedEmails.has(normalizeEmail(inv.email)))
}

/**
 * Read the org's member list + pending-invitation list and merge them.
 * Network and auth errors are caught — the function never throws.
 * Callers see a `failed: true` result instead so the Users page can
 * keep rendering the local member list.
 */
export async function listClerkOrgParticipants(
  context: APIContext,
  clerkOrgId: string
): Promise<ClerkOrgParticipantsResult> {
  try {
    const client = clerkClient(context)

    const [membershipPage, invitationPage] = await Promise.all([
      client.organizations.getOrganizationMembershipList({
        organizationId: clerkOrgId,
        limit: CLERK_ORG_PARTICIPANT_PAGE_LIMIT,
      }),
      client.organizations.getOrganizationInvitationList({
        organizationId: clerkOrgId,
        status: ['pending'],
        limit: CLERK_ORG_PARTICIPANT_PAGE_LIMIT,
      }),
    ])

    const members: ClerkOrgMember[] = []
    for (const m of membershipPage.data) {
      const projected = projectClerkMember({
        publicUserData: m.publicUserData ?? null,
        role: m.role,
        createdAt: m.createdAt ?? null,
      })
      if (projected !== null) members.push(projected)
    }

    const pendingRaw: ClerkOrgPendingInvite[] = []
    for (const inv of invitationPage.data) {
      const projected = projectClerkPendingInvite({
        emailAddress: inv.emailAddress,
        role: inv.role,
        status: inv.status ?? null,
        createdAt: inv.createdAt ?? null,
        expiresAt: inv.expiresAt ?? null,
      })
      if (projected !== null) pendingRaw.push(projected)
    }
    const pendingInvites = dedupePendingAgainstMembers(pendingRaw, members)

    const truncated =
      membershipPage.totalCount > CLERK_ORG_PARTICIPANT_PAGE_LIMIT ||
      invitationPage.totalCount > CLERK_ORG_PARTICIPANT_PAGE_LIMIT

    return { members, pendingInvites, truncated, failed: false }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error('Clerk org participants fetch failed', { clerkOrgId, message })
    return { members: [], pendingInvites: [], truncated: false, failed: true }
  }
}
