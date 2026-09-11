/**
 * POST /api/admin/operator/[customer]/authority
 *
 * SMD flips one per-domain authority switch (managed <-> client) for a customer
 * (design §5.9, ADR 0041). Form fields:
 *
 *   domain      — a switchable authority domain (e.g. 'people_access')
 *   new_holder  — 'managed' | 'client'
 *
 * Records the flip as INTENT to operator_authority_audit (the Layer-0 SMD actor
 * is stamped — the audit, foundations §6). It does NOT mutate the live
 * customer_configs replica: the authority block lives in customer.yaml (git
 * source of truth, ADR 0012) and reaches the runtime via the deferred git
 * write-back path, exactly like the trust-ceiling endpoint. Status banner:
 *   ?status=saved          — recorded; applies on next config sync
 *   ?status=no_change      — target equals current holder (no-op)
 *   ?status=invalid_domain — not a switchable domain (SMD-only or unknown)
 *   ?status=invalid_holder — new_holder not managed/client
 *   ?status=not_found      — no operator with that slug
 *
 * Admin-only (middleware on /api/admin/* + explicit re-check).
 */

import type { APIContext, APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveEntityIdBySlug } from '../../../../../lib/admin/operator-overview'
import {
  validateAuthorityFlip,
  recordAuthorityFlip,
} from '../../../../../lib/admin/authority-write'
import { getCustomerConfig } from '../../../../../lib/portal/customer-config'
import { resolveDomainAuthority, isSwitchableDomain } from '../../../../../lib/operator/authority'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'

function redirectToAuthority(slug: string, status: string): Response {
  const target = `/admin/operator/${encodeURIComponent(slug)}/authority?status=${encodeURIComponent(status)}`
  return new Response(null, { status: 303, headers: { Location: target } })
}

async function handlePost(ctx: APIContext): Promise<Response> {
  const auth = requireAdminSession(ctx.locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const slug = ctx.params.customer ?? ''
  const entityId = await resolveEntityIdBySlug(env.DB, slug)
  if (!entityId) return redirectToAuthority(slug, 'not_found')

  const form = await ctx.request.formData()
  const domain = form.get('domain')
  const newHolder = form.get('new_holder')
  if (typeof domain !== 'string' || typeof newHolder !== 'string') {
    return redirectToAuthority(slug, 'invalid_holder')
  }

  const config = await getCustomerConfig(env.DB, entityId)
  if (!config) return redirectToAuthority(slug, 'not_found')

  // Current holder is the materialized posture (the only honest "old" value).
  const oldHolder = isSwitchableDomain(domain)
    ? resolveDomainAuthority(config.authority, domain)
    : 'managed'

  const validation = validateAuthorityFlip({
    domain,
    old_holder: oldHolder,
    new_holder: newHolder,
  })
  if (!validation.ok) return redirectToAuthority(slug, validation.error)

  await recordAuthorityFlip(env.DB, {
    entity_id: entityId,
    customer_slug: config.customer_slug,
    actor_user_id: session.userId,
    actor_email: session.email,
    actor_role: session.role,
    domain: validation.domain,
    old_holder: validation.old_holder,
    new_holder: validation.new_holder,
  })

  return redirectToAuthority(slug, 'saved')
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
