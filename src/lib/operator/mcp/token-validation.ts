import { createRemoteJWKSet, jwtVerify } from 'jose'
import { z } from 'zod'
import type { ResolvedMcpCustomer } from './customer-resolution'

const verifiedClaimsSchema = z.object({
  sub: z.string().min(1),
  iss: z.url(),
  aud: z.union([z.string().min(1), z.array(z.string().min(1)).min(1)]).optional(),
  org_id: z.string().min(1).optional(),
  email: z.email().optional(),
  email_verified: z.boolean().optional(),
})

export type McpAuthFailureReason =
  | 'missing_token'
  | 'token_not_jwt'
  | 'signature_invalid'
  | 'claims_invalid'
  | 'wrong_issuer'
  | 'wrong_audience'
  | 'connector_disabled'
  | 'identity_not_authored'
  | 'organization_mismatch'
  // Open-by-domain JIT refusals (slice 2e), surfaced by the route, not token validation.
  | 'jit_revoked'
  | 'jit_cap_exceeded'

export type McpAuthResult =
  | {
      ok: true
      customer: ResolvedMcpCustomer
      subject: string
      tokenAudience: string[]
      localUserId: string
      email: string
      profile: string
    }
  | {
      ok: false
      reason: McpAuthFailureReason
      detail: string
      subject?: string
      tokenAudience?: string[]
      /**
       * The verified primary email + its verified flag from the token, surfaced
       * ONLY on `identity_not_authored` so the route can decide an open-policy JIT
       * grant (slice 2e). The PRIMARY email is the trust anchor — under shared
       * Clerk a user can attach a secondary verified address, but the token always
       * carries their primary, so an outsider cannot JIT into a firm by adding one.
       */
      email?: string
      emailVerified?: boolean
    }

export type McpTokenVerifier = (token: string, customer: ResolvedMcpCustomer) => Promise<unknown>

export function extractBearerToken(authorizationHeader: string | null): string | null {
  if (!authorizationHeader) return null
  const match = /^Bearer\s+(.+)$/i.exec(authorizationHeader.trim())
  const token = match?.[1]?.trim()
  return token ? token : null
}

/**
 * One remote JWKS per issuer, kept for the life of the isolate.
 *
 * `createRemoteJWKSet` owns a key cache with its own cooldown and max-age; the
 * cache only helps if the same instance sees the next request. Constructing it
 * per call (the shape before 2026-09-10) meant every MCP request fetched the
 * issuer's JWKS again, which is a network round trip on the auth path and a
 * dependency on Clerk's availability per call. The map is keyed by issuer URL,
 * holds no request-scoped state, and is populated once per issuer, the same
 * class of module-level value as the WASM init memo at `src/lib/pdf/render.ts`
 * (coding-standards rule 10: module-level values are for immutable init-time
 * state, which a public-key set per issuer is).
 */
const jwksByIssuer = new Map<string, ReturnType<typeof createRemoteJWKSet>>()

function jwksFor(issuer: string): ReturnType<typeof createRemoteJWKSet> {
  const cached = jwksByIssuer.get(issuer)
  if (cached) return cached
  const created = createRemoteJWKSet(new URL('/.well-known/jwks.json', issuer))
  jwksByIssuer.set(issuer, created)
  return created
}

async function verifyPinnedClerkToken(
  token: string,
  customer: ResolvedMcpCustomer
): Promise<unknown> {
  const result = await jwtVerify(token, jwksFor(customer.clerk.issuer), {
    algorithms: ['RS256'],
  })
  return result.payload
}

function audIncludes(aud: string | string[] | undefined, expected: string): boolean {
  if (!aud) return false
  return Array.isArray(aud) ? aud.includes(expected) : aud === expected
}

function normalizeAudience(aud: string | string[] | undefined): string[] {
  if (!aud) return []
  return Array.isArray(aud) ? aud : [aud]
}

function validateClaims(
  rawClaims: unknown,
  customer: ResolvedMcpCustomer
): McpAuthResult | z.infer<typeof verifiedClaimsSchema> {
  const parsed = verifiedClaimsSchema.safeParse(rawClaims)
  if (!parsed.success) {
    return { ok: false, reason: 'claims_invalid', detail: 'required token claims are invalid' }
  }
  const claims = parsed.data
  if (claims.iss !== customer.clerk.issuer) {
    return { ok: false, reason: 'wrong_issuer', detail: 'token issuer does not match resource' }
  }
  // Clerk DCR JWTs currently omit aud. In that shape, the exact issuer plus the
  // customer-scoped subject allowlist below form the isolation boundary. A
  // present audience remains authoritative and must match this resource.
  if (claims.aud && !audIncludes(claims.aud, customer.clerk.resourceUri)) {
    return {
      ok: false,
      reason: 'wrong_audience',
      detail: 'token is bound to another resource',
      subject: claims.sub,
      tokenAudience: normalizeAudience(claims.aud),
    }
  }
  if (customer.clerkOrgId && claims.org_id !== customer.clerkOrgId) {
    return {
      ok: false,
      reason: 'organization_mismatch',
      detail: 'token organization does not match customer',
      subject: claims.sub,
      tokenAudience: normalizeAudience(claims.aud),
    }
  }
  return claims
}

export async function validateMcpToken(
  token: string | null,
  customer: ResolvedMcpCustomer,
  verifier?: McpTokenVerifier
): Promise<McpAuthResult> {
  if (!token) return { ok: false, reason: 'missing_token', detail: 'no bearer token' }
  if (!verifier && token.split('.').length !== 3) {
    return { ok: false, reason: 'token_not_jwt', detail: 'bearer token is not a compact JWT' }
  }

  let rawClaims: unknown
  try {
    rawClaims = await (verifier ?? verifyPinnedClerkToken)(token, customer)
  } catch (err) {
    return {
      ok: false,
      reason: 'signature_invalid',
      detail: err instanceof Error ? err.message : 'token verification failed',
    }
  }

  const claims = validateClaims(rawClaims, customer)
  if ('ok' in claims) return claims
  if (!customer.connector.enabled) {
    return {
      ok: false,
      reason: 'connector_disabled',
      detail: 'mcp_connector is disabled',
      subject: claims.sub,
      tokenAudience: normalizeAudience(claims.aud),
    }
  }

  const principal = customer.principals.find((entry) => entry.clerkUserId === claims.sub)
  if (!principal) {
    return {
      ok: false,
      reason: 'identity_not_authored',
      detail: 'Clerk subject is not authorized for this Operator',
      subject: claims.sub,
      tokenAudience: normalizeAudience(claims.aud),
      email: claims.email,
      emailVerified: claims.email_verified === true,
    }
  }

  return {
    ok: true,
    customer,
    subject: claims.sub,
    tokenAudience: normalizeAudience(claims.aud),
    localUserId: principal.localUserId,
    email: principal.email,
    profile: principal.profile,
  }
}
