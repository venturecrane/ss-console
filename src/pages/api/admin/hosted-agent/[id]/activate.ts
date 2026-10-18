import type { APIRoute } from 'astro'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { getIntakeById, setIntakeStatus } from '../../../../../lib/db/hosted-agent-intake'
import { trimString, escapeHtml } from '../../../../../lib/api/helpers'
import { sendEmail } from '../../../../../lib/email/resend'
import { hostedAgentLiveEmailHtml } from '../../../../../lib/email/hosted-agent-templates'
import { getPortalBaseUrl } from '../../../../../lib/config/app-url'

/**
 * POST /api/admin/hosted-agent/:id/activate (ADR 0067)
 *
 * The LAST concierge seam: after `reprovision.sh <slug>` and the boot
 * smoke test pass, the Captain flips the seat live here. This endpoint
 * does not provision anything: it promotes the subscription
 * (provisioning → active; deliberately the only code path that does),
 * closes the intake work item, and sends the go-live email whose channel
 * details the Captain authored in the form — the wrapper renders exactly
 * what was written, never templated channel copy.
 */

const QUEUE = '/admin/hosted-agent'

function back(st: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${QUEUE}?st=${st}` } })
}

/** Best-effort go-live email to the principal contact. Never fails the action. */
async function sendGoLiveEmail(
  intake: { entity_id: string; agent_name: string | null },
  channelDetails: string
): Promise<void> {
  try {
    const buyer = await env.DB.prepare(
      `SELECT u.email, u.name FROM product_roles r
           JOIN users u ON u.id = r.user_id
          WHERE r.entity_id = ? AND r.product_slug = 'hosted-agent'
            AND r.role = 'principal' AND r.revoked_at IS NULL
          ORDER BY r.granted_at ASC LIMIT 1`
    )
      .bind(intake.entity_id)
      .first<{ email: string; name: string | null }>()
    if (!buyer?.email) return
    const portalBase = getPortalBaseUrl(env) ?? 'https://portal.smd.services'
    await sendEmail(env.RESEND_API_KEY, {
      to: buyer.email,
      subject: `${intake.agent_name ?? 'Your agent'} is live`,
      html: hostedAgentLiveEmailHtml(
        buyer.name?.trim() || buyer.email,
        escapeHtml(channelDetails).replace(/\n/g, '<br>'),
        `${portalBase}/portal/products/hosted-agent`
      ),
    })
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
  const channelDetails = trimString(form.get('channel_details'))
  if (!channelDetails || channelDetails.length > 2000) return back('invalid')

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
    return back('error')
  }

  await sendGoLiveEmail(intake, channelDetails)
  return back('activated')
}
