/**
 * The auth gate's bounce must keep a Clerk invitation ticket, or an invitee
 * lands on a bare sign-in form with nothing to accept (src/lib/auth/auth-redirect.ts).
 */
import { describe, expect, it } from 'vitest'
import {
  honoredReturnPath,
  safeRedirectPath,
  unauthenticatedRedirectLocation,
} from '../src/lib/auth/auth-redirect'

function loc(href: string): URL {
  const url = new URL(href)
  return new URL(unauthenticatedRedirectLocation(url, url.pathname), url.origin)
}

describe('unauthenticatedRedirectLocation', () => {
  const couples = 'https://portal.smd.services/portal/products/hosted-agent/couples'

  it('ticket + sign_up goes to /auth/sign-up with ticket, status and redirect_url', () => {
    const l = loc(`${couples}?__clerk_ticket=eyJ.abc-_~&__clerk_status=sign_up`)
    expect(l.pathname).toBe('/auth/sign-up')
    expect(l.searchParams.get('__clerk_ticket')).toBe('eyJ.abc-_~')
    expect(l.searchParams.get('__clerk_status')).toBe('sign_up')
    expect(l.searchParams.get('redirect_url')).toBe('/portal/products/hosted-agent/couples')
  })

  it('ticket with no status goes to /auth/sign-up and adds no status', () => {
    const l = loc(`${couples}?__clerk_ticket=t1`)
    expect(l.pathname).toBe('/auth/sign-up')
    expect(l.searchParams.get('__clerk_ticket')).toBe('t1')
    expect(l.searchParams.has('__clerk_status')).toBe(false)
  })

  it('ticket + sign_in goes to /auth/sign-in', () => {
    const l = loc(`${couples}?__clerk_ticket=t2&__clerk_status=sign_in`)
    expect(l.pathname).toBe('/auth/sign-in')
    expect(l.searchParams.get('__clerk_ticket')).toBe('t2')
    expect(l.searchParams.get('__clerk_status')).toBe('sign_in')
  })

  it('ticket + complete goes to /auth/sign-in, status preserved', () => {
    const l = loc(`${couples}?__clerk_ticket=t3&__clerk_status=complete`)
    expect(l.pathname).toBe('/auth/sign-in')
    expect(l.searchParams.get('__clerk_status')).toBe('complete')
  })

  it('no ticket is the plain sign-in, carrying only the return path', () => {
    const url = new URL('https://portal.smd.services/portal/engagement?x=1')
    expect(unauthenticatedRedirectLocation(url, url.pathname)).toBe(
      '/auth/sign-in?redirect_url=%2Fportal%2Fengagement'
    )
  })

  it('a status without a ticket is ignored', () => {
    const url = new URL('https://portal.smd.services/portal?__clerk_status=sign_up')
    expect(unauthenticatedRedirectLocation(url, url.pathname)).toBe(
      '/auth/sign-in?redirect_url=%2Fportal'
    )
  })

  it('an unsafe pathname is never echoed as redirect_url', () => {
    const url = new URL('https://portal.smd.services/x')
    expect(unauthenticatedRedirectLocation(url, '//evil.com/portal')).toBe('/auth/sign-in')
  })
})

describe('safeRedirectPath', () => {
  it.each(['/portal', '/portal/products/operator', '/api/checkout/hosted-agent', '/a?b=c'])(
    'accepts %s',
    (v) => expect(safeRedirectPath(v)).toBe(v)
  )
  it.each([
    null,
    '',
    '//evil.com',
    '//evil.com/portal',
    '/\\evil.com',
    '/portal\\..\\x',
    'https://evil.com',
    'evil.com',
    'javascript:alert(1)',
    '/por tal',
    '/portal\n',
    '/\tevil.com',
  ])('rejects %j', (v) => expect(safeRedirectPath(v)).toBeNull())
})

describe('honoredReturnPath', () => {
  it('honors a path under the surface prefix', () => {
    expect(honoredReturnPath('/portal/products/hosted-agent/couples', 'portal')).toBe(
      '/portal/products/hosted-agent/couples'
    )
    expect(honoredReturnPath('/portal', 'portal')).toBe('/portal')
    expect(honoredReturnPath('/admin/entities', 'admin')).toBe('/admin/entities')
  })
  it('refuses another surface, a lookalike prefix, or an open redirect', () => {
    expect(honoredReturnPath('/admin/entities', 'portal')).toBeNull()
    expect(honoredReturnPath('/portalevil', 'portal')).toBeNull()
    expect(honoredReturnPath('//evil.com/portal', 'portal')).toBeNull()
    expect(honoredReturnPath('https://evil.com/portal', 'portal')).toBeNull()
    expect(honoredReturnPath(null, 'portal')).toBeNull()
  })
})
