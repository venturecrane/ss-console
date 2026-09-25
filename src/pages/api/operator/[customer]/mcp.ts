/**
 * The per-customer MCP endpoint a firm's Claude connector talks to (ADR 0057).
 *
 * The slug must resolve to a customer whose authored resource URI matches this
 * URL exactly, or the route answers 404. That resolution reads the live
 * mcp_issued_grants rows on every request (the kill switch), and POST hands the
 * request to handleMcpPost, which validates the bearer token against that
 * principal set before a tool runs; runtime reads, handoffs and turns then reach
 * that customer's Machine only.
 */
import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import {
  buildMcpResourcePath,
  loadMcpCustomer,
} from '../../../../lib/operator/mcp/customer-resolution'
import {
  handleMcpGet,
  handleMcpOptions,
  handleMcpPost,
} from '../../../../lib/operator/mcp/mcp-route'
import type { HandoffEnvelope } from '../../../../lib/operator/mcp/webhook-transport'
import {
  createMachineTurnTransport,
  createMachineWebhookTransport,
  isWebhookConfigured,
} from '../../../../lib/operator/mcp/webhook-transport'
import { readMachineRuntime } from '../../../../lib/operator/runtime-read'
import {
  createMachineRuntimeTransport,
  createRuntimeReadAudit,
} from '../../../../lib/operator/runtime-read-transport'

async function loadRouteCustomer(customerSlug: string | undefined, origin: string) {
  if (!customerSlug) return null
  const customer = await loadMcpCustomer(env.DB, customerSlug)
  if (!customer) return null
  const requestedResource = new URL(buildMcpResourcePath(customerSlug), origin).toString()
  return requestedResource === customer.clerk.resourceUri ? customer : null
}

export const POST: APIRoute = async ({ request, url, params }) => {
  const customer = await loadRouteCustomer(params.customer, url.origin)
  if (!customer) return new Response('Not found', { status: 404 })

  const transport = createMachineRuntimeTransport(env)
  const webhookTransport = isWebhookConfigured(env) ? createMachineWebhookTransport(env) : null
  const turnTransport = isWebhookConfigured(env) ? createMachineTurnTransport(env) : null

  return handleMcpPost(request, url, {
    db: env.DB,
    customer,
    readRuntime: (auth, query) => {
      const audit = createRuntimeReadAudit(env.DB, { actorUserId: auth.localUserId })
      return readMachineRuntime({ transport, audit }, customer.customerId, query, {
        actor: auth.email,
        actorRole: 'mcp_client',
      })
    },
    sendHandoff: webhookTransport
      ? (auth, params) => {
          const envelope: HandoffEnvelope = {
            handoff_id: params.handoff_id,
            surface: 'mcp',
            trust_class: 'known_external',
            task: params.task,
            context: params.context,
            from_email: auth.email,
            from_profile: auth.profile,
            submitted_at: new Date().toISOString(),
          }
          return webhookTransport.send(customer.customerId, envelope)
        }
      : undefined,
    driveTurn: turnTransport
      ? (auth, params) =>
          turnTransport.driveTurn(customer.customerId, {
            message: params.message,
            thread_id: params.thread_id,
            principal_subject: auth.subject,
            from_email: auth.email,
            from_profile: auth.profile,
          })
      : undefined,
  })
}

export const GET: APIRoute = async ({ url, params }) => {
  const customer = await loadRouteCustomer(params.customer, url.origin)
  return customer ? handleMcpGet() : new Response('Not found', { status: 404 })
}

export const OPTIONS: APIRoute = async ({ url, params }) => {
  const customer = await loadRouteCustomer(params.customer, url.origin)
  return customer ? handleMcpOptions() : new Response('Not found', { status: 404 })
}
