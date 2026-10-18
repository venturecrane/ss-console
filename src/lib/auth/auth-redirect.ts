/**
 * Where an unauthenticated page request is sent, and which `redirect_url`
 * values the auth pages will honor.
 *
 * Why this exists: a Clerk Organization invitation email links through Clerk
 * to our redirect URL (for example portal.smd.services/portal/products/
 * hosted-agent/couples) with `__clerk_ticket` and `__clerk_status` appended.
 * The invitee is not signed in, so the portal auth gate bounces them. That
 * bounce used to be a bare `/auth/sign-in`, which dropped the ticket: the
 * invitee landed on a sign-in form with no account and nothing to accept.
 *
 * The ticket is what lets Clerk's <SignUp>/<SignIn> components accept the
 * invitation, and they read it from the page URL, so it must arrive intact.
 */

export const CLERK_TICKET_PARAM = '__clerk_ticket'
export const CLERK_STATUS_PARAM = '__clerk_status'

/**
 * A same-origin absolute path, or null. Rejects protocol-relative (`//x`),
 * backslash tricks (`/\x`, which some browsers normalize to `//x`), absolute
 * URLs, and anything carrying control characters or whitespace.
 */
export function safeRedirectPath(value: string | null | undefined): string | null {
  if (!value) return null
  if (!value.startsWith('/')) return null
  if (value.startsWith('//') || value.startsWith('/\\')) return null
  if (value.includes('\\')) return null
  // eslint-disable-next-line no-control-regex
  if (/[\u0000- \u007f]/.test(value)) return null
  return value
}

/**
 * The location an unauthenticated page request is redirected to.
 *
 * With a Clerk ticket: `/auth/sign-up` when the status is `sign_up` or absent,
 * `/auth/sign-in` otherwise (`sign_in`, `complete`), carrying the ticket and
 * status unchanged plus `redirect_url=<the page they were going to>`.
 *
 * Without a ticket: `/auth/sign-in?redirect_url=<the page they were going to>`.
 */
export function unauthenticatedRedirectLocation(url: URL, pathname: string): string {
  const params = new URLSearchParams()
  const ticket = url.searchParams.get(CLERK_TICKET_PARAM)
  const status = url.searchParams.get(CLERK_STATUS_PARAM)
  const returnPath = safeRedirectPath(pathname)

  let target = '/auth/sign-in'
  if (ticket) {
    target = status === null || status === 'sign_up' ? '/auth/sign-up' : '/auth/sign-in'
    params.set(CLERK_TICKET_PARAM, ticket)
    if (status !== null) params.set(CLERK_STATUS_PARAM, status)
  }
  if (returnPath && returnPath !== '/') params.set('redirect_url', returnPath)

  const query = params.toString()
  return query ? `${target}?${query}` : target
}

/**
 * The post-sign-in landing path to honor for a surface, or null. Only a safe
 * same-origin path under that surface's own prefix is honored, so a
 * user-supplied value can never move someone to a different surface or host.
 */
export function honoredReturnPath(
  value: string | null | undefined,
  surface: 'admin' | 'portal'
): string | null {
  const path = safeRedirectPath(value)
  if (!path) return null
  const prefix = surface === 'portal' ? '/portal' : '/admin'
  if (path !== prefix && !path.startsWith(`${prefix}/`) && !path.startsWith(`${prefix}?`)) {
    return null
  }
  return path
}
