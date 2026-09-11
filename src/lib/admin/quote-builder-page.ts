import { getEntity } from '../db/entities'
import {
  getMissingAuthoredContent,
  getQuote,
  parseDeliverables,
  parseSchedule,
  QUOTE_STATUSES,
  VALID_TRANSITIONS,
} from '../db/quotes'
import {
  parseLineItems,
  type DeliverableRow,
  type LineItem,
  type QuoteStatus,
  type ScheduleRow,
} from '../db/quotes'
import { listContacts } from '../db/contacts'
import { listSignalsForEntity } from '../db/signal-attribution'
import { getSOWStateForQuote } from '../sow/service'

type Database = Parameters<typeof getEntity>[0]

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function formatCurrency(amount: number): string {
  return `$${amount.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
}

export function formatSignalLabel(signal: {
  source_pipeline: string
  created_at: string
  content: string
}): string {
  const date = new Date(signal.created_at).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
  const oneLine = signal.content.replace(/\s+/g, ' ').trim()
  const snippet = oneLine.length > 80 ? `${oneLine.slice(0, 79)}…` : oneLine
  return `[${signal.source_pipeline}] ${date} - ${snippet}`
}

export async function loadQuoteBuilderPage(params: {
  db: Database
  orgId: string
  entityId: string
  quoteId: string
  url: URL
}) {
  const [entity, quote, contacts, entitySignals] = await Promise.all([
    getEntity(params.db, params.orgId, params.entityId),
    getQuote(params.db, params.orgId, params.quoteId),
    listContacts(params.db, params.orgId, params.entityId),
    listSignalsForEntity(params.db, params.orgId, params.entityId),
  ])

  if (!entity) return { missing: 'entity' as const }
  if (!quote) return { missing: 'quote' as const }

  const lineItems: LineItem[] = parseLineItems(quote.line_items)
  const status = quote.status as QuoteStatus
  const validTransitions = VALID_TRANSITIONS[status] ?? []
  const isDraft = status === 'draft'
  const isTerminal = validTransitions.length === 0
  const schedule: ScheduleRow[] = parseSchedule(quote)
  const deliverables: DeliverableRow[] = parseDeliverables(quote)
  const engagementOverview = quote.engagement_overview ?? ''
  const milestoneLabel = quote.milestone_label ?? ''
  const missingAuthored = getMissingAuthoredContent(quote)

  const sowState = await getSOWStateForQuote(params.db, params.orgId, params.quoteId)
  const latestSowRevision = sowState.latestRevision
  const activeSignatureRequest = sowState.openSignatureRequest
  const hasSow = !!latestSowRevision
  const sowGeneratedAt = latestSowRevision ? new Date(latestSowRevision.rendered_at) : null
  const isSowStale = !!latestSowRevision && latestSowRevision.quote_version !== quote.version
  const expiresAt = quote.expires_at ? new Date(quote.expires_at) : null
  const isExpired = expiresAt && expiresAt < new Date()
  const isThreeMilestone = quote.total_hours >= 40
  const depositPct = quote.deposit_pct

  return {
    entity,
    quote,
    contacts,
    entitySignals,
    lineItems,
    status,
    validTransitions,
    isDraft,
    isTerminal,
    schedule,
    deliverables,
    engagementOverview,
    milestoneLabel,
    missingAuthored,
    latestSowRevision,
    activeSignatureRequest,
    hasSow,
    sowGeneratedAt,
    isSowStale,
    expiresAt,
    isExpired,
    isThreeMilestone,
    depositPct,
    saved: params.url.searchParams.get('saved'),
    error: params.url.searchParams.get('error'),
    QUOTE_STATUSES,
  }
}
