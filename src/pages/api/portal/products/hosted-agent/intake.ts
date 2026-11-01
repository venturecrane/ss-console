import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { resolveHostedAgentAccess } from '../../../../../lib/portal/hosted-agent-access'
import {
  getIntakeByEntity,
  submitHostedAgentIntake,
} from '../../../../../lib/db/hosted-agent-intake'
import { isValidEmail, trimString } from '../../../../../lib/api/helpers'
import { sendEmail } from '../../../../../lib/email/resend'
import { hostedAgentIntakeNotificationEmailHtml } from '../../../../../lib/email/hosted-agent-templates'
import { getAdminBaseUrl } from '../../../../../lib/config/app-url'

/**
 * POST /api/portal/products/hosted-agent/intake (ADR 0067)
 *
 * Persists the setup questionnaire. Plain 0-JS form; 303s back to the
 * product landing with a `?st=` status the surface renders as a banner.
 * Gated on the principal role; only rows still in the pre-provisioning
 * statuses accept writes (the UPDATE itself is status-guarded too).
 */

const LANDING = '/portal/products/operator/setup'

function back(st: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${LANDING}?st=${st}` } })
}

interface ParsedIntakeForm {
  agentName: string
  useCases: string
  telegramHandle: string | null
  timezone: string
  allowedSenders: string[]
}

/** Parse + validate the questionnaire form. Null means invalid. */
function parseIntakeForm(form: FormData): ParsedIntakeForm | null {
  const agentName = trimString(form.get('agent_name'))
  const useCases = trimString(form.get('use_cases'))
  const timezone = trimString(form.get('timezone'))
  if (!agentName || agentName.length > 64) return null
  if (!useCases || useCases.length > 4000) return null
  if (!timezone || timezone.length > 64) return null
  if (form.get('spend_limit_confirmed') !== '1') return null

  const senders = (trimString(form.get('allowed_senders')) ?? '')
    .split(/[\n,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s.length > 0)
  if (senders.length === 0 || senders.length > 20) return null
  if (!senders.every((s) => isValidEmail(s))) return null

  return {
    agentName,
    useCases,
    telegramHandle: trimString(form.get('telegram_handle')),
    timezone,
    allowedSenders: senders,
  }
}

export const POST: APIRoute = async ({ locals, request }) => {
  const access = await resolveHostedAgentAccess(env.DB, locals, { allowedRoles: ['principal'] })
  if (access.kind === 'redirect') {
    return new Response(null, { status: 303, headers: { Location: access.to } })
  }

  const intake = await getIntakeByEntity(env.DB, access.client.id)
  if (!intake) return back('error')

  const parsed = parseIntakeForm(await request.formData())
  if (!parsed) return back('invalid')

  try {
    await submitHostedAgentIntake(env.DB, intake.id, {
      ...parsed,
      spendLimitConfirmed: true,
    })
  } catch (err) {
    console.error('[hosted-agent/intake] save failed:', err)
    return back('error')
  }

  // Best-effort concierge baton: tell team@ the questionnaire landed and
  // provisioning can start. Never blocks the customer's save.
  try {
    const adminBase = getAdminBaseUrl(env) ?? 'https://admin.smd.services'
    await sendEmail(env.RESEND_API_KEY, {
      to: 'team@smd.services',
      subject: `Self-serve intake submitted: ${access.client.name}`,
      html: hostedAgentIntakeNotificationEmailHtml({
        entityName: access.client.name,
        agentName: parsed.agentName,
        adminQueueUrl: `${adminBase}/admin/operator/intake`,
      }),
    })
  } catch (err) {
    console.error('[hosted-agent/intake] team notification failed:', err)
  }
  return back('saved')
}
