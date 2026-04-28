/**
 * Auth module — re-exports for convenience.
 */

export { hashPassword, verifyPassword } from './password'
export {
  createSession,
  validateSession,
  renewSession,
  destroySession,
  buildSessionCookie,
  buildClearSessionCookie,
  parseSessionToken,
  SESSION_COOKIE_NAME,
  SESSION_DURATION_MS,
} from './session'
export type { SessionData } from './session'
export {
  createMagicLink,
  verifyMagicLink,
  MAGIC_LINK_EXPIRY_MS,
  PROSPECT_MAGIC_LINK_EXPIRY_MS,
} from './magic-link'
