/**
 * The MCP token verifier keeps one remote JWKS per issuer for the life of the
 * isolate. Before 2026-09-10 every request constructed a fresh
 * `createRemoteJWKSet`, discarding jose's key cache and fetching Clerk's JWKS
 * on every call (2026-08-23 review C5, carried three reviews).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const createRemoteJWKSet = vi.fn((url: URL) => ({ url: url.toString() }))
const jwtVerify = vi.fn(async (_token: string, jwks: { url: string }) => ({
  payload: { sub: 'user_1', iss: new URL(jwks.url).origin },
}))
vi.mock('jose', () => ({
  createRemoteJWKSet: (url: URL) => createRemoteJWKSet(url),
  jwtVerify: (token: string, jwks: { url: string }) => jwtVerify(token, jwks),
}))

import { validateMcpToken } from '../src/lib/operator/mcp/token-validation'
import type { ResolvedMcpCustomer } from '../src/lib/operator/mcp/customer-resolution'

function customer(issuer: string): ResolvedMcpCustomer {
  return {
    entityId: 'ent',
    customerId: 'smd',
    clerkOrgId: null,
    connector: {
      enabled: true,
      data_posture: 'open',
      policy: 'allowlist',
      allowed_domains: [],
      default_profile: null,
      ttl_days: 30,
      access: [],
    },
    clerk: {
      issuer,
      resourceUri: 'https://smd.services/api/operator/smd/mcp',
      clientId: null,
      clerkAppId: null,
    },
    principals: [],
  }
}

describe('remote JWKS memo', () => {
  beforeEach(() => {
    createRemoteJWKSet.mockClear()
    jwtVerify.mockClear()
  })

  it('constructs one JWKS per issuer across many verifications', async () => {
    const a = customer('https://clerk.a.example')
    await validateMcpToken('h.p.s', a)
    await validateMcpToken('h.p.s', a)
    await validateMcpToken('h.p.s', a)
    expect(jwtVerify).toHaveBeenCalledTimes(3)
    expect(createRemoteJWKSet).toHaveBeenCalledTimes(1)
    expect(createRemoteJWKSet.mock.calls[0]?.[0].toString()).toBe(
      'https://clerk.a.example/.well-known/jwks.json'
    )
  })

  it('keeps issuers apart: a second issuer gets its own JWKS, then reuses it', async () => {
    await validateMcpToken('h.p.s', customer('https://clerk.a.example'))
    await validateMcpToken('h.p.s', customer('https://clerk.b.example'))
    await validateMcpToken('h.p.s', customer('https://clerk.b.example'))
    // Issuer a was already memoised by the previous test in this module.
    const urls = createRemoteJWKSet.mock.calls.map((c) => c[0].toString())
    expect(urls).toEqual(['https://clerk.b.example/.well-known/jwks.json'])
  })
})
