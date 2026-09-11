/**
 * Trusted profile extraction from a Clerk user.
 *
 * The email returned here feeds ensureLocalUser (clerk-bridge.ts), whose
 * auto-link path binds a Clerk identity to a pre-existing local users row
 * purely by email match. That makes the email a trust anchor: an address
 * that Clerk has NOT verified must never reach the auto-link path, or an
 * attacker-added secondary address could bind their Clerk identity to
 * another person's client seat (invoices, SOWs, documents).
 *
 * Policy: only Clerk's verified PRIMARY email is trusted. Anything else —
 * missing primary, unverified primary — yields email: null, and the bridge
 * refuses to auto-link or JIT-create (2026-08-14 code-review finding; the
 * previous fallback to emailAddresses[0] contradicted the bridge's own
 * "verified primary is the trust anchor" contract).
 *
 * Typed structurally (not against @clerk/astro's User) so the bridge and
 * tests stay decoupled from Clerk's SDK types; the real Clerk user object
 * satisfies this shape.
 */

export interface ClerkEmailLike {
  emailAddress: string
  verification?: { status?: string | null } | null
}

export interface ClerkUserLike {
  primaryEmailAddress?: ClerkEmailLike | null
  firstName?: string | null
  lastName?: string | null
  username?: string | null
}

/** The verified primary email address, or null when there is no trusted email. */
export function verifiedPrimaryEmail(clerkUser: ClerkUserLike): string | null {
  const primary = clerkUser.primaryEmailAddress
  if (!primary?.emailAddress) return null
  return primary.verification?.status === 'verified' ? primary.emailAddress : null
}

/**
 * Extract the email + display name we persist locally from a Clerk user.
 * email is null when Clerk holds no verified primary address — callers
 * pass it through to ensureLocalUser, which then refuses email-based
 * linking and JIT creation.
 */
export function clerkProfile(clerkUser: ClerkUserLike): { email: string | null; name: string } {
  const email = verifiedPrimaryEmail(clerkUser)
  const name =
    [clerkUser.firstName, clerkUser.lastName].filter(Boolean).join(' ').trim() ||
    clerkUser.username ||
    (email ?? '')
  return { email, name }
}
