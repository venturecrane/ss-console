/**
 * Auth for SMD's own agents acting on the console as SMD
 * (`/api/internal/agent/*`). One shared bearer, `SMD_AGENT_ADMIN_KEY`, a
 * Worker secret sourced from Infisical /ss prod. It is not a seat credential
 * (those are per-tenant, machine-key.ts) and it is not an admin session: each
 * route that accepts it does one narrow act and nothing else.
 *
 * Fails closed: an unset or empty secret rejects every request, so a Worker
 * deployed without the secret exposes nothing.
 */

import { constantTimeEqual } from './constant-time'

/**
 * The SMD admin user seeded by migrations/0005_seed_admin_user.sql. Agent
 * acts that need a human inviter (Clerk invitations) act as this user.
 */
export const SMD_ADMIN_USER_ID = '01JQFK0000ADMINUSER00000'

export function verifyAgentAdminKey(request: Request, expected: string | undefined): boolean {
  if (typeof expected !== 'string' || expected.length === 0) return false
  const auth = request.headers.get('Authorization') ?? ''
  if (!auth.startsWith('Bearer ')) return false
  const provided = auth.slice('Bearer '.length)
  if (provided.length === 0) return false
  return constantTimeEqual(provided, expected)
}
