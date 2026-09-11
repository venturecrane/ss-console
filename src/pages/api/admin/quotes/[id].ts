import type { APIContext, APIRoute } from 'astro'
import { getQuote, updateQuote, updateQuoteStatus } from '../../../../lib/db/quotes'
import { parseDeliverables, parseLineItems } from '../../../../lib/db/quote-content'
import type { LineItem, DeliverableRow } from '../../../../lib/db/quotes'
import { getEntity } from '../../../../lib/db/entities'
import { listContacts } from '../../../../lib/db/contacts'
import { getSignalById } from '../../../../lib/db/signal-attribution'
import type { SOWTemplateProps } from '../../../../lib/pdf/sow-template'
import { createSOWRevisionForQuote } from '../../../../lib/sow/service'
import { env } from 'cloudflare:workers'
import { requireAdminSession } from '../../../../lib/auth/admin-session'
import { errorResponse } from '../../../../lib/api/helpers'

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

type Redirect = APIContext['redirect']
type Quote = NonNullable<Awaited<ReturnType<typeof getQuote>>>

function formatCurrency(amount: number): string {
  return `$${amount.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

function formatDate(date: Date): string {
  return date.toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' })
}

function buildPaymentProps(existing: Quote): SOWTemplateProps['payment'] {
  const isThreeMilestone = existing.total_hours >= 40
  const depositPct = existing.deposit_pct
  const totalPrice = existing.total_price

  if (isThreeMilestone) {
    return {
      schedule: 'three_milestone',
      totalPrice: formatCurrency(totalPrice),
      deposit: formatCurrency(totalPrice * 0.4),
      milestone: formatCurrency(totalPrice * 0.3),
      ...(existing.milestone_label?.trim()
        ? { milestoneLabel: existing.milestone_label.trim() }
        : {}),
      completion: formatCurrency(totalPrice * 0.3),
    }
  }
  return {
    schedule: 'two_part',
    totalPrice: formatCurrency(totalPrice),
    deposit: formatCurrency(totalPrice * depositPct),
    completion: formatCurrency(totalPrice * (1 - depositPct)),
  }
}

function buildSowItems(
  lineItems: LineItem[],
  authoredDeliverables: DeliverableRow[]
): Array<{ name: string; description: string }> {
  if (authoredDeliverables.length > 0) {
    return authoredDeliverables.map((row) => ({ name: row.title, description: row.body }))
  }
  return lineItems.map((item) => ({ name: item.problem, description: item.description }))
}

async function handleGeneratePdf(
  redirect: Redirect,
  orgId: string,
  userId: string,
  existing: Quote
): Promise<Response> {
  const quoteId = existing.id
  const quoteUrl = `/admin/entities/${existing.entity_id}/quotes/${quoteId}`

  const entity = await getEntity(env.DB, orgId, existing.entity_id)
  if (!entity) {
    return redirect(`${quoteUrl}?error=client_not_found`, 302)
  }

  const contacts = await listContacts(env.DB, orgId, existing.entity_id)
  const primaryContact = contacts[0]

  if (!primaryContact?.name?.trim()) {
    return redirect(
      `${quoteUrl}?error=${encodeURIComponent('Cannot generate SOW: add a primary contact with a name before generating the PDF.')}`,
      302
    )
  }

  if (!existing.engagement_overview?.trim()) {
    return redirect(
      `${quoteUrl}?error=${encodeURIComponent('Cannot generate SOW: author the engagement overview on this quote before generating the PDF.')}`,
      302
    )
  }

  const lineItems: LineItem[] = parseLineItems(existing.line_items)
  const authoredDeliverables: DeliverableRow[] = parseDeliverables(existing)
  const sowItems = buildSowItems(lineItems, authoredDeliverables)

  const now = new Date()
  const expirationDate = new Date(now.getTime() + 5 * 24 * 60 * 60 * 1000)

  const templateProps: SOWTemplateProps = {
    client: {
      businessName: entity.name,
      contactName: primaryContact.name,
      contactTitle: primaryContact?.title ?? undefined,
    },
    document: {
      date: formatDate(now),
      expirationDate: formatDate(expirationDate),
      sowNumber: 'PENDING',
    },
    engagement: {
      overview: existing.engagement_overview.trim(),
      startDate: 'TBD upon deposit',
      endDate: 'TBD based on scope',
    },
    items: sowItems,
    payment: buildPaymentProps(existing),
  }

  await createSOWRevisionForQuote({
    db: env.DB,
    storage: env.STORAGE,
    orgId,
    quote: existing,
    actorId: userId,
    templateProps,
  })

  return redirect(`${quoteUrl}?saved=1`, 302)
}

function parseJsonArray<T extends object>(
  raw: FormDataEntryValue | null,
  mapFn: (row: Record<string, unknown>) => T
): T[] | undefined {
  if (typeof raw !== 'string') return undefined
  try {
    const parsed: unknown = raw.trim() === '' ? [] : JSON.parse(raw)
    if (!Array.isArray(parsed)) return undefined
    return parsed
      .filter((row): row is Record<string, unknown> => row !== null && typeof row === 'object')
      .map(mapFn)
      .filter((row) => Object.values(row).some((v) => typeof v === 'string' && v.length > 0))
  } catch {
    return undefined
  }
}

function parseLineItemsField(formData: FormData): LineItem[] | undefined {
  const raw = formData.get('line_items')
  if (!raw || typeof raw !== 'string') return undefined
  // parseLineItems parses + validates each element against the LineItem shape
  // (never casts, never throws). An empty result means the field was absent,
  // malformed, or had no valid rows — in all cases we skip the update so a
  // bad payload can't blank out a quote's line items. Returns undefined to
  // signal "leave unchanged" to buildUpdateData.
  const parsed = parseLineItems(raw)
  return parsed.length > 0 ? parsed : undefined
}

function parseDepositPct(formData: FormData): number | undefined {
  const raw = formData.get('deposit_pct')
  if (!raw || typeof raw !== 'string') return undefined
  const v = parseFloat(raw)
  return !isNaN(v) && v > 0 && v <= 1 ? v : undefined
}

async function resolveSignalId(
  formData: FormData,
  orgId: string,
  entityId: string
): Promise<string | null | undefined> {
  const signalRaw = formData.get('originating_signal_id')
  if (typeof signalRaw !== 'string') return undefined
  const v = signalRaw.trim()
  if (v === '__none__') return null
  if (v === '') return undefined
  const signal = await getSignalById(env.DB, orgId, v)
  return signal && signal.entity_id === entityId ? signal.id : undefined
}

async function buildUpdateData(
  formData: FormData,
  orgId: string,
  entityId: string
): Promise<Record<string, unknown>> {
  const updateData: Record<string, unknown> = {}

  const lineItems = parseLineItemsField(formData)
  if (lineItems !== undefined) updateData.lineItems = lineItems

  const depositPct = parseDepositPct(formData)
  if (depositPct !== undefined) updateData.depositPct = depositPct

  const scheduleRows = parseJsonArray(formData.get('schedule'), (row) => ({
    label: typeof row.label === 'string' ? row.label.trim() : '',
    body: typeof row.body === 'string' ? row.body.trim() : '',
  }))
  if (scheduleRows !== undefined) updateData.schedule = scheduleRows

  const deliverableRows = parseJsonArray(formData.get('deliverables'), (row) => ({
    title: typeof row.title === 'string' ? row.title.trim() : '',
    body: typeof row.body === 'string' ? row.body.trim() : '',
  }))
  if (deliverableRows !== undefined) updateData.deliverables = deliverableRows

  const engagementOverview = formData.get('engagement_overview')
  if (typeof engagementOverview === 'string') {
    updateData.engagementOverview = engagementOverview
  }

  const milestoneLabel = formData.get('milestone_label')
  if (typeof milestoneLabel === 'string') {
    updateData.milestoneLabel = milestoneLabel
  }

  const signalId = await resolveSignalId(formData, orgId, entityId)
  if (signalId !== undefined) updateData.originatingSignalId = signalId

  return updateData
}

async function handleStatusAction(
  redirect: Redirect,
  orgId: string,
  quoteId: string,
  entityId: string,
  action: string
): Promise<Response | null> {
  const quoteUrl = `/admin/entities/${entityId}/quotes/${quoteId}`
  const validStatuses: Record<string, string> = {
    decline: 'declined',
    expire: 'expired',
    send: 'sent',
  }

  const newStatus = validStatuses[action]
  if (!newStatus) return null

  try {
    await updateQuoteStatus(
      env.DB,
      orgId,
      quoteId,
      newStatus as Parameters<typeof updateQuoteStatus>[3]
    )
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    console.error(`[api/admin/quotes/[id]] ${action} error:`, err)
    return redirect(`${quoteUrl}?error=${encodeURIComponent(msg)}`, 302)
  }

  return redirect(`${quoteUrl}?saved=1`, 302)
}

async function handlePost({ request, locals, redirect, params }: APIContext): Promise<Response> {
  const auth = requireAdminSession(locals)
  if (!auth.ok) return auth.response
  const { session } = auth

  const quoteId = params.id
  if (!quoteId) {
    return errorResponse(400, 'Quote ID required')
  }

  try {
    const existing = await getQuote(env.DB, session.orgId, quoteId)
    if (!existing) {
      return redirect('/admin/entities?error=not_found', 302)
    }

    const formData = await request.formData()
    const actionRaw = formData.get('action')
    const action = typeof actionRaw === 'string' ? actionRaw : ''

    if (action === 'generate-pdf') {
      return handleGeneratePdf(redirect, session.orgId, session.userId, existing)
    }

    const statusResult = await handleStatusAction(
      redirect,
      session.orgId,
      quoteId,
      existing.entity_id,
      action
    )
    if (statusResult) return statusResult

    // Default: update action
    const updateData = await buildUpdateData(formData, session.orgId, existing.entity_id)
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

export const POST: APIRoute = (ctx) => handlePost(ctx)
