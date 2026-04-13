import type { APIRoute } from 'astro'
import { getQuote, updateQuote, updateQuoteStatus } from '../../../../lib/db/quotes'
import type { LineItem, QuoteStatus } from '../../../../lib/db/quotes'
import { getEntity } from '../../../../lib/db/entities'
import { listContacts } from '../../../../lib/db/contacts'
import type { SOWTemplateProps } from '../../../../lib/pdf/sow-template'
import { createSOWRevisionForQuote } from '../../../../lib/sow/service'

/**
 * POST /api/admin/quotes/:id
 *
 * Updates an existing quote from form data.
 * Handles multiple actions:
 * - action=update: update fields and recalculate totals
 * - action=generate-pdf: create an immutable SOW revision
 *
 * Protected by auth middleware (requires admin role).
 */
export const POST: APIRoute = async ({ request, locals, redirect, params }) => {
  const session = locals.session
  if (!session || session.role !== 'admin') {
    return new Response(JSON.stringify({ error: 'Unauthorized' }), {
      status: 401,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const quoteId = params.id
  if (!quoteId) {
    return new Response(JSON.stringify({ error: 'Quote ID required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    })
  }

  const env = locals.runtime.env

  try {
    const existing = await getQuote(env.DB, session.orgId, quoteId)
    if (!existing) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')

    // ----- ACTION: generate-pdf -----
    if (action === 'generate-pdf') {
      const entity = await getEntity(env.DB, session.orgId, existing.entity_id)
      if (!entity) {
        return redirect(
          `/admin/entities/${existing.entity_id}/quotes/${quoteId}?error=client_not_found`,
          302
        )
      }

      const contacts = await listContacts(env.DB, session.orgId, existing.entity_id)
      const primaryContact = contacts[0]

      const lineItems: LineItem[] = JSON.parse(existing.line_items)

      // Format currency
      const formatCurrency = (amount: number) =>
        `$${amount.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`

      // Format date
      const formatDate = (date: Date) =>
        date.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })

      const now = new Date()
      const expirationDate = new Date(now.getTime() + 5 * 24 * 60 * 60 * 1000)

      // Determine payment schedule
      const isThreeMilestone = existing.total_hours >= 40
      const depositPct = existing.deposit_pct
      const totalPrice = existing.total_price

      let paymentProps: SOWTemplateProps['payment']
      if (isThreeMilestone) {
        // 40% deposit, 30% mid-engagement, 30% completion
        paymentProps = {
          schedule: 'three_milestone',
          totalPrice: formatCurrency(totalPrice),
          deposit: formatCurrency(totalPrice * 0.4),
          milestone: formatCurrency(totalPrice * 0.3),
          milestoneLabel: 'mid-engagement milestone',
          completion: formatCurrency(totalPrice * 0.3),
        }
      } else {
        paymentProps = {
          schedule: 'two_part',
          totalPrice: formatCurrency(totalPrice),
          deposit: formatCurrency(totalPrice * depositPct),
          completion: formatCurrency(totalPrice * (1 - depositPct)),
        }
      }

      const templateProps: SOWTemplateProps = {
        client: {
          businessName: entity.name,
          contactName: primaryContact?.name ?? 'Business Owner',
          contactTitle: primaryContact?.title ?? undefined,
        },
        document: {
          date: formatDate(now),
          expirationDate: formatDate(expirationDate),
          sowNumber: 'PENDING',
        },
        engagement: {
          overview: 'Operations cleanup engagement as discussed during assessment.',
          startDate: 'TBD upon deposit',
          endDate: 'TBD based on scope',
        },
        items: lineItems.map((item) => ({
          name: item.problem,
          description: item.description,
        })),
        payment: paymentProps,
      }

      await createSOWRevisionForQuote({
        db: env.DB,
        storage: env.STORAGE,
        orgId: session.orgId,
        quote: existing,
        actorId: session.userId,
        templateProps,
      })

      return redirect(`/admin/entities/${existing.entity_id}/quotes/${quoteId}?saved=1`, 302)
    }

    // ----- ACTION: decline -----
    if (action === 'decline') {
      try {
        await updateQuoteStatus(env.DB, session.orgId, quoteId, 'declined' as QuoteStatus)
      } catch (err) {
        console.error('[api/admin/quotes/[id]] Decline error:', err)
        return redirect(
          `/admin/entities/${existing.entity_id}/quotes/${quoteId}?error=invalid_transition`,
          302
        )
      }
      return redirect(`/admin/entities/${existing.entity_id}/quotes/${quoteId}?saved=1`, 302)
    }

    // ----- ACTION: expire -----
    if (action === 'expire') {
      try {
        await updateQuoteStatus(env.DB, session.orgId, quoteId, 'expired' as QuoteStatus)
      } catch (err) {
        console.error('[api/admin/quotes/[id]] Expire error:', err)
        return redirect(
          `/admin/entities/${existing.entity_id}/quotes/${quoteId}?error=invalid_transition`,
          302
        )
      }
      return redirect(`/admin/entities/${existing.entity_id}/quotes/${quoteId}?saved=1`, 302)
    }

    // ----- ACTION: update (default) -----
    const lineItemsJson = formData.get('line_items')
    const depositPctStr = formData.get('deposit_pct')

    const updateData: Record<string, unknown> = {}

    if (lineItemsJson && typeof lineItemsJson === 'string') {
      try {
        const lineItems: LineItem[] = JSON.parse(lineItemsJson)
        if (Array.isArray(lineItems) && lineItems.length > 0) {
          updateData.lineItems = lineItems
        }
      } catch {
        // Invalid JSON — skip line items update
      }
    }

    if (depositPctStr && typeof depositPctStr === 'string') {
      const depositPct = parseFloat(depositPctStr)
      if (!isNaN(depositPct) && depositPct > 0 && depositPct <= 1) {
        updateData.depositPct = depositPct
      }
    }

    await updateQuote(env.DB, session.orgId, quoteId, updateData)

    return redirect(`/admin/entities/${existing.entity_id}/quotes/${quoteId}?saved=1`, 302)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    const stack = err instanceof Error ? err.stack : ''
    console.error('[api/admin/quotes/[id]] Update error:', msg, stack)
    return redirect(
      `/admin/entities?error=server&detail=${encodeURIComponent(msg.slice(0, 200))}`,
      302
    )
  }
}
