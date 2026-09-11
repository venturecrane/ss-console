import type { APIContext, APIRoute } from 'astro'
import {
  isOperatorPaymentMethod,
  setOperatorPriceAndPaymentMethod,
} from '../../../../../lib/db/services'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { captureError } from '../../../../../lib/observability/sentry'

/**
 * POST /api/admin/clients/[id]/operator-price
 *
 * Authors the operator's monthly recurring price on the commercial spine
 * (ADR 0046). The form field `monthly_price` is the dollar amount; an empty
 * value clears it (back to unpriced). The optional `payment_method` field
 * (`ach` | `card`, migration 0113) authors the rail the retainer is
 * collected by; card adds the 3% fee line (agreement §3.8) to checkout and
 * to every monthly invoice. Writes ONLY the `services` commercial record —
 * never `subscriptions` (that is provisioning's, and gates portal access).
 * Admin-gated. Redirects back to the client hub.
 */

/** Parse the submitted price: '' → null (clear); otherwise a finite, non-negative number. */
function parsePrice(
  raw: FormDataEntryValue | null
): { ok: true; value: number | null } | { ok: false } {
  if (typeof raw !== 'string' || raw.trim() === '') return { ok: true, value: null }
  const n = Number(raw.trim().replace(/[$,]/g, ''))
  if (!Number.isFinite(n) || n < 0) return { ok: false }
  return { ok: true, value: n }
}

async function handlePost({ request, locals, params, redirect }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const entityId = params.id
  if (!entityId) return redirect('/admin/clients?error=missing', 302)

  try {
    const formData = await request.formData()
    const parsed = parsePrice(formData.get('monthly_price'))
    if (!parsed.ok) {
      return redirect(`/admin/clients/${entityId}?error=bad_price`, 302)
    }
    const rawMethod = formData.get('payment_method')
    if (rawMethod !== null && !isOperatorPaymentMethod(rawMethod)) {
      return redirect(`/admin/clients/${entityId}?error=bad_payment_method`, 302)
    }
    // One statement for both fields: the price and the rail land together or
    // not at all (code review 2026-09-10, Architecture 8).
    await setOperatorPriceAndPaymentMethod(env.DB, session.orgId, entityId, parsed.value, rawMethod)
    return redirect(`/admin/clients/${entityId}?priced=1`, 302)
  } catch (err) {
    console.error('[api/admin/clients/operator-price] error:', err)
    captureError(err, 'admin.operator-price')
    return redirect(`/admin/clients/${entityId}?error=server`, 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
