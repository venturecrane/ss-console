import { hasOpenQuoteForEntity, listQuotes } from '../db/quotes'
import { getEntity } from '../db/entities'
import type { EntityStage } from '../db/entities'
import { listContext } from '../db/context'
import { listContacts } from '../db/contacts'
import { listMeetings } from '../db/meetings'
import { listEngagements } from '../db/engagements'
import { listInvoices } from '../db/invoices'
import { findDraftableMeeting } from '../../lib/entities/draftable-meeting'

type Database = Parameters<typeof getEntity>[0]
type EntityRecord = Exclude<Awaited<ReturnType<typeof getEntity>>, null>

export type EntityDetailTransition = {
  label: string
  stage: EntityStage
  variant: 'primary' | 'destructive'
  action?: string
}

const RE_ENRICH_STAGES: EntityStage[] = [
  'prospect',
  'meetings',
  'proposing',
  'engaged',
  'delivered',
  'ongoing',
]

export const ENTITY_DETAIL_TRANSITIONS: Record<EntityStage, EntityDetailTransition[]> = {
  signal: [
    {
      label: 'Promote',
      stage: 'prospect',
      variant: 'primary',
    },
    {
      label: 'Dismiss',
      stage: 'lost',
      variant: 'destructive',
    },
  ],
  prospect: [{ label: 'Lost', stage: 'lost', variant: 'destructive' }],
  meetings: [
    { label: 'Mark as Proposing', stage: 'proposing', variant: 'primary' },
    { label: 'Lost', stage: 'lost', variant: 'destructive' },
  ],
  proposing: [
    { label: 'Mark as Engaged', stage: 'engaged', variant: 'primary' },
    { label: 'Lost', stage: 'lost', variant: 'destructive' },
  ],
  engaged: [{ label: 'Mark as Delivered', stage: 'delivered', variant: 'primary' }],
  delivered: [
    { label: 'Mark as Ongoing', stage: 'ongoing', variant: 'primary' },
    { label: 'Re-engage', stage: 'prospect', variant: 'destructive' },
  ],
  ongoing: [
    { label: 'Re-engage', stage: 'prospect', variant: 'primary' },
    { label: 'Lost', stage: 'lost', variant: 'destructive' },
  ],
  lost: [{ label: 'Re-engage', stage: 'prospect', variant: 'primary' }],
}

