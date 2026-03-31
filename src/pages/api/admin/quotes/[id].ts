import type { APIRoute } from 'astro'
import { getQuote, updateQuote, updateQuoteStatus } from '../../../../lib/db/quotes'
import type { LineItem, QuoteStatus } from '../../../../lib/db/quotes'
import { getClient } from '../../../../lib/db/clients'
import { listContacts } from '../../../../lib/db/contacts'
import { uploadPdf } from '../../../../lib/storage/r2'
import { renderSow } from '../../../../lib/pdf/render'
import type { SOWTemplateProps } from '../../../../lib/pdf/sow-template'
import { scheduleProposalCadence } from '../../../../lib/follow-ups/scheduler'

/**
 * POST /api/admin/quotes/:id
 *
 * Updates an existing quote from form data.
 * Handles multiple actions:
 * - action=update: update fields and recalculate totals
 * - action=generate-pdf: render SOW PDF, upload to R2, save path
 * - action=send: transition status to sent, set sent_at/expires_at
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
      return redirect('/admin/clients?error=not_found', 302)
    }

    const formData = await request.formData()
    const action = formData.get('action')

    // ----- ACTION: generate-pdf -----
    if (action === 'generate-pdf') {
      const client = await getClient(env.DB, session.orgId, existing.client_id)
      if (!client) {
        return redirect(
          `/admin/clients/${existing.client_id}/quotes/${quoteId}?error=client_not_found`,
          302
        )
      }

      const contacts = await listContacts(env.DB, session.orgId, existing.client_id)
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

      // Generate SOW number: SOW-YYYYMM-NNN
      const month = String(now.getMonth() + 1).padStart(2, '0')
      const sowNumber = `SOW-${now.getFullYear()}${month}-001`

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
          businessName: client.business_name,
          contactName: primaryContact?.name ?? 'Business Owner',
          contactTitle: primaryContact?.title ?? undefined,
        },
        document: {
          date: formatDate(now),
          expirationDate: formatDate(expirationDate),
          sowNumber,
        },
        engagement: {
          overview:
            existing.notes ?? 'Operations cleanup engagement as discussed during assessment.',
          startDate: 'TBD upon deposit',
          endDate: 'TBD based on scope',
        },
        items: lineItems.map((item) => ({
          name: item.problem,
          description: item.description,
        })),
        payment: paymentProps,
        smd: {
          signerName: 'Scott Durgan',
          signerTitle: 'Principal',
        },
      }

      const pdf = await renderSow(templateProps)
      const sowPath = await uploadPdf(env.STORAGE, session.orgId, quoteId, pdf)
      await updateQuote(env.DB, session.orgId, quoteId, { sow_path: sowPath })

      return redirect(`/admin/clients/${existing.client_id}/quotes/${quoteId}?saved=1`, 302)
    }

    // ----- ACTION: send -----
    if (action === 'send') {
      try {
        await updateQuoteStatus(env.DB, session.orgId, quoteId, 'sent' as QuoteStatus)
      } catch (err) {
        console.error('[api/admin/quotes/[id]] Send error:', err)
        return redirect(
          `/admin/clients/${existing.client_id}/quotes/${quoteId}?error=invalid_transition`,
          302
        )
      }

      // Schedule proposal follow-up cadence (Decision #19: Day 2, Day 5, Day 7)
      try {
        const sentAt = new Date().toISOString()
        await scheduleProposalCadence(env.DB, session.orgId, quoteId, existing.client_id, sentAt)
      } catch (err) {
        // Non-blocking — log but don't fail the send action
        console.error('[api/admin/quotes/[id]] Follow-up scheduling error:', err)
      }

      return redirect(`/admin/clients/${existing.client_id}/quotes/${quoteId}?saved=1`, 302)
    }

    // ----- ACTION: update (default) -----
    const lineItemsJson = formData.get('line_items')
    const depositPctStr = formData.get('deposit_pct')
    const notes = formData.get('notes')

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

    if (notes !== null && notes !== undefined) {
      updateData.notes = typeof notes === 'string' ? notes.trim() || null : null
    }

    await updateQuote(env.DB, session.orgId, quoteId, updateData)

    return redirect(`/admin/clients/${existing.client_id}/quotes/${quoteId}?saved=1`, 302)
  } catch (err) {
    console.error('[api/admin/quotes/[id]] Update error:', err)
    return redirect('/admin/clients?error=server', 302)
  }
}
