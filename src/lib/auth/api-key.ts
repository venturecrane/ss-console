/**
 * API key validation for machine-to-machine endpoints.
 *
 * Uses constant-time comparison to prevent timing attacks.
 * Matches the pattern used in webhook handlers (signwell.ts, stripe.ts).
 */
export function validateApiKey(request: Request, expectedKey: string | undefined): boolean {
  const header = request.headers.get('Authorization')
  if (!header?.startsWith('Bearer ')) return false

  const provided = header.slice(7)
  const expected = expectedKey ?? ''

  if (!expected || provided.length !== expected.length) return false

  let mismatch = 0
  for (let i = 0; i < provided.length; i++) {
    mismatch |= provided.charCodeAt(i) ^ expected.charCodeAt(i)
  }

  return mismatch === 0
}
