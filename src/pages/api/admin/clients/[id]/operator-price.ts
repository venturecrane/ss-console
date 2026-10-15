import type { APIContext, APIRoute } from 'astro'
import {
  isOperatorPaymentMethod,
  OperatorSeatNotFoundError,
  parseOperatorLabel,
  setOperatorSeatCommercials,
} from '../../../../../lib/db/services'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../../lib/auth/admin-session'
import { captureError } from '../../../../../lib/observability/sentry'

/**
 * POST /api/admin/clients/[id]/operator-price
 *
 * Authors ONE Operator seat's commercial record on the spine (ADR 0046;
 * migrations 0113, 0126). The seat is named by the form field `instance`
 * (the seat's customer_slug) and must be a live operator seat of THIS
 * client: a slug from any other client is refused, never written.
 *
 *   monthly_price   dollars; empty clears it (back to unpriced)
 *   payment_method  `ach` | `card`; card adds the 3% fee line (agreement
 *                   §3.8) to checkout and to every monthly invoice
 *   label           the client-visible line name ("Marketing Operator");
 *                   empty clears it; absent leaves it as it was
 *
 * Writes ONLY the `services` commercial record and, for a seat with none
 * yet, the seat's link to it (`subscriptions.service_id`): never the seat's
 * status or Stripe attachment, which provisioning and billing own.
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
    const instance = formData.get('instance')
    if (typeof instance !== 'string' || instance.trim() === '') {
      return redirect(`/admin/clients/${entityId}?error=no_seat`, 302)
    }
    const parsed = parsePrice(formData.get('monthly_price'))
    if (!parsed.ok) {
      return redirect(`/admin/clients/${entityId}?error=bad_price`, 302)
    }
    const rawMethod = formData.get('payment_method')
    if (rawMethod !== null && !isOperatorPaymentMethod(rawMethod)) {
      return redirect(`/admin/clients/${entityId}?error=bad_payment_method`, 302)
    }
    const rawLabel = formData.get('label')
    const label = rawLabel === null ? null : parseOperatorLabel(rawLabel)
    if (label !== null && !label.ok) {
      return redirect(`/admin/clients/${entityId}?error=bad_label`, 302)
    }
    // One statement for the seat's price, rail and label: they land together
    // or not at all (code review 2026-09-10, Architecture 8).
    await setOperatorSeatCommercials(env.DB, session.orgId, entityId, instance.trim(), {
      price: parsed.value,
      method: rawMethod,
      ...(label === null ? {} : { label: label.value }),
    })
    return redirect(`/admin/clients/${entityId}?priced=1`, 302)
  } catch (err) {
    if (err instanceof OperatorSeatNotFoundError) {
      return redirect(`/admin/clients/${entityId}?error=no_seat`, 302)
    }
    console.error('[api/admin/clients/operator-price] error:', err)
    captureError(err, 'admin.operator-price')
    return redirect(`/admin/clients/${entityId}?error=server`, 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
