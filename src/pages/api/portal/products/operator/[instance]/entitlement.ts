import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveOperatorAccess } from '../../../../../../lib/portal/operator-access'
import { getCustomerConfigBySlug } from '../../../../../../lib/portal/customer-config'
import { clientRolePermits } from '../../../../../../lib/portal/operator/client-rbac'
import { resolveDomainSurfaceMode } from '../../../../../../lib/portal/operator/domain-surface'
import type { LiveExposure } from '../../../../../../lib/operator/entitlement-compiler'
import {
  applyTierChange,
  readLiveOverrides,
} from '../../../../../../lib/portal/operator/entitlement-change'

/**
 * POST /api/portal/products/operator/[instance]/entitlement — the entitlement
 * dial (#2003, A&P diligence reply Q7; agreement §2.4-2.5).
 *
 * Form fields: routine, targetTier, reason (all required).
 *
 * A Named Administrator's tier change is RUNTIME POSTURE (Captain ruling
 * 2026-07-28): compiled to per-action-class ceiling values, applied on the
 * running Machine through the gate (POST /entitlement/set — the same
 * console-proxy trust boundary as the pause), and enforced from the next tool
 * call. The authored ceiling in git is non-raisable from here — the compiler
 * refuses above-ceiling targets, and the Machine's own clamp refuses them
 * independently. Every change is recorded with who/when/why and unioned into
 * the activity feed.
 *
 * Enforcement chain:
 *  1. resolveOperatorAccess — Clerk session, entity binding, live
 *     subscription, role ∈ {principal}
 *  2. `trust` authority domain must be client-operable AND the role matrix
 *     must permit; unauthored authority = managed = refused (fail-closed)
 *  3. compileTierChange — letter ceiling + vertical floor, both non-raisable
 *  4. Machine first, record second (never record a change the Machine did
 *     not acknowledge; the Machine re-clamps as defense in depth)
 */

const OPERATOR_LANDING = '/portal/products/operator'

function redirectToSettings(instance: string, status: string): Response {
  return new Response(null, {
    status: 303,
    headers: {
      Location: `${OPERATOR_LANDING}/${instance}/settings?status=${encodeURIComponent(status)}`,
    },
  })
}

export const POST: APIRoute = async ({ params, request, locals }) => {
  const instance = typeof params.instance === 'string' ? params.instance : ''
  if (instance === '') return new Response('Not found', { status: 404 })

  const access = await resolveOperatorAccess(env.DB, locals, {
    allowedRoles: ['principal'],
    customerSlug: instance,
  })
  if (access.kind === 'redirect') return new Response('Forbidden', { status: 403 })

  const config = await getCustomerConfigBySlug(env.DB, instance)
  const mode = resolveDomainSurfaceMode(
    config?.authority ?? null,
    'trust',
    clientRolePermits(access.roles, 'trust')
  )
  if (mode !== 'operable') return redirectToSettings(instance, 'entitlement_not_operable')

  const formData = await request.formData()
  const field = (name: string): string => {
    const v = formData.get(name)
    return typeof v === 'string' ? v.trim() : ''
  }
  const routine = field('routine')
  const targetTier = field('targetTier')
  const reason = field('reason')
  if (routine === '' || targetTier === '') {
    return redirectToSettings(instance, 'entitlement_invalid_request')
  }
  if (reason === '') return redirectToSettings(instance, 'entitlement_reason_required')

  const resolved = resolveGridAndExposure(config)
  if (!resolved) return redirectToSettings(instance, 'entitlement_config_unreadable')

  // Overlay the Machine's LIVE overrides onto the projected authored exposure
  // before compiling: the projection is the authored baseline, but the tier
  // the client is moving FROM is the enforced one — without this, lowering a
  // previously-raised routine would no-op against the stale authored value.
  // A failed read falls back to authored (the apply itself stays safe: the
  // set is absolute and the Machine re-clamps). A persona mismatch does NOT
  // fall back (ss#2314): the seat is enforcing overrides under a name this
  // grid does not know, so the tier we would compile FROM is fiction — refuse
  // and say so rather than write a change computed against the wrong baseline.
  const read = await readLiveOverrides(
    {
      OPERATOR_RUNTIME_READ_SECRET: env.OPERATOR_RUNTIME_READ_SECRET,
      OPERATOR_RUNTIME_READ_URL: env.OPERATOR_RUNTIME_READ_URL,
    },
    instance,
    resolved.live.personaSlug
  )
  if (read.status === 'persona_mismatch') {
    return redirectToSettings(instance, 'entitlement_config_unreadable')
  }
  if (read.status === 'ok') {
    resolved.live = {
      personaSlug: resolved.live.personaSlug,
      exposure: { ...resolved.live.exposure, ...read.overrides },
    }
  }

  const outcome = await applyTierChange(
    env.DB,
    {
      OPERATOR_MCP_WEBHOOK_SECRET: env.OPERATOR_MCP_WEBHOOK_SECRET,
      OPERATOR_RUNTIME_READ_URL: env.OPERATOR_RUNTIME_READ_URL,
    },
    { grid: resolved.grid, live: resolved.live },
    {
      entityId: access.client.id,
      customerSlug: instance,
      routine,
      targetTier,
      reason,
      vertical: config?.vertical ?? null,
      actor: { userId: access.user.id, email: access.user.email, role: 'principal' },
      source: 'portal',
    }
  )

  return redirectToSettings(instance, statusFor(outcome))
}

/**
 * Grid + live persona exposure from the projection, or null when either is
 * absent (a seat whose grid has never been projected cannot be configured
 * from here — refuse rather than guess).
 */
function resolveGridAndExposure(
  config: Awaited<ReturnType<typeof getCustomerConfigBySlug>>
): { grid: NonNullable<NonNullable<typeof config>['routine_grid']>; live: LiveExposure } | null {
  const grid = config?.routine_grid ?? null
  if (!grid) return null
  const persona = config?.personas.find((p) => p.slug === grid.persona)
  if (!persona) return null
  return {
    grid,
    live: {
      personaSlug: persona.slug,
      exposure: persona.entitlements.exposure,
    },
  }
}

/** Outcome → status slug the settings page renders as client copy. */
function statusFor(outcome: Awaited<ReturnType<typeof applyTierChange>>): string {
  switch (outcome.kind) {
    case 'applied':
      return 'entitlement_applied'
    case 'noop':
      return 'entitlement_no_change'
    case 'rejected':
      return `entitlement_rejected_${outcome.rejections[0].code}`
    default:
      return 'entitlement_failed'
  }
}
