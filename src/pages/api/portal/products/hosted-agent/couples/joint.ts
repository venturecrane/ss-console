import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveCouplesAccess } from '../../../../../../lib/portal/couples-access'
import { confirmJointSettings, saveJointSettings } from '../../../../../../lib/db/couples-joint'
import { jointSettingsFromForm } from '../../../../../../lib/hosted-agent/couples/joint-settings'

/**
 * POST /api/portal/products/hosted-agent/couples/joint
 *
 * The joint section (ADR 0090 decision 4). Two actions, by the `action` field:
 *
 *   save     propose a new version; the saver's confirmation of it is recorded.
 *            Any earlier confirmation by the other partner stays on the earlier
 *            version, so the section is not final until they confirm again.
 *   confirm  confirm the version the partner was shown (`version`). A version
 *            that is no longer current is refused with `stale`.
 *
 * The confirming person is the caller's own partner row, from the session.
 */

const JOINT_PAGE = '/portal/products/hosted-agent/couples/joint'

function back(st: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${JOINT_PAGE}?st=${st}` } })
}

export const POST: APIRoute = async ({ locals, request }) => {
  const access = await resolveCouplesAccess(env.DB, locals)
  if (access.kind === 'redirect') {
    return new Response(null, { status: 303, headers: { Location: access.to } })
  }
  const { instance, person } = access
  // Agreeing to joint settings comes after agreeing to use the agent at all.
  if (person.consent_at === null) return back('consent_first')
  const form = await request.formData()
  const action = form.get('action')

  if (action === 'confirm') {
    const raw = form.get('version')
    const version = typeof raw === 'string' && /^\d{1,6}$/.test(raw) ? Number(raw) : NaN
    if (!Number.isInteger(version)) return back('invalid')
    const result = await confirmJointSettings(env.DB, instance.id, person.id, version)
    return back(result === 'confirmed' ? 'confirmed' : 'stale')
  }

  if (action === 'save') {
    const settings = jointSettingsFromForm(form)
    if (!settings) return back('invalid')
    await saveJointSettings(env.DB, instance.id, person.id, settings)
    return back('saved')
  }

  return back('invalid')
}
