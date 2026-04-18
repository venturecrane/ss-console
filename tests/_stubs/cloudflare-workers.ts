/**
 * Vitest stub for the runtime-only `cloudflare:workers` module. Tests that
 * exercise API-route handlers now import `env` from this module (adapter v13
 * pattern); Node can't resolve the real module, so tests alias to this file
 * via `vitest.config.ts` and mutate the exported object to inject bindings.
 *
 *   import { env as testEnv } from 'cloudflare:workers'
 *   Object.assign(testEnv, { DB: mockDb, SESSIONS: mockKv })
 *
 * Reset between tests with `Object.keys(env).forEach(k => delete env[k])` in
 * a `beforeEach` if cross-test bleed is a concern.
 */
export const env: Record<string, unknown> = {}
