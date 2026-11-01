import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveHostedAgentAccess } from '../../../../../lib/portal/hosted-agent-access'
import { getIntakeByEntity, setIntakeKeyStatus } from '../../../../../lib/db/hosted-agent-intake'
import { maskSecret, MAX_SECRET_LENGTH } from '../../../../../lib/operator/credential-secret-write'
import {
  createHostedKeyWriter,
  isHostedSecretTransportConfigured,
} from '../../../../../lib/operator/infisical-secret-transport'

/**
 * POST /api/portal/products/hosted-agent/anthropic-key (ADR 0067)
 *
 * Write-only entry for the customer's BYO Anthropic key. Mirrors the
 * connector-secret endpoint's no-leak posture (ADR 0042): the value lives
 * only in the POST body, flows straight to the Infisical staging path via
 * the injected transport, and is NEVER stored in D1, logged, echoed, or
 * placed in a URL. The audit row (connector_secret_audit, connector
 * 'anthropic') is structurally incapable of carrying the value.
 *
 * Gates, in order:
 *   - principal role on the hosted-agent product
 *   - a customer slug must be assigned (Captain does this in the admin
 *     queue BEFORE the customer is asked for the key, so the write lands
 *     at the right per-customer path)
 *   - the transport must be configured — honest `not_enabled` until wired
 */

const RETURN_TO = '/portal/products/operator/setup/api-key'

function back(cs: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${RETURN_TO}?cs=${cs}` } })
}

export const POST: APIRoute = async ({ locals, request }) => {
  const access = await resolveHostedAgentAccess(env.DB, locals, { allowedRoles: ['principal'] })
  if (access.kind === 'redirect') {
    return new Response(null, { status: 303, headers: { Location: access.to } })
  }

  const intake = await getIntakeByEntity(env.DB, access.client.id)
  if (!intake) return back('error')
  if (!intake.customer_slug) return back('not_ready')

  if (!isHostedSecretTransportConfigured(env)) {
    return back('not_enabled')
  }

  const form = await request.formData()
  const secret = form.get('secret')
  if (typeof secret !== 'string' || secret.trim().length === 0) {
    return back('empty_secret')
  }
  const trimmed = secret.trim()
  if (trimmed.length > MAX_SECRET_LENGTH) {
    return back('too_long')
  }

  let ref: string
  try {
    const writer = createHostedKeyWriter(env)
    const written = await writer.write({ customerSlug: intake.customer_slug, secret: trimmed })
    ref = written.ref
  } catch {
    // Deliberately discard the thrown value — it may embed request detail.
    return back('write_failed')
  }

  try {
    await env.DB.prepare(
      `INSERT INTO connector_secret_audit
           (customer_slug, entity_id, connector, actor_user_id, actor_email, actor_role, masked_tail, storage_ref)
         VALUES (?, ?, 'anthropic', ?, ?, 'principal', ?, ?)`
    )
      .bind(
        intake.customer_slug,
        access.client.id,
        access.user.id,
        access.user.email,
        maskSecret(trimmed),
        ref
      )
      .run()
    await setIntakeKeyStatus(env.DB, intake.id, 'received')
  } catch (err) {
    // The vault write landed; the bookkeeping failure must not read as a
    // failed key entry. Log (no value present in err) and confirm.
    console.error('[hosted-agent/anthropic-key] audit bookkeeping failed:', err)
  }
  return back('saved')
}
