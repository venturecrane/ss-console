import type { APIRoute } from 'astro'
import { createInvoice } from '../../../../lib/db/invoices'
import type { InvoiceType } from '../../../../lib/db/invoices'

const VALID_TYPES: InvoiceType[] = ['deposit', 'completion', 'milestone', 'assessment', 'retainer']

/**
 * POST /api/admin/invoices
 *
 * Creates a new invoice from form data.
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

  const env = locals.runtime.env

  try {
    const formData = await request.formData()

    const clientId = formData.get('client_id')
    const engagementId = formData.get('engagement_id')
    const type = formData.get('type')
    const amountStr = formData.get('amount')
    const description = formData.get('description')
    const dueDate = formData.get('due_date')
    const redirectUrl = formData.get('redirect_url')

    if (
      !clientId ||
      typeof clientId !== 'string' ||
      !type ||
      typeof type !== 'string' ||
      !amountStr ||
      typeof amountStr !== 'string'
    ) {
      const target = typeof redirectUrl === 'string' ? redirectUrl : '/admin/clients'
      return redirect(`${target}?error=missing`, 302)
    }

    if (!VALID_TYPES.includes(type as InvoiceType)) {
      const target = typeof redirectUrl === 'string' ? redirectUrl : '/admin/clients'
      return redirect(`${target}?error=invalid_type`, 302)
    }

    const amount = parseFloat(amountStr)
    if (isNaN(amount) || amount <= 0) {
      const target = typeof redirectUrl === 'string' ? redirectUrl : '/admin/clients'
      return redirect(`${target}?error=invalid_amount`, 302)
    }

    await createInvoice(env.DB, session.orgId, {
      entity_id: clientId,
      engagement_id: typeof engagementId === 'string' && engagementId.trim() ? engagementId : null,
      type: type as InvoiceType,
      amount,
      description:
        typeof description === 'string' && description.trim() ? description.trim() : null,
      due_date: typeof dueDate === 'string' && dueDate.trim() ? dueDate.trim() : null,
    })

    const target = typeof redirectUrl === 'string' ? redirectUrl : '/admin/clients'
    return redirect(`${target}?created=1`, 302)
  } catch (err) {
    console.error('[api/admin/invoices] Create error:', err)
    return redirect('/admin/clients?error=server', 302)
  }
}
