import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveOperatorAccess } from '../../../../../../lib/portal/operator-access'
import { getCustomerConfigBySlug } from '../../../../../../lib/portal/customer-config'
import { clientRolePermits } from '../../../../../../lib/portal/operator/client-rbac'
import { resolveDomainSurfaceMode } from '../../../../../../lib/portal/operator/domain-surface'
import {
  recordPauseEvent,
  resumeOnMachine,
  setStopOnMachine,
  type PauseAction,
} from '../../../../../../lib/portal/operator/pause-control'

/**
 * POST /api/portal/products/operator/[instance]/pause — the portal kill
 * switch (#2003, A&P diligence reply Q6/Q7).
 *
 * Form fields:
 *   action — 'pause' | 'resume'
 *   reason — required free text (the governance row is useless without it)
 *
 * Q6: "It stops all activity immediately and stays off until it's turned
 * back on, and your authorized portal users can do it without us. Every
 * pause and resume is logged."
 *
 * Enforcement chain, in order:
 *   1. resolveOperatorAccess — Clerk session, entity binding, live
 *      subscription, role ∈ {principal} (the portal's "admin" level; the
 *      firm's named admins hold principal).
 *   2. Domain surface — the `runtime` authority switch must be `client`
 *      (authored per seat) AND the role matrix must permit; otherwise the
 *      control renders read_request and this route refuses. No imposed
 *      default: an unauthored switch means NOT operable (fail-closed).
 *   3. Machine leg — gate POST /sticky-stop/set (pause, overlay#188) or
 *      /sticky-stop/clear (resume). The gate call happens BEFORE the audit
 *      row: a pause the Machine did not acknowledge is never recorded as a
 *      pause; conversely a failure surfaces honestly and nothing is logged.
 *   4. Governance row in operator_pause_events (who/when/why + gate level),
 *      unioned into the client-readable audit record.
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
  if (access.kind === 'redirect') {
    return new Response('Forbidden', { status: 403 })
  }

  // Layer 1 (authority switch) × Layer 2 (role matrix): the pause control is
  // operable only when the seat authors runtime: client AND the acting user's
  // roles permit the runtime domain. Everything else is read_request.
  const config = await getCustomerConfigBySlug(env.DB, instance)
  const mode = resolveDomainSurfaceMode(
    config?.authority ?? null,
    'runtime',
    clientRolePermits(access.roles, 'runtime')
  )
  if (mode !== 'operable') {
    return redirectToSettings(instance, 'pause_not_operable')
  }

  const formData = await request.formData()
  const actionRaw = formData.get('action')
  const reasonRaw = formData.get('reason')
  const action: PauseAction | null =
    actionRaw === 'pause' || actionRaw === 'resume' ? actionRaw : null
  const reason = typeof reasonRaw === 'string' ? reasonRaw.trim() : ''
  if (action === null) return redirectToSettings(instance, 'pause_invalid_action')
  if (reason === '') return redirectToSettings(instance, 'pause_reason_required')

  // Machine first, record second: never log a pause the Machine didn't take.
  let gateLevel: string
  try {
    if (action === 'pause') {
      const result = await setStopOnMachine(env, instance, {
        actor_id: access.user.email,
        reason,
      })
      gateLevel = result.level
    } else {
      const result = await resumeOnMachine(env, instance, {
        captain_id: access.user.email,
        reason,
      })
      gateLevel = result.level
    }
  } catch {
    return redirectToSettings(instance, 'pause_gate_unreachable')
  }

  await recordPauseEvent(env.DB, {
    entity_id: access.client.id,
    customer_slug: instance,
    action,
    actor_user_id: access.user.id,
    actor_email: access.user.email,
    actor_role: 'principal',
    source: 'portal',
    reason,
    gate_level: gateLevel,
  })

  return redirectToSettings(instance, action === 'pause' ? 'paused' : 'resumed')
}
