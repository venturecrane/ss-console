import { hasOpenQuoteForEntity, listQuotes } from '../db/quotes'
import { parseJsonRecord } from '../api/helpers'
import { getEntity } from '../db/entities'
import type { EntityStage } from '../db/entities'
import { listContext } from '../db/context'
import type { ContextEntry } from '../db/context'
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

export const ENTITY_DETAIL_TRANSITIONS: Record<EntityStage, EntityDetailTransition[]> = {
  signal: [
    { label: 'Promote', stage: 'prospect', variant: 'primary' },
    { label: 'Dismiss', stage: 'lost', variant: 'destructive' },
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

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

export function parseMetadata(json: string | null): Record<string, unknown> | null {
  return parseJsonRecord(json)
}

/**
 * Collapse the raw context log into a display timeline. Preserved from the
 * former decision-evidence module; the machine source-dedup branches are inert
 * now that the enrichment pipeline is gone, but harmless for any legacy rows.
 */
function composeDeduplicatedTimeline(contextEntries: ContextEntry[]): ContextEntry[] {
  const out: ContextEntry[] = []
  const latestBySource = new Set<string>()

  for (const entry of [...contextEntries].reverse()) {
    if (entry.type === 'outreach_draft') continue
    if (entry.source === 'intelligence_brief') continue
    if (entry.source === 'review_synthesis' || entry.source === 'review_analysis') {
      if (latestBySource.has(entry.source)) continue
      latestBySource.add(entry.source)
    }
    out.push(entry)
  }

  return out
}

type Contact = Awaited<ReturnType<typeof listContacts>>[number]
type Quote = Awaited<ReturnType<typeof listQuotes>>[number]

export interface EntityDetailPageResult {
  entity: EntityRecord
  contextEntries: ContextEntry[]
  contacts: Contact[]
  meetings: Awaited<ReturnType<typeof listMeetings>>
  engagements: Awaited<ReturnType<typeof listEngagements>>
  quotes: Quote[]
  invoices: Awaited<ReturnType<typeof listInvoices>>
  mostRecentDraftableMeeting: ReturnType<typeof findDraftableMeeting>
  filteredEntries: ContextEntry[]
  deduplicatedTimeline: ContextEntry[]
  typeFilter: string
  typeCounts: Record<string, number>
  currentLostReason: { code: string; detail: string | null } | null
  promoted: string | null
  noteAdded: string | null
  replyLogged: string | null
  stageUpdated: string | null
  contactAdded: string | null
  contactUpdated: string | null
  contactDeleted: string | null
  error: string | null
  showNewQuoteButton: boolean
  supersedeCandidates: Quote[]
  transitions: EntityDetailTransition[]
  latestSentQuoteAt: string | null
}

function extractUrlParams(url: URL): {
  typeFilter: string
  promoted: string | null
  noteAdded: string | null
  replyLogged: string | null
  stageUpdated: string | null
  contactAdded: string | null
  contactUpdated: string | null
  contactDeleted: string | null
  error: string | null
} {
  const sp = url.searchParams
  return {
    typeFilter: sp.get('type') ?? '',
    promoted: sp.get('promoted'),
    noteAdded: sp.get('note_added'),
    replyLogged: sp.get('reply_logged'),
    stageUpdated: sp.get('stage_updated'),
    contactAdded: sp.get('contact_added'),
    contactUpdated: sp.get('contact_updated'),
    contactDeleted: sp.get('contact_deleted'),
    error: sp.get('error'),
  }
}

function resolveLostReason(
  entity: EntityRecord,
  contextEntries: ContextEntry[]
): { code: string; detail: string | null } | null {
  if (entity.stage !== 'lost') return null
  for (const entry of [...contextEntries].reverse().filter((e) => e.type === 'stage_change')) {
    const meta = parseJsonRecord(entry.metadata)
    if (meta?.to === 'lost' && typeof meta.lost_reason === 'string') {
      return {
        code: meta.lost_reason,
        detail: typeof meta.lost_detail === 'string' ? meta.lost_detail : null,
      }
    }
  }
  return null
}

function resolveQuoteFlags(
  entity: EntityRecord,
  quotes: Quote[],
  meetings: Awaited<ReturnType<typeof listMeetings>>,
  hasOpenQuote: boolean
): { showNewQuoteButton: boolean; supersedeCandidates: Quote[]; latestSentQuoteAt: string | null } {
  const showNewQuoteButton =
    ['signal', 'prospect', 'meetings', 'proposing'].includes(entity.stage) &&
    !hasOpenQuote &&
    meetings.length > 0
  const supersedeCandidates = showNewQuoteButton
    ? quotes.filter((q) => q.status === 'declined' || q.status === 'expired')
    : []
  const latestSentQuote = quotes
    .filter((q) => q.sent_at)
    .sort((a, b) => (b.sent_at ?? '').localeCompare(a.sent_at ?? ''))[0]
  return {
    showNewQuoteButton,
    supersedeCandidates,
    latestSentQuoteAt: latestSentQuote?.sent_at ?? null,
  }
}

function resolveContextDerivedFields(
  contextEntries: ContextEntry[],
  typeFilter: string
): {
  filteredEntries: ContextEntry[]
  deduplicatedTimeline: ContextEntry[]
  typeCounts: Record<string, number>
} {
  const deduplicatedTimeline = composeDeduplicatedTimeline(contextEntries)
  const filteredEntries = typeFilter
    ? deduplicatedTimeline.filter((e) => e.type === typeFilter)
    : deduplicatedTimeline
  const typeCounts: Record<string, number> = {}
  for (const entry of contextEntries) typeCounts[entry.type] = (typeCounts[entry.type] ?? 0) + 1
  return { filteredEntries, deduplicatedTimeline, typeCounts }
}

async function loadEntityDetailDependencies(
  params: Parameters<typeof loadEntityDetailPage>[0]
): Promise<{
  contextEntries: ContextEntry[]
  contacts: Contact[]
  meetings: Awaited<ReturnType<typeof listMeetings>>
  engagements: Awaited<ReturnType<typeof listEngagements>>
  quotes: Quote[]
  invoices: Awaited<ReturnType<typeof listInvoices>>
}> {
  const [contextEntries, contacts, meetings, engagements, quotes, invoices] = await Promise.all([
    listContext(params.db, params.entityId),
    listContacts(params.db, params.orgId, params.entityId),
    listMeetings(params.db, params.orgId, params.entityId),
    listEngagements(params.db, params.orgId, params.entityId),
    listQuotes(params.db, params.orgId, params.entityId),
    listInvoices(params.db, params.orgId, { entityId: params.entityId }),
  ])

  return { contextEntries, contacts, meetings, engagements, quotes, invoices }
}

export async function loadEntityDetailPage(params: {
  db: Database
  orgId: string
  entityId: string
  url: URL
}): Promise<EntityDetailPageResult> {
  const entity = await getEntity(params.db, params.orgId, params.entityId)
  if (!entity) return null as never
  const data = await loadEntityDetailDependencies(params)
  const urlParams = extractUrlParams(params.url)
  const ctx = resolveContextDerivedFields(data.contextEntries, urlParams.typeFilter)
  const currentLostReason = resolveLostReason(entity, data.contextEntries)
  const transitions = ENTITY_DETAIL_TRANSITIONS[entity.stage].map((t) => ({
    ...t,
    action: t.action ?? `/api/admin/entities/${entity.id}/stage`,
  }))
  const hasOpenQuote = await hasOpenQuoteForEntity(params.db, params.orgId, params.entityId)
  const quoteFlags = resolveQuoteFlags(entity, data.quotes, data.meetings, hasOpenQuote)
  return {
    entity,
    contextEntries: data.contextEntries,
    contacts: data.contacts,
    meetings: data.meetings,
    engagements: data.engagements,
    quotes: data.quotes,
    invoices: data.invoices,
    mostRecentDraftableMeeting: findDraftableMeeting(data.meetings, data.quotes),
    filteredEntries: ctx.filteredEntries,
    deduplicatedTimeline: ctx.deduplicatedTimeline,
    typeCounts: ctx.typeCounts,
    currentLostReason,
    ...urlParams,
    showNewQuoteButton: quoteFlags.showNewQuoteButton,
    supersedeCandidates: quoteFlags.supersedeCandidates,
    transitions,
    latestSentQuoteAt: quoteFlags.latestSentQuoteAt,
  }
}
