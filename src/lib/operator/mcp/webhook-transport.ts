/**
 * Production transport for the async MCP handoff path (Phase 2 / ADR 0043).
 *
 * Delivers a signed `HandoffEnvelope(surface="mcp")` to the Machine's
 * `/webhooks/mcp` gate over HTTPS. The bearer is
 * `HMAC-SHA256(OPERATOR_MCP_WEBHOOK_SECRET, slug)` — same derivation as the
 * runtime-read key (a different master). Each Machine holds only its own derived
 * key (`WEBHOOK_SECRET_MCP` set at provision); the master lives only on the
 * console.
 *
 * The Machine acknowledges receipt synchronously (2xx). The Operator works the
 * task async and reports via its authored channels (email, Telegram, etc.).
 */

import { deriveRuntimeReadKey } from '../runtime-read-transport'
import { machineBaseUrl } from '../machine-url'
import { resolveCustomerFlyApp } from '../fly-app-registry'

export interface MachineWebhookEnv {
  /** Same Machine base URL template as the runtime-read path
   * (e.g. `https://{app}.fly.dev`). Reused — both paths target the same Machine. */
  OPERATOR_RUNTIME_READ_URL?: string
  /** Master secret from which each Machine's per-customer MCP webhook key is
   * derived (`HMAC-SHA256(master, slug)`). Lives only on the console. */
  OPERATOR_MCP_WEBHOOK_SECRET?: string
}

export function isWebhookConfigured(env: MachineWebhookEnv): boolean {
  return (
    typeof env.OPERATOR_RUNTIME_READ_URL === 'string' &&
    env.OPERATOR_RUNTIME_READ_URL.length > 0 &&
    typeof env.OPERATOR_MCP_WEBHOOK_SECRET === 'string' &&
    env.OPERATOR_MCP_WEBHOOK_SECRET.length > 0
  )
}

export interface HandoffEnvelope {
  handoff_id: string
  surface: 'mcp'
  trust_class: 'known_external'
  task: string
  context?: string
  from_email: string
  from_profile: string
  submitted_at: string
}

export interface MachineWebhookTransport {
  send(customerSlug: string, envelope: HandoffEnvelope): Promise<void>
}

/**
 * Construct the production console→Machine webhook transport. Throws on
 * transport failure so the caller can map it to a fail-closed result.
 * A 4xx/5xx from the Machine is treated as a delivery failure.
 */
export function createMachineWebhookTransport(env: MachineWebhookEnv): MachineWebhookTransport {
  return {
    send: async (customerSlug, envelope) => {
      if (!isWebhookConfigured(env)) {
        throw new Error('MCP webhook transport not configured (OPERATOR_MCP_WEBHOOK_SECRET unset)')
      }
      const app = resolveCustomerFlyApp(customerSlug)
      if (!app) throw new Error(`webhook transport: unknown customer ${customerSlug}`)

      const baseUrl = machineBaseUrl(env.OPERATOR_RUNTIME_READ_URL!, app)
      const bearer = await deriveRuntimeReadKey(env.OPERATOR_MCP_WEBHOOK_SECRET!, customerSlug)

      const resp = await fetch(`${baseUrl}/webhooks/handoff`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${bearer}`,
          'X-Tenant-Slug': customerSlug,
        },
        body: JSON.stringify(envelope),
      })
      if (!resp.ok) throw new Error(`webhook delivery failed: ${resp.status}`)
    },
  }
}

/**
 * Synchronous console→Machine turn request (ADR 0057 amendment, Phase 3). The
 * console is the sole public Claude door; it has already enforced the grant
 * kill-switch for the principal before calling this, and forwards the identity
 * so the Machine turn is attributed. The Machine's turn endpoint accepts only
 * this authenticated console-proxy bearer — it is not a public door.
 */
export interface OperatorTurnRequest {
  message: string
  thread_id?: string
  principal_subject: string
  from_email: string
  from_profile: string
}

export interface OperatorTurnReply {
  reply: string
  thread_id?: string
}

export interface MachineTurnTransport {
  driveTurn(customerSlug: string, req: OperatorTurnRequest): Promise<OperatorTurnReply>
}

/**
 * Construct the production console→Machine synchronous turn transport. Posts to
 * the Machine's authenticated `/mcp/turn` endpoint (bearer
 * `HMAC-SHA256(OPERATOR_MCP_WEBHOOK_SECRET, slug)`, same derivation as the
 * handoff path) and returns the Operator's reply. Throws on transport failure or
 * a non-2xx so the caller maps it to a fail-closed `delivery_failed`. A Worker
 * has no wall-clock cap on an HTTP-triggered request, so the turn is awaited
 * synchronously; the async handoff path remains the fallback for long work.
 */
export function createMachineTurnTransport(env: MachineWebhookEnv): MachineTurnTransport {
  return {
    driveTurn: async (customerSlug, req) => {
      if (!isWebhookConfigured(env)) {
        throw new Error('MCP turn transport not configured (OPERATOR_MCP_WEBHOOK_SECRET unset)')
      }
      const app = resolveCustomerFlyApp(customerSlug)
      if (!app) throw new Error(`turn transport: unknown customer ${customerSlug}`)

      const baseUrl = machineBaseUrl(env.OPERATOR_RUNTIME_READ_URL!, app)
      const bearer = await deriveRuntimeReadKey(env.OPERATOR_MCP_WEBHOOK_SECRET!, customerSlug)

      const resp = await fetch(`${baseUrl}/mcp/turn`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${bearer}`,
          'X-Tenant-Slug': customerSlug,
        },
        body: JSON.stringify(req),
      })
      if (!resp.ok) throw new Error(`turn delivery failed: ${resp.status}`)

      const data = await resp.json<{ reply?: unknown; thread_id?: unknown }>()
      return {
        reply: typeof data.reply === 'string' ? data.reply : '',
        thread_id: typeof data.thread_id === 'string' ? data.thread_id : undefined,
      }
    },
  }
}
