import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { createCouplesInstance } from '../../../../../lib/db/couples-onboarding'
import { isValidEmail, trimString } from '../../../../../lib/api/helpers'
import { normalizeEmail } from '../../../../../lib/identity/email'
import { captureError } from '../../../../../lib/observability/sentry'

/**
 * POST /api/admin/hosted-agent/couples (ADR 0090 decision 4)
 *
 * SMD creates a couples instance: the entity, an SMD-paid `hosted-agent`
 * subscription at `provisioning` (no checkout, no Stripe), the intake work
 * item with `role = 'couples'`, and one partner row per person. Nothing is
 * sent: inviting each partner is a separate, explicit action on the instance
 * page.
 *
 * Form: couple_name, agent_name, p1_name, p1_email, p2_name, p2_email.
 */

const LIST = '/admin/hosted-agent/couples'

function back(location: string): Response {
  return new Response(null, { status: 303, headers: { Location: location } })
}

interface ParsedForm {
  coupleName: string
  agentName: string
  partners: [{ name: string; email: string }, { name: string; email: string }]
}

function partner(form: FormData, key: 'p1' | 'p2'): { name: string; email: string } | null {
  const name = trimString(form.get(`${key}_name`))
  const rawEmail = trimString(form.get(`${key}_email`))
  if (!name || name.length > 80 || !rawEmail) return null
  const email = normalizeEmail(rawEmail)
  return isValidEmail(email) ? { name, email } : null
}

function parseCouplesInstanceForm(form: FormData): ParsedForm | null {
  const coupleName = trimString(form.get('couple_name'))
  const agentName = trimString(form.get('agent_name'))
  if (!coupleName || coupleName.length > 120) return null
  if (!agentName || agentName.length > 40 || !/^[A-Za-z][A-Za-z '-]*$/.test(agentName)) return null
  const p1 = partner(form, 'p1')
  const p2 = partner(form, 'p2')
  if (!p1 || !p2 || p1.email === p2.email) return null
  return { coupleName, agentName, partners: [p1, p2] }
}

export const POST: APIRoute = async ({ locals, request }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response

  const parsed = parseCouplesInstanceForm(await request.formData())
  if (!parsed) return back(`${LIST}?st=invalid`)

  try {
    const id = await createCouplesInstance(env.DB, parsed)
    return back(`${LIST}/${id}?st=created`)
  } catch (err) {
    console.error('[admin/couples] create failed:', err)
    captureError(err, 'admin.couples.create')
    return back(`${LIST}?st=error`)
  }
}