export function contextTypeBadge(_type?: string): string {
  return 'bg-border-subtle text-text-secondary'
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function isOverdue(iso: string | null): boolean {
  if (!iso) return false
  return new Date(iso).getTime() < Date.now()
}

export function parseMetadata(json: string | null): Record<string, unknown> | null {
  if (!json) return null
  try {
    return JSON.parse(json) as Record<string, unknown>
  } catch {
    return null
  }
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function renderSimpleMarkdown(md: string): string {
  let html = escapeHtml(md)

  html = html.replace(
    /^## (.+)$/gm,
    (_match, title) =>
      `<h4 class="font-semibold text-sm text-[color:var(--ss-color-text-primary)] mt-4 mb-1">${title}</h4>`
  )
  html = html.replace(/\*\*(.+?)\*\*/g, (_match, value) => `<strong>${value}</strong>`)
  html = html.replace(
    /^- (.+)$/gm,
    (_match, value) =>
      `<li class="ml-4 list-disc text-sm text-[color:var(--ss-color-text-primary)]">${value}</li>`
  )
  html = html.replace(
    /^\d+\. (.+)$/gm,
    (_match, value) =>
      `<li class="ml-4 list-decimal text-sm text-[color:var(--ss-color-text-primary)]">${value}</li>`
  )
  html = html.replace(
    /((?:<li class="ml-4 list-disc[^>]*>[^<]*<\/li>\n?)+)/g,
    (_match, value) => `<ul class="mb-2">${value}</ul>`
  )
  html = html.replace(
    /((?:<li class="ml-4 list-decimal[^>]*>[^<]*<\/li>\n?)+)/g,
    (_match, value) => `<ol class="mb-2">${value}</ol>`
  )
  html = html.replace(
    /^(?!<[hulo])(.+)$/gm,
    (_match, value) =>
      `<p class="text-sm text-[color:var(--ss-color-text-primary)] mb-2">${value}</p>`
  )
  html = html.replace(
    /<p class="text-sm text-\[color:var\(--color-text-primary\)\] mb-2"><\/p>/g,
    ''
  )

  return html
}

export function sentimentColor(trend: string): string {
  if (trend === 'improving') return 'text-text-primary bg-surface'
  if (trend === 'declining') return 'text-error bg-surface'
  return 'text-text-secondary bg-background'
}

export function confidenceColor(confidence?: string): string {
  if (confidence === 'high') return 'bg-surface text-text-primary border border-border'
  if (confidence === 'medium') return 'bg-background text-text-secondary border border-border'
  return 'bg-border-subtle text-text-secondary'
}

export async function loadEntityDetailPage(params: {
  db: Database
  orgId: string
  entityId: string
  url: URL
}): Promise<{
  entity: EntityRecord
  contextEntries: Awaited<ReturnType<typeof listContext>>
  contacts: Awaited<ReturnType<typeof listContacts>>
  meetings: Awaited<ReturnType<typeof listMeetings>>
  engagements: Awaited<ReturnType<typeof listEngagements>>
  quotes: Awaited<ReturnType<typeof listQuotes>>
  invoices: Awaited<ReturnType<typeof listInvoices>>
  mostRecentDraftableMeeting: ReturnType<typeof findDraftableMeeting>
  hasOutreach: boolean
  filteredEntries: Awaited<ReturnType<typeof listContext>>
  typeFilter: string
  typeCounts: Record<string, number>
  currentLostReason: { code: string; detail: string | null } | null
  promoted: string | null
  noteAdded: string | null
  replyLogged: string | null
  stageUpdated: string | null
  dossierGenerated: string | null
  contactAdded: string | null
  contactUpdated: string | null
  contactDeleted: string | null
  error: string | null
  showReEnrichButton: boolean
  showNewQuoteButton: boolean
  supersedeCandidates: Awaited<ReturnType<typeof listQuotes>>
  transitions: EntityDetailTransition[]
  dossierBrief: Awaited<ReturnType<typeof listContext>>[number] | undefined
  outreachEntry: Awaited<ReturnType<typeof listContext>>[number] | undefined
  outreachContact: Awaited<ReturnType<typeof listContacts>>[number] | null
  outreachMailto: string | null
  outreachFromDossier: unknown
  hasDossier: boolean
  lastEnrichmentAt: string | null
  latestSentQuoteAt: string | null
  reviewMeta: {
    unified_rating?: number | null
    total_reviews_across_platforms?: number
    sentiment_trend?: string
    top_themes?: string[]
    operational_problems?: Array<{ problem: string; confidence: string; evidence: string }>
  } | null
  websiteMeta: { digital_maturity?: { score: number; reasoning: string } } | null
  competitorMeta: { entity_rank_by_rating?: number | null; total_competitors?: number } | null
}> {
  const entity = await getEntity(params.db, params.orgId, params.entityId)
  if (!entity) return null as never

  const [contextEntries, contacts, meetings, engagements, quotes, invoices] = await Promise.all([
    listContext(params.db, params.entityId),
    listContacts(params.db, params.orgId, params.entityId),
    listMeetings(params.db, params.orgId, params.entityId),
    listEngagements(params.db, params.orgId, params.entityId),
    listQuotes(params.db, params.orgId, params.entityId),
    listInvoices(params.db, params.orgId, { entityId: params.entityId }),
  ])

  const mostRecentDraftableMeeting = findDraftableMeeting(meetings, quotes)
  const hasOutreach = contextEntries.some((entry) => entry.type === 'outreach_draft')

  const timelineEntries = [...contextEntries].reverse()
  const typeFilter = params.url.searchParams.get('type') ?? ''
  const filteredEntries = typeFilter
    ? timelineEntries.filter((entry) => entry.type === typeFilter)
    : timelineEntries

  const typeCounts: Record<string, number> = {}
  for (const entry of contextEntries) {
    typeCounts[entry.type] = (typeCounts[entry.type] ?? 0) + 1
  }

  let currentLostReason: { code: string; detail: string | null } | null = null
  if (entity.stage === 'lost') {
    const lostEntries = [...contextEntries]
      .reverse()
      .filter((entry) => entry.type === 'stage_change')
    for (const entry of lostEntries) {
      if (!entry.metadata) continue
      try {
        const meta = JSON.parse(entry.metadata) as Record<string, unknown>
        if (meta.to === 'lost' && typeof meta.lost_reason === 'string') {
          currentLostReason = {
            code: meta.lost_reason,
            detail: typeof meta.lost_detail === 'string' ? meta.lost_detail : null,
          }
          break
        }
      } catch {
        continue
      }
    }
  }

  const promoted = params.url.searchParams.get('promoted')
  const noteAdded = params.url.searchParams.get('note_added')
  const replyLogged = params.url.searchParams.get('reply_logged')
  const stageUpdated = params.url.searchParams.get('stage_updated')
  const dossierGenerated = params.url.searchParams.get('dossier')
  const contactAdded = params.url.searchParams.get('contact_added')
  const contactUpdated = params.url.searchParams.get('contact_updated')
  const contactDeleted = params.url.searchParams.get('contact_deleted')
  const error = params.url.searchParams.get('error')

  const hasOpenQuote = await hasOpenQuoteForEntity(params.db, params.orgId, params.entityId)
  const showReEnrichButton = RE_ENRICH_STAGES.includes(entity.stage)
  const showNewQuoteButton =
    ['signal', 'prospect', 'meetings', 'proposing'].includes(entity.stage) &&
    !hasOpenQuote &&
    meetings.length > 0
  const supersedeCandidates = showNewQuoteButton
    ? quotes.filter((quote) => quote.status === 'declined' || quote.status === 'expired')
    : []

  const dossierBrief = contextEntries.filter((entry) => entry.source === 'intelligence_brief').pop()
  const reviewSynthEntry = contextEntries
    .filter((entry) => entry.source === 'review_synthesis')
    .pop()
  const deepWebsiteEntry = contextEntries.filter((entry) => entry.source === 'deep_website').pop()
  const competitorEntry = contextEntries.filter((entry) => entry.source === 'competitors').pop()
  const outreachEntry = contextEntries.filter((entry) => entry.type === 'outreach_draft').pop()
  const outreachMeta = parseMetadata(outreachEntry?.metadata ?? null)
  const outreachFromDossier = outreachMeta?.trigger === 'dossier'
  const hasDossier = !!dossierBrief

  const outreachContact =
    contacts.find((contact) => contact.email && contact.email.trim().length > 0) ?? null
  const outreachMailto =
    outreachEntry && outreachContact?.email
      ? `mailto:${encodeURIComponent(outreachContact.email)}` +
        `?subject=${encodeURIComponent(`Reaching out - ${entity.name}`)}` +
        `&body=${encodeURIComponent(outreachEntry.content)}`
      : null

  const lastEnrichmentAt =
    contextEntries
      .filter((entry) => entry.type === 'enrichment')
      .map((entry) => entry.created_at)
      .sort()
      .pop() ?? null

  const latestSentQuote = quotes
    .filter((quote) => quote.sent_at)
    .sort((a, b) => (b.sent_at ?? '').localeCompare(a.sent_at ?? ''))[0]
  const latestSentQuoteAt = latestSentQuote?.sent_at ?? null

  const reviewMeta = parseMetadata(reviewSynthEntry?.metadata ?? null) as {
    unified_rating?: number | null
    total_reviews_across_platforms?: number
    sentiment_trend?: string
    top_themes?: string[]
    operational_problems?: Array<{ problem: string; confidence: string; evidence: string }>
  } | null
  const websiteMeta = parseMetadata(deepWebsiteEntry?.metadata ?? null) as {
    digital_maturity?: { score: number; reasoning: string }
  } | null
  const competitorMeta = parseMetadata(competitorEntry?.metadata ?? null) as {
    entity_rank_by_rating?: number | null
    total_competitors?: number
  } | null

  const transitions = ENTITY_DETAIL_TRANSITIONS[entity.stage].map((transition) => ({
    ...transition,
    action: transition.action ?? `/api/admin/entities/${entity.id}/stage`,
  }))

  return {
    entity,
    contextEntries,
    contacts,
    meetings,
    engagements,
    quotes,
    invoices,
    mostRecentDraftableMeeting,
    hasOutreach,
    filteredEntries,
    typeFilter,
    typeCounts,
    currentLostReason,
    promoted,
    noteAdded,
    replyLogged,
    stageUpdated,
    dossierGenerated,
    contactAdded,
    contactUpdated,
    contactDeleted,
    error,
    showReEnrichButton,
    showNewQuoteButton,
    supersedeCandidates,
    transitions,
    dossierBrief,
    outreachEntry,
    outreachContact,
    outreachMailto,
    outreachFromDossier,
    hasDossier,
    lastEnrichmentAt,
    latestSentQuoteAt,
    reviewMeta,
    websiteMeta,
    competitorMeta,
  }
}
