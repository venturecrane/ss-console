import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { getIntakeById, setIntakeStatus } from '../../../../../lib/db/hosted-agent-intake'
import { trimString, escapeHtml } from '../../../../../lib/api/helpers'
import { sendEmail } from '../../../../../lib/email/resend'
import { hostedAgentLiveEmailHtml } from '../../../../../lib/email/hosted-agent-templates'
import { getPortalBaseUrl } from '../../../../../lib/config/app-url'
import { COUPLES_ROLE, listPeople } from '../../../../../lib/db/couples-onboarding'
import { COUPLES_LANDING_PATH } from '../../../../../lib/portal/couples-access'

/**
 * POST /api/admin/hosted-agent/:id/activate (ADR 0067)
 *
 * The LAST concierge seam: after `reprovision.sh <slug>` and the boot
 * smoke test pass, the Captain flips the seat live here. This endpoint
 * does not provision anything: it promotes the subscription
 * (provisioning → active; deliberately the only code path that does),
 * closes the intake work item, and sends the go-live email (to the principal;
 * on a couples instance to BOTH partners, from their authored rows) whose channel
 * details the Captain authored in the form — the wrapper renders exactly
 * what was written, never templated channel copy.
 */

const QUEUE = '/admin/hosted-agent'

/** A couples instance page may ask to come back to itself; nothing else may. */
const COUPLES_RETURN = /^\/admin\/hosted-agent\/couples\/[0-9a-f-]{36}$/

function back(st: string, returnTo: string | null = null): Response {
  const base = returnTo && COUPLES_RETURN.test(returnTo) ? returnTo : QUEUE
  return new Response(null, { status: 303, headers: { Location: `${base}?st=${st}` } })
}

interface GoLiveRecipient {
  email: string
  name: string
}

/**
 * Who the go-live email reaches, from authored rows only: on a couples
 * instance BOTH partners, from the partner rows SMD authored at creation
 * (ADR 0090; a couples instance has no principal); otherwise the principal.
 */
async function goLiveRecipients(intake: {
  id: string
  entity_id: string
  role: string | null
}): Promise<{ recipients: GoLiveRecipient[]; portalPath: string }> {
  if (intake.role === COUPLES_ROLE) {
    const people = await listPeople(env.DB, intake.id)
    return {
      recipients: people.map((p) => ({ email: p.email, name: p.name })),
      portalPath: COUPLES_LANDING_PATH,
    }
  }
  const buyer = await env.DB.prepare(
    `SELECT u.email, u.name FROM product_roles r
         JOIN users u ON u.id = r.user_id
        WHERE r.entity_id = ? AND r.product_slug = 'hosted-agent'
          AND r.role = 'principal' AND r.revoked_at IS NULL
        ORDER BY r.granted_at ASC LIMIT 1`
  )
    .bind(intake.entity_id)
    .first<{ email: string; name: string | null }>()
  return {
    recipients: buyer?.email
      ? [{ email: buyer.email, name: buyer.name?.trim() || buyer.email }]
      : [],
    portalPath: '/portal/products/hosted-agent',
  }
}

/** Best-effort go-live email to each recipient. Never fails the action. */
async function sendGoLiveEmail(
  intake: { id: string; entity_id: string; agent_name: string | null; role: string | null },
  channelDetails: string
): Promise<void> {
  try {
    const { recipients, portalPath } = await goLiveRecipients(intake)
    const portalBase = getPortalBaseUrl(env) ?? 'https://portal.smd.services'
    const details = escapeHtml(channelDetails).replace(/\n/g, '<br>')
    for (const r of recipients) {
      try {
        await sendEmail(env.RESEND_API_KEY, {
          to: r.email,
          subject: `${intake.agent_name ?? 'Your agent'} is live`,
          html: hostedAgentLiveEmailHtml(r.name, details, `${portalBase}${portalPath}`),
        })
      } catch (err) {
        // One recipient's failure must not stop the other partner's email.
        console.error('[admin/hosted-agent] go-live email failed for one recipient:', err)
      }
    }
  } catch (err) {
    console.error('[admin/hosted-agent] go-live email failed:', err)
  }
}

export const POST: APIRoute = async ({ locals, params, request }) => {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response

  const id = params.id
  if (typeof id !== 'string' || id.length === 0) return back('invalid')

  const intake = await getIntakeById(env.DB, id)
  if (!intake || !intake.customer_slug) return back('invalid')
  if (intake.status === 'live' || intake.status === 'cancelled') return back('invalid')

  const form = await request.formData()
  const returnTo = trimString(form.get('return_to'))
  const channelDetails = trimString(form.get('channel_details'))
  if (!channelDetails || channelDetails.length > 2000) return back('invalid', returnTo)

  try {
    await env.DB.prepare(
      `UPDATE subscriptions SET status = 'active', updated_at = datetime('now')
          WHERE id = ? AND status = 'provisioning'`
    )
      .bind(intake.subscription_id)
      .run()
    // Persist the authored channel details: the live view renders them
    // verbatim for the life of the subscription (migration 0088) — the
    // go-live email must not be the only copy.
    await env.DB.prepare(
      `UPDATE hosted_agent_intake SET channel_details = ?, updated_at = datetime('now') WHERE id = ?`
    )
      .bind(channelDetails, id)
      .run()
    await setIntakeStatus(env.DB, id, 'live')
  } catch (err) {
    console.error('[admin/hosted-agent] activate failed:', err)
    return back('error', returnTo)
  }

  await sendGoLiveEmail(intake, channelDetails)
  return back('activated', returnTo)
}
