import type { APIContext, APIRoute } from 'astro'
import {
  CARD_FEE_LINE_DESCRIPTION,
  cardProcessingFeeCents,
  createInvoice,
  isInvoiceType,
} from '../../../../lib/db/invoices'
import type { CreateInvoiceData } from '../../../../lib/db/invoices'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../lib/auth/admin-session'

/**
 * POST /api/admin/invoices
 *
 * Creates a new invoice from form data.
 *
 * Form fields:
 *   client_id, type, amount            — required
 *   description, due_date, engagement_id, redirect_url — optional
 *   line_item                          — optional. The authored line the
 *     client reads under "What's included". When present, one line item is
 *     written for the full amount alongside the invoice, so the invoice can
 *     be presented straight away (the send-gate refuses an invoice with no
 *     authored line). When absent the invoice is created as a bare draft and
 *     stays unsendable until a line is authored.
 *   card_payment                       — optional ('on'). The client asked to
 *     pay by card: a second line adds the 3% processing fee (agreement §3.8)
 *     and the invoice total includes it. Issue then offers card only; without
 *     this the invoice is ACH only. Requires line_item.
 *
 * Protected by auth middleware (requires admin role).
 */

function parseInvoiceForm(
  formData: FormData
): { clientId: string; type: string; amountStr: string; redirectUrl: string | null } | null {
  const clientId = formData.get('client_id')
  const type = formData.get('type')
  const amountStr = formData.get('amount')
  const redirectUrl = formData.get('redirect_url')
  if (
    !clientId ||
    typeof clientId !== 'string' ||
    !type ||
    typeof type !== 'string' ||
    !amountStr ||
    typeof amountStr !== 'string'
  ) {
    return null
  }
  return {
    clientId,
    type,
    amountStr,
    redirectUrl: typeof redirectUrl === 'string' ? redirectUrl : null,
  }
}

function optionalText(formData: FormData, key: string): string | null {
  const v = formData.get(key)
  return typeof v === 'string' && v.trim() ? v.trim() : null
}

/** The optional form fields, each empty → null; the line item becomes one
 * authored line for the full amount. */
export function optionalInvoiceFields(
  formData: FormData,
  amount: number
): Pick<CreateInvoiceData, 'engagement_id' | 'description' | 'due_date' | 'line_items' | 'amount'> {
  const lineItem = optionalText(formData, 'line_item')
  const amountCents = Math.round(amount * 100)
  const cardPayment = formData.get('card_payment') === 'on' && lineItem !== null
  const feeCents = cardPayment ? cardProcessingFeeCents(amountCents) : 0
  const lineItems = lineItem ? [{ description: lineItem, amount_cents: amountCents }] : []
  if (cardPayment)
    lineItems.push({ description: CARD_FEE_LINE_DESCRIPTION, amount_cents: feeCents })
  return {
    engagement_id: optionalText(formData, 'engagement_id'),
    description: optionalText(formData, 'description'),
    due_date: optionalText(formData, 'due_date'),
    line_items: lineItems,
    amount: (amountCents + feeCents) / 100,
  }
}

async function handlePost({ request, locals, redirect }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  try {
    const formData = await request.formData()
    const parsed = parseInvoiceForm(formData)
    const redirectUrl = formData.get('redirect_url')
    const defaultTarget = '/admin/entities'
    const target = typeof redirectUrl === 'string' ? redirectUrl : defaultTarget

    if (!parsed) {
      return redirect(`${target}?error=missing`, 302)
    }

    const { clientId, type, amountStr } = parsed

    if (!isInvoiceType(type)) {
      return redirect(`${target}?error=invalid_type`, 302)
    }

    const amount = parseFloat(amountStr)
    if (isNaN(amount) || amount <= 0) {
      return redirect(`${target}?error=invalid_amount`, 302)
    }

    await createInvoice(env.DB, session.orgId, {
      entity_id: clientId,
      type,
      // amount comes from optionalInvoiceFields: the fee-inclusive total when
      // the client pays by card, the entered amount otherwise.
      ...optionalInvoiceFields(formData, amount),
    })

    return redirect(`${target}?created=1`, 302)
  } catch (err) {
    console.error('[api/admin/invoices] Create error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}

export const POST: APIRoute = (ctx) => handlePost(ctx)
