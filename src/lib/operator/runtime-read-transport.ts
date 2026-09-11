/**
 * Production wiring for the live runtime read path (runtime-read.ts, ADR 0043
 * path A). Two seams the edge needs:
 *
 *   1. The console→Machine transport ({@link MachineRuntimeTransport}) — the
 *      authenticated, read-only HTTP call to one customer's Machine read
 *      endpoint (the overlay's `GET /runtime/<kind>`). Guarded by
 *      {@link isRuntimeReadConfigured}: until `OPERATOR_RUNTIME_READ_URL` and
 *      `OPERATOR_RUNTIME_READ_SECRET` are both set, drill-in surfaces render
 *      honest empty states. `readMachineRuntime` is fail-closed, so a transport
 *      throw always collapses to a clean empty result — never a crash.
 *
 *   2. The console-side read-audit sink ({@link RuntimeReadAudit}) — a D1
 *      insert into `operator_runtime_read_audit`.
 *
 * Per-customer key (no shared key): the bearer is `HMAC-SHA256(master, slug)`
 * where the master (`OPERATOR_RUNTIME_READ_SECRET`) lives ONLY here and each
 * Machine holds only its own derived key (set at provision). The canonical HMAC
 * input is the customer slug (== customer_id); the provision script MUST derive
 * over the identical string (see `operator/bin/provision-customer.sh`). A key
 * extracted from one Machine cannot read another, and the master never leaves
 * the console.
 */

import type { MachineRuntimeTransport, RuntimeReadAudit, RuntimeReadQuery } from './runtime-read'
import { machineBaseUrl } from './machine-url'
import { RuntimeReadUnauthorizedError } from './runtime-read'
import { resolveCustomerFlyApp } from './fly-app-registry'

/**
 * Structural view of the env the transport needs. Optional fields so this
 * module compiles before the bindings are provisioned; declared in env.d.ts.
 */
export interface RuntimeReadEnv {
  /** Enable flag + per-customer host template for the console→Machine read.
   * A `{app}` placeholder is substituted with the registry-resolved Fly app;
   * absent placeholder falls back to `https://<app>.fly.dev`. */
  OPERATOR_RUNTIME_READ_URL?: string
  /** Master secret from which each Machine's per-customer read key is derived
   * (`HMAC-SHA256(master, slug)`). Lives only on the console. */
  OPERATOR_RUNTIME_READ_SECRET?: string
}

/**
 * Wired only when BOTH the host template and the master secret are present.
 * Either missing → not configured → surfaces fail closed to empty states.
 */
export function isRuntimeReadConfigured(env: RuntimeReadEnv): boolean {
  return (
    typeof env.OPERATOR_RUNTIME_READ_URL === 'string' &&
    env.OPERATOR_RUNTIME_READ_URL.length > 0 &&
    typeof env.OPERATOR_RUNTIME_READ_SECRET === 'string' &&
    env.OPERATOR_RUNTIME_READ_SECRET.length > 0
  )
}

export class RuntimeReadNotConfiguredError extends Error {
  constructor() {
    super(
      'operator runtime read path is not configured ' +
        '(OPERATOR_RUNTIME_READ_URL / OPERATOR_RUNTIME_READ_SECRET unset)'
    )
    this.name = 'RuntimeReadNotConfiguredError'
  }
}

/**
 * Derive a Machine's per-customer read key: `hex(HMAC-SHA256(master, slug))`.
 * The canonical input is the customer slug with NO trailing newline — the
 * provision script's `printf '%s'` must match exactly or every read 401s.
 * Exported so the cross-side match test can pin it against the shell derivation.
 */
export async function deriveRuntimeReadKey(master: string, customerSlug: string): Promise<string> {
  const enc = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(master),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(customerSlug))
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

/** Append the read query params the Machine endpoint understands. */
function appendQuery(url: URL, query: RuntimeReadQuery): void {
  if (query.cursor) url.searchParams.set('cursor', query.cursor)
  if (typeof query.limit === 'number') url.searchParams.set('limit', String(query.limit))
  if (query.id) url.searchParams.set('id', query.id)
  // memory_export requires a table; the Machine refuses one outside its allow-list.
  if (query.table) url.searchParams.set('table', query.table)
  // config_export requires a section; the Machine refuses one outside its allow-list.
  if (query.section) url.searchParams.set('section', query.section)
}

/**
 * Construct the production console→Machine transport. Read-only by construction
 * (the only method is `read`, scoped to one customer per call). Throws on any
 * failure so `readMachineRuntime` collapses it to a fail-closed empty result;
 * a 401/403 throws {@link RuntimeReadUnauthorizedError} so the audit row records
 * `unauthorized` rather than `unreachable`.
 */
export function createMachineRuntimeTransport(env: RuntimeReadEnv): MachineRuntimeTransport {
  return {
    read: async (customerSlug, query) => {
      if (!isRuntimeReadConfigured(env)) throw new RuntimeReadNotConfiguredError()
      const app = resolveCustomerFlyApp(customerSlug)
      // Unlisted customer → refuse to target any app (fail-closed unreachable).
      if (!app) throw new Error(`runtime read: unknown customer ${customerSlug}`)

      const url = new URL(
        `${machineBaseUrl(env.OPERATOR_RUNTIME_READ_URL!, app)}/runtime/${query.kind}`
      )
      appendQuery(url, query)
      const bearer = await deriveRuntimeReadKey(env.OPERATOR_RUNTIME_READ_SECRET!, customerSlug)

      const resp = await fetch(url.toString(), {
        method: 'GET',
        headers: { Authorization: `Bearer ${bearer}`, 'X-Tenant-Slug': customerSlug },
      })
      if (resp.status === 401 || resp.status === 403) throw new RuntimeReadUnauthorizedError()
      if (!resp.ok) throw new Error(`runtime read failed: ${resp.status}`)
      return { data: await resp.json() }
    },
  }
}

/**
 * Construct the console-side read-audit sink. Binds the per-request actor id
 * (which the core's audit interface does not carry) and writes one append-only
 * `operator_runtime_read_audit` row per read attempt.
 */
export function createRuntimeReadAudit(
  db: D1Database,
  ctx: { actorUserId: string }
): RuntimeReadAudit {
  return {
    record: async (row) => {
      await db
        .prepare(
          'INSERT INTO operator_runtime_read_audit ' +
            '(customer_slug, actor_user_id, actor_email, actor_role, kind, outcome) ' +
            'VALUES (?, ?, ?, ?, ?, ?)'
        )
        .bind(row.customerSlug, ctx.actorUserId, row.actor, row.actorRole, row.kind, row.outcome)
        .run()
    },
  }
}
