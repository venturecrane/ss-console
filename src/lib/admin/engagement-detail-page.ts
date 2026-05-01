import { getEntity } from '../db/entities'
import { getEngagement, VALID_TRANSITIONS } from '../db/engagements'
import type { EngagementStatus } from '../db/engagements'
import { getQuote } from '../db/quotes'
import { listMilestones, VALID_TRANSITIONS as MILESTONE_TRANSITIONS } from '../db/milestones'
import type { MilestoneStatus } from '../db/milestones'
import { listInvoices } from '../db/invoices'
import { listContext } from '../db/context'
import { listParkingLot } from '../db/parking-lot'
import { listSignalsForEntity } from '../db/signal-attribution'
import { listContacts } from '../db/contacts'
import { listEngagementContacts } from '../db/engagement-contacts'
import { listDocuments } from '../storage/r2'

type Database = Parameters<typeof getEntity>[0]
type StorageBucket = Parameters<typeof listDocuments>[0]

export function contextTypeEyebrowClass(): string {
  return 'text-label uppercase font-mono text-[color:var(--ss-color-text-secondary)] tracking-[var(--ss-text-letter-spacing-label)]'
}

export function formatDate(date: string | null): string {
  if (!date) return '--'
  return new Date(date).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function truncateSignal(content: string, max = 80): string {
  const oneLine = content.replace(/\s+/g, ' ').trim()
  return oneLine.length <= max ? oneLine : `${oneLine.slice(0, max - 1)}…`
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
  return `[${signal.source_pipeline}] ${date} - ${truncateSignal(signal.content)}`
}

export async function loadEngagementDetailPage(params: {
  db: Database
  storage: StorageBucket
  orgId: string
  engagementId: string
  url: URL
}) {
  const engagement = await getEngagement(params.db, params.orgId, params.engagementId)
  if (!engagement) return null

  const entity = await getEntity(params.db, params.orgId, engagement.entity_id)
  const quote = engagement.quote_id
    ? await getQuote(params.db, params.orgId, engagement.quote_id)
    : null
  const milestones = await listMilestones(params.db, params.orgId, params.engagementId)
  const invoices = await listInvoices(params.db, params.orgId, {
    engagementId: params.engagementId,
  })
  const contextEntries = await listContext(params.db, engagement.entity_id, {
    engagement_id: params.engagementId,
  })
  const parkingLot = await listParkingLot(params.db, params.orgId, params.engagementId)
  const entitySignals = await listSignalsForEntity(params.db, params.orgId, engagement.entity_id)
  const entityContacts = await listContacts(params.db, params.orgId, engagement.entity_id)
  const engagementContacts = await listEngagementContacts(
    params.db,
    params.orgId,
    params.engagementId
  )
  const documents = await listDocuments(
    params.storage,
    `${params.orgId}/engagements/${params.engagementId}/docs/`
  )

  const status = engagement.status as EngagementStatus
  const nextStatuses = VALID_TRANSITIONS[status] ?? []
  const depositInvoice = invoices.find((invoice) => invoice.type === 'deposit')

  return {
    engagement,
    entity,
    quote,
    milestones,
    invoices,
    contextEntries,
    parkingLot,
    entitySignals,
    entityContacts,
    engagementContacts,
    documents,
    status,
    nextStatuses,
    depositInvoice,
    milestoneTransitions: MILESTONE_TRANSITIONS,
    saved: params.url.searchParams.get('saved'),
    error: params.url.searchParams.get('error'),
    milestoneAdded: params.url.searchParams.get('milestone_added'),
    milestoneDeleted: params.url.searchParams.get('milestone_deleted'),
    parkingLotAdded: params.url.searchParams.get('parking_lot_added'),
    parkingLotDispositioned: params.url.searchParams.get('parking_lot_dispositioned'),
    parkingLotDeleted: params.url.searchParams.get('parking_lot_deleted'),
    engagementContactAdded: params.url.searchParams.get('engagement_contact_added'),
    engagementContactRemoved: params.url.searchParams.get('engagement_contact_removed'),
    engagementContactPrimarySet: params.url.searchParams.get('engagement_contact_primary_set'),
  }
}

export type { MilestoneStatus }
