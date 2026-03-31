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
