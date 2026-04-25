/**
 * Wrapper that turns each "best-effort" enrichment-module call into a
 * persisted run row. Exists because the pre-existing
 * `result.completed/skipped/errors` arrays were in-memory only — workers
 * discarded the result, leaving zero record of which modules ran for which
 * entity, which is why partial-enrichment prospects (e.g. Cactus Creative
 * Studio) were undebuggable.
 *
 * Module body returns one of three terminal outcomes; throws are caught
 * and classified as `failed`. Lock contention (a running row already
 * exists for this entity+module) yields `skipped, reason: 'in_progress'`.
 */

import type { ModuleId } from './modules'
import { startRun, completeRun, type RunMode, type RunStatus } from '../db/enrichment-runs'

export class ModuleError extends Error {
  constructor(
    public readonly kind: 'fetch_failed' | 'parse_error' | 'timeout' | 'api_error' | 'unknown',
    message?: string,
    public readonly cause?: unknown
  ) {
    super(message ?? kind)
    this.name = 'ModuleError'
  }
}

export function classifyError(err: unknown): ModuleError {
  if (err instanceof ModuleError) return err
  if (err instanceof Error) {
    if (err.name === 'AbortError' || /aborted|timeout/i.test(err.message)) {
      return new ModuleError('timeout', err.message, err)
    }
    if (err instanceof SyntaxError) {
      return new ModuleError('parse_error', err.message, err)
    }
    if (err instanceof TypeError && /fetch|network/i.test(err.message)) {
      return new ModuleError('fetch_failed', err.message, err)
    }
    return new ModuleError('unknown', err.message, err)
  }
  return new ModuleError('unknown', String(err), err)
}

export type ModuleOutcome =
  | { kind: 'succeeded'; context_entry_id?: string | null }
  | { kind: 'no_data'; reason?: string }
  | { kind: 'skipped'; reason: string }

export interface InstrumentOptions {
  db: D1Database
  org_id: string
  entity_id: string
  module: ModuleId
  mode: RunMode
  triggered_by: string
  input_fingerprint?: string | null
}

export interface InstrumentResult {
  status: RunStatus
  reason?: string
}

/**
 * Run the module body under instrumentation. Always settles to one of:
 * succeeded | no_data | skipped | failed. Never throws.
 */
export async function instrumentModule(
  opts: InstrumentOptions,
  body: () => Promise<ModuleOutcome>
): Promise<InstrumentResult> {
  const runId = await startRun(opts.db, {
    org_id: opts.org_id,
    entity_id: opts.entity_id,
    module: opts.module,
    mode: opts.mode,
    triggered_by: opts.triggered_by,
    input_fingerprint: opts.input_fingerprint ?? null,
  })

  if (!runId) {
    return { status: 'skipped', reason: 'in_progress' }
  }

  try {
    const outcome = await body()
    if (outcome.kind === 'succeeded') {
      await completeRun(opts.db, runId, {
        status: 'succeeded',
        context_entry_id: outcome.context_entry_id ?? null,
      })
      return { status: 'succeeded' }
    }
    if (outcome.kind === 'no_data') {
      await completeRun(opts.db, runId, {
        status: 'no_data',
        reason: outcome.reason ?? null,
      })
      return { status: 'no_data', reason: outcome.reason }
    }
    await completeRun(opts.db, runId, {
      status: 'skipped',
      reason: outcome.reason,
    })
    return { status: 'skipped', reason: outcome.reason }
  } catch (err) {
    const me = classifyError(err)
    console.error('[enrichment] module threw', {
      module: opts.module,
      message: me.message,
      cause: me.cause,
    })
    await completeRun(opts.db, runId, {
      status: 'failed',
      reason: me.kind,
      error_message: me.message,
    })
    return { status: 'failed', reason: me.kind }
  }
}

/**
 * Stable 1KB-truncated SHA-256 of an input string. Used as the
 * `input_fingerprint` for review_synthesis and intelligence_brief so we
 * can later detect when upstream context has grown enough to justify
 * re-running a module that already succeeded.
 */
export async function fingerprint(input: string): Promise<string> {
  const truncated = input.slice(0, 1024)
  const bytes = new TextEncoder().encode(truncated)
  const hashBuffer = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(hashBuffer))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}
