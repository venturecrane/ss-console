/**
 * Config module — re-exports for convenience.
 */

export {
  getAppBaseUrl,
  getPortalBaseUrl,
  getAdminBaseUrl,
  requireAppBaseUrl,
  requirePortalBaseUrl,
  requireAdminBaseUrl,
  buildAppUrl,
  buildPortalUrl,
  buildAdminUrl,
} from './app-url'
export type { AppUrlEnv } from './app-url'
