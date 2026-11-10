/**
 * EntityRowView — pre-computed view model for a single entity row on the
 * admin entity list page.
 *
 * All per-row computation happens here (in the page frontmatter loop) rather
 * than inside the JSX map arrow, keeping the arrow under the 75-line ceiling.
 */
import type { Entity } from '../db/entities'
import { ENTITY_STAGES } from '../db/entities'
import type { Quote } from '../db/quotes'
import type { Meeting } from '../db/meetings'
import type { Engagement } from '../db/engagements'
import type { InvoiceRollup } from '../db/invoices'
import type { ContextEntry } from '../db/context'
import type { Contact } from '../db/contacts'
import type { LostReasonCode } from '../db/lost-reasons'
import { lostReasonLabel, lostReasonChipClass } from '../db/lost-reasons'
import { findDraftableMeeting } from '../entities/draftable-meeting'
import {
  findNextScheduledMeeting,
  getMeetingSubstate,
  MEETING_SUBSTATE_LABEL,
  type MeetingSubstate,
} from '../entities/meeting-substate'
import { relativeTime } from './relative-time'

export interface EntityRowView {
  entity: Entity
  isSignal: boolean
  isProspect: boolean
  isMeetings: boolean
  isEngaged: boolean
  isLost: boolean
  isDelivered: boolean
  website: { href: string; label: string } | null
  rowSubstate: MeetingSubstate | null
  rowSubstateLabel: string
  rowNextMeeting: Meeting | null
  nextMeetingLabel: string
  rowEngagement: Engagement | null
  rowInvoiceRollup: InvoiceRollup | null
  rowLostReason: { code: LostReasonCode; detail: string | null } | null
  rowLostReasonLabel: string | null
  rowLostReasonChipClass: string
  stabilizationDays: number | null
  activeQuote: Quote | null
  quoteAge: string | null
  outreachMailto: string | null
  contactEmail: string | null
  hasOutreachDraft: boolean
  draftableMeetingId: string | null
  showProspectActions: boolean
  showMeetingsActions: boolean
  isOverdue: boolean
  nextActionDateLabel: string
  stageLabel: string
  stageChangedAtLabel: string
  createdAtLabel: string
}

function fmt(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  })
}

