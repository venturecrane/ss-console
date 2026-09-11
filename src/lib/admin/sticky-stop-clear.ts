/**
 * Captain cost-breaker clear — console-side transport + governance audit
 * (ADR 0062 §6, #1701).
 *
 * The clear is a control-plane action: the admin API authenticates the Captain,
 * this module proxies the reset to the Machine's gate (POST /sticky-stop/clear,
 * Bearer HMAC-SHA256(OPERATOR_MCP_WEBHOOK_SECRET, slug) — the same console-proxy
 * key as /mcp/turn), and records the governance row in `operator_stop_clears`.
 *
 * Why the audit lives here, not on the Machine: the audit-ledger broker
 * PID-gates appends to the gateway process (OP-P1-4), so neither the gate nor
 * the console can write the Machine ledger. The STOP is a runtime event on the
 * Machine ledger; the RESUME is a governance action audited control-plane-side.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { machineBaseUrl } from '../operator/machine-url'
import { deriveRuntimeReadKey } from '../operator/runtime-read-transport'
import { resolveCustomerFlyApp } from '../operator/fly-app-registry'

export interface StickyStopClearEnv {
  OPERATOR_MCP_WEBHOOK_SECRET?: string
  OPERATOR_RUNTIME_READ_URL?: string
}

export interface ClearedRow {
  customer: string
  persona: string
  prior_level: string
}

export interface GateClearResult {
  cleared: ClearedRow[]
  level: string
}

/** True when the clear transport can reach a Machine (secret + URL present). */
function isClearConfigured(env: StickyStopClearEnv): boolean {
  return (
    typeof env.OPERATOR_MCP_WEBHOOK_SECRET === 'string' &&
    env.OPERATOR_MCP_WEBHOOK_SECRET.length > 0 &&
    typeof env.OPERATOR_RUNTIME_READ_URL === 'string' &&
    env.OPERATOR_RUNTIME_READ_URL.length > 0
  )
}

/**
 * Proxy the clear to the customer's Machine gate. Throws on any failure so the
 * caller records nothing and surfaces an honest error (the operator stays
 * paused, which is the safe direction). The gate resets state and returns 200
 * even though it writes no Machine-ledger row (broker PID-gate).
 */
export async function clearStopOnMachine(
  env: StickyStopClearEnv,
  customerSlug: string,
  body: { captain_id: string; reason: string }
): Promise<GateClearResult> {
  if (!isClearConfigured(env)) {
    throw new Error('clear transport not configured (OPERATOR_MCP_WEBHOOK_SECRET / URL unset)')
  }
  const app = resolveCustomerFlyApp(customerSlug)
  if (!app) throw new Error(`clear: unknown customer ${customerSlug}`)

  const bearer = await deriveRuntimeReadKey(env.OPERATOR_MCP_WEBHOOK_SECRET!, customerSlug)
  const url = `${machineBaseUrl(env.OPERATOR_RUNTIME_READ_URL!, app)}/sticky-stop/clear`
  const resp = await fetch(url, {
    method: 'POST',
    headers: { Authorization: `Bearer ${bearer}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`gate clear failed: ${resp.status} ${detail.slice(0, 200)}`)
  }
  const data: Partial<GateClearResult> = await resp.json()
  return {
    cleared: Array.isArray(data.cleared) ? data.cleared : [],
    level: data.level ?? 'unknown',
  }
}

export interface RecordClearInput {
  entity_id: string
  customer_slug: string
  actor_user_id: string
  actor_email: string
  actor_role: string
  reason: string
  result: GateClearResult
}

/** Record the governance row. Uses crypto.randomUUID for the id. */
export async function recordStopClear(db: D1Database, input: RecordClearInput): Promise<void> {
  await db
    .prepare(
      'INSERT INTO operator_stop_clears ' +
        '(id, entity_id, customer_slug, actor_user_id, actor_email, actor_role, reason, ' +
        'cleared_json, gate_level) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
    )
    .bind(
      crypto.randomUUID(),
      input.entity_id,
      input.customer_slug,
      input.actor_user_id,
      input.actor_email,
      input.actor_role,
      input.reason,
      JSON.stringify(input.result.cleared),
      input.result.level
    )
    .run()
}
