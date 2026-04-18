import type { APIRoute } from 'astro'
import { createQuote } from '../../../../lib/db/quotes'
import type { LineItem } from '../../../../lib/db/quotes'
import { env } from 'cloudflare:workers'

/**
 * POST /api/admin/quotes
 *
 * Creates a new quote from form data.
 * Line items are submitted as a JSON hidden field.
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ request, locals, redirect }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  try {
    const formData = await request.formData()

    const entityId = formData.get('entity_id')
    const assessmentId = formData.get('assessment_id')
    const lineItemsJson = formData.get('line_items')
    const rateStr = formData.get('rate')
    const depositPctStr = formData.get('deposit_pct')

    if (
      !entityId ||
      typeof entityId !== 'string' ||
      !assessmentId ||
      typeof assessmentId !== 'string' ||
      !lineItemsJson ||
      typeof lineItemsJson !== 'string' ||
      !rateStr ||
      typeof rateStr !== 'string'
    ) {
      return redirect(`/admin/entities/${entityId ?? ''}?error=missing`, 302)
    }

    let lineItems: LineItem[]
    try {
      lineItems = JSON.parse(lineItemsJson)
    } catch {
      return redirect(`/admin/entities/${entityId}?error=invalid_line_items`, 302)
    }

    if (!Array.isArray(lineItems) || lineItems.length === 0) {
      return redirect(`/admin/entities/${entityId}?error=missing_line_items`, 302)
    }

    const rate = parseFloat(rateStr)
    if (isNaN(rate) || rate <= 0) {
      return redirect(`/admin/entities/${entityId}?error=invalid_rate`, 302)
    }

    const depositPct = depositPctStr ? parseFloat(depositPctStr as string) : 0.5

    const quote = await createQuote(env.DB, session.orgId, {
      entityId,
      assessmentId,
      lineItems,
      rate,
      depositPct: isNaN(depositPct) ? 0.5 : depositPct,
    })

    return redirect(`/admin/entities/${entityId}/quotes/${quote.id}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/quotes] Create error:', err)
    return redirect('/admin/entities?error=server', 302)
  }
}