function resolveWebsite(raw: string | null): { href: string; label: string } | null {
  if (!raw) return null
  let url: URL
  try {
    url = new URL(raw.includes('://') ? raw : `https://${raw}`)
  } catch {
    return null
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
  const label = (url.hostname + url.pathname).replace(/^www\./, '').replace(/\/$/, '')
  return { href: url.toString(), label }
}

function buildOutreachMailto(
  entityName: string,
  contactEmail: string | null | undefined,
  draftContent: string | null | undefined
): string | null {
  if (!contactEmail || !draftContent) return null
  return (
    `mailto:${encodeURIComponent(contactEmail)}` +
    `?subject=${encodeURIComponent(`Reaching out — ${entityName}`)}` +
    `&body=${encodeURIComponent(draftContent)}`
  )
}

export interface BuildArgs {
  entity: Entity
  filterStage: string
  outreachDraftByEntityId: Map<string, ContextEntry>
  contactEmailByEntityId: Map<string, Contact>
  meetingsByEntityId: Map<string, Meeting[]>
  quotesByEntityId: Map<string, Quote[]>
  activeEngagementByEntityId: Map<string, Engagement>
  invoiceRollupByEntityId: Map<string, InvoiceRollup>
  lostReasonByEntityId: Map<string, { code: LostReasonCode; detail: string | null }>
  activeQuoteByEntityId: Map<string, Quote>
}

function resolveProspectFields(
  args: BuildArgs,
  entityId: string,
  entityName: string
): Pick<
  EntityRowView,
  'outreachMailto' | 'contactEmail' | 'hasOutreachDraft' | 'showProspectActions'
> {
  const draft = args.outreachDraftByEntityId.get(entityId)
  const email = args.contactEmailByEntityId.get(entityId)
  const contactEmail = email ? email.email : null
  const outreachDraft = draft ? draft.content : null
  const outreachMailto = buildOutreachMailto(entityName, contactEmail, outreachDraft)
  return {
    outreachMailto,
    contactEmail,
    hasOutreachDraft: !!draft,
    showProspectActions: !!(outreachMailto || draft),
  }
}

function resolveMeetingsFields(
  args: BuildArgs,
  entityId: string
): Pick<
  EntityRowView,
  | 'rowSubstate'
  | 'rowSubstateLabel'
  | 'rowNextMeeting'
  | 'nextMeetingLabel'
  | 'draftableMeetingId'
  | 'showMeetingsActions'
> {
  const rowMeetings = args.meetingsByEntityId.get(entityId)
  const rowQuotes = args.quotesByEntityId.get(entityId)
  const meetings = rowMeetings ? rowMeetings : []
  const quotes = rowQuotes ? rowQuotes : []
  const rowSubstate = getMeetingSubstate(meetings, quotes)
  const rowNextMeeting = findNextScheduledMeeting(meetings)
  const draftable = findDraftableMeeting(meetings, quotes)
  const substateLabel = rowSubstate ? (MEETING_SUBSTATE_LABEL[rowSubstate] ?? rowSubstate) : ''
  const nextScheduled = rowNextMeeting ? rowNextMeeting.scheduled_at : null
  const draftableMeetingId = draftable ? draftable.id : null
  return {
    rowSubstate,
    rowSubstateLabel: substateLabel,
    rowNextMeeting,
    nextMeetingLabel: nextScheduled ? fmt(nextScheduled) : '',
    draftableMeetingId,
    showMeetingsActions: !!draftableMeetingId,
  }
}

function resolveProposingFields(
  args: BuildArgs,
  entityId: string
): Pick<EntityRowView, 'activeQuote' | 'quoteAge'> {
  const activeQuote = args.activeQuoteByEntityId.get(entityId)
  if (!activeQuote) return { activeQuote: null, quoteAge: null }
  const useSentAt = activeQuote.status !== 'draft'
  const timestamp = useSentAt
    ? (activeQuote.sent_at ?? activeQuote.updated_at)
    : activeQuote.updated_at
  return { activeQuote, quoteAge: relativeTime(timestamp) }
}

function resolveLostFields(
  args: BuildArgs,
  entityId: string
): Pick<EntityRowView, 'rowLostReason' | 'rowLostReasonLabel' | 'rowLostReasonChipClass'> {
  const reason = args.lostReasonByEntityId.get(entityId)
  if (!reason) return { rowLostReason: null, rowLostReasonLabel: null, rowLostReasonChipClass: '' }
  return {
    rowLostReason: reason,
    rowLostReasonLabel: lostReasonLabel(reason.code) ?? null,
    rowLostReasonChipClass: lostReasonChipClass(reason.code),
  }
}

function resolveEngagedFields(
  args: BuildArgs,
  entityId: string
): Pick<EntityRowView, 'rowEngagement' | 'rowInvoiceRollup'> {
  return {
    rowEngagement: args.activeEngagementByEntityId.get(entityId) ?? null,
    rowInvoiceRollup: args.invoiceRollupByEntityId.get(entityId) ?? null,
  }
}

interface RowInput {
  args: BuildArgs
  entity: Entity
  isSignal: boolean
  isProspect: boolean
  isMeetings: boolean
  isEngaged: boolean
  isLost: boolean
  isDelivered: boolean
}

export function buildEntityRowView(args: BuildArgs): EntityRowView {
  const e = args.entity
  return assembleRow({
    args,
    entity: e,
    isSignal: e.stage === 'signal',
    isProspect: e.stage === 'prospect',
    isMeetings: e.stage === 'meetings',
    isEngaged: e.stage === 'engaged',
    isLost: e.stage === 'lost',
    isDelivered: e.stage === 'delivered',
  })
}

function resolveStageFields(
  args: BuildArgs,
  e: Entity,
  flags: Omit<RowInput, 'args' | 'entity' | 'meta'>
) {
  return {
    prospectFields: flags.isProspect
      ? resolveProspectFields(args, e.id, e.name)
      : {
          outreachMailto: null,
          contactEmail: null,
          hasOutreachDraft: false,
          showProspectActions: false,
        },
    meetingsFields: flags.isMeetings
      ? resolveMeetingsFields(args, e.id)
      : {
          rowSubstate: null,
          rowSubstateLabel: '',
          rowNextMeeting: null,
          nextMeetingLabel: '',
          draftableMeetingId: null,
          showMeetingsActions: false,
        },
    engagedFields: flags.isEngaged
      ? resolveEngagedFields(args, e.id)
      : { rowEngagement: null, rowInvoiceRollup: null },
    lostFields: flags.isLost
      ? resolveLostFields(args, e.id)
      : { rowLostReason: null, rowLostReasonLabel: null, rowLostReasonChipClass: '' },
    proposingFields:
      args.filterStage === 'proposing'
        ? resolveProposingFields(args, e.id)
        : { activeQuote: null, quoteAge: null },
  }
}

function assembleRow(input: RowInput): EntityRowView {
  const {
    args,
    entity: e,
    isSignal,
    isProspect,
    isMeetings,
    isEngaged,
    isLost,
    isDelivered,
  } = input
  const flags = { isSignal, isProspect, isMeetings, isEngaged, isLost, isDelivered }

  const { prospectFields, meetingsFields, engagedFields, lostFields, proposingFields } =
    resolveStageFields(args, e, flags)

  const stageEntry = ENTITY_STAGES.find((s) => s.value === e.stage)

  return {
    entity: e,
    ...flags,
    website: resolveWebsite(e.website),
    ...meetingsFields,
    ...engagedFields,
    ...lostFields,
    stabilizationDays: computeStabilizationDays(isDelivered, e.stage_changed_at),
    ...proposingFields,
    ...prospectFields,
    isOverdue: e.next_action_at ? new Date(e.next_action_at).getTime() < Date.now() : false,
    nextActionDateLabel: e.next_action_at ? fmt(e.next_action_at) : '',
    stageLabel: stageEntry ? stageEntry.label : e.stage,
    stageChangedAtLabel: fmt(e.stage_changed_at),
    createdAtLabel: fmt(e.created_at),
  }
}

function computeStabilizationDays(isDelivered: boolean, stageChangedAt: string): number | null {
  if (!isDelivered) return null
  return Math.floor((Date.now() - new Date(stageChangedAt).getTime()) / (1000 * 60 * 60 * 24))
}
