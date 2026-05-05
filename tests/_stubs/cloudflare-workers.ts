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
 *
 * Workflows stubs
 * ---------------
 * `WorkflowEntrypoint` and the `WorkflowEvent` / `WorkflowStep` types are
 * provided as no-op stubs so production code that subclasses
 * WorkflowEntrypoint (`src/lib/enrichment/workflow.ts`) imports cleanly
 * under vitest. The workflow class is exercised in tests by instantiating
 * it directly with a mock `env` and a mock `step` — we bypass the real
 * Cloudflare Workflows runtime entirely. See
 * tests/enrichment-workflow.test.ts.
 */
export const env: Record<string, unknown> = {}

/**
 * Minimal stub mirroring the real WorkflowEntrypoint. The real class is
 * provided by the Cloudflare runtime and exposes `env` and `ctx` to
 * subclasses. The test stub only needs to supply `env` so subclasses
 * can call `this.env.DB` etc.
 */
export class WorkflowEntrypoint<EnvT = unknown, _ParamsT = unknown> {
  protected env: EnvT
  protected ctx: unknown
  constructor(ctx: unknown, env: EnvT) {
    this.ctx = ctx
    this.env = env
  }
}

/**
 * Type-only stubs. Vitest only needs these to satisfy the type imports
 * in workflow.ts under test transpilation; they're not used at runtime.
 */
export type WorkflowEvent<P = unknown> = {
  payload: P
  timestamp: Date
  instanceId: string
}
export type WorkflowStep = {
  do<T>(name: string, fn: () => Promise<T>): Promise<T>
  do<T>(name: string, config: unknown, fn: () => Promise<T>): Promise<T>
  sleep(name: string, duration: string | number): Promise<void>
}
