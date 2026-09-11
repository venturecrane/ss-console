import type { APIRoute } from 'astro'
import { resolveOperatorAccess } from '../../../../../lib/portal/operator-access'
import { applySkillToggle } from '../../../../../lib/portal/operator/config-governance'
import { env } from 'cloudflare:workers'
import { errorResponse } from '../../../../../lib/api/helpers'

/**
 * POST /api/portal/operator/settings/skill-toggle
 *
 * Enables or disables a single skill on the active persona (ADR 0026 / 0030 §4).
 *
 * Form fields:
 *   skillName     — slug of the skill being toggled
 *   personaSlug   — slug of the persona owning the skill (optional)
 *   nextEnabled   — 'true' or 'false' (the target state)
 *
 * Auth: principal only. Records the governance action to the immutable
 * `config_change_audit` ledger; does not mutate the live config replica
 * (ADR 0012 §2 — value applies on the next git sync). Status banner:
 *   ?status=saved      — recorded
 *   ?status=invalid_*  — malformed request
 */

const OPERATOR_LANDING = '/portal/products/operator'

// The settings page is now instance-addressed. Redirect back to the addressed
// instance's settings; fall back to the bare chooser when the instance can't be
// determined from the form.
function settingsUrl(instance: string | null): string {
  return instance ? `${OPERATOR_LANDING}/${instance}/settings` : OPERATOR_LANDING
}

function redirectToSettings(instance: string | null, status: string): Response {
  const base = settingsUrl(instance)
  const sep = base.includes('?') ? '&' : '?'
  return new Response(null, {
    status: 303,
    headers: { Location: `${base}${sep}status=${encodeURIComponent(status)}` },
  })
}

export const POST: APIRoute = async ({ locals, request }) => {
  const formData = await request.formData()
  const instanceRaw = formData.get('instance')
  const instance = typeof instanceRaw === 'string' && instanceRaw !== '' ? instanceRaw : null

  const access = await resolveOperatorAccess(env.DB, locals, {
    allowedRoles: ['principal'],
    customerSlug: instance ?? '',
  })
  if (access.kind === 'redirect') {
    return errorResponse(403, 'forbidden', 'Forbidden.')
  }

  const skillName = formData.get('skillName')
  const nextEnabled = formData.get('nextEnabled')
  const personaSlug = formData.get('personaSlug')

  if (typeof skillName !== 'string' || skillName === '') {
    return redirectToSettings(instance, 'invalid_skill')
  }
  if (nextEnabled !== 'true' && nextEnabled !== 'false') {
    return redirectToSettings(instance, 'invalid_state')
  }

  await applySkillToggle(env.DB, {
    customer_slug: access.customerSlug,
    entity_id: access.client.id,
    actor: { user_id: access.user.id, email: access.user.email, role: 'principal' },
    persona_slug: typeof personaSlug === 'string' && personaSlug !== '' ? personaSlug : null,
    skill_name: skillName,
    next_enabled: nextEnabled === 'true',
  })

  return redirectToSettings(instance, 'saved')
}
