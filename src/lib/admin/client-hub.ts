/**
 * Client hub composition helpers (ADR 0046).
 *
 * The `service` spine table does not exist yet — the hub composes the
 * client's services view-side from the tables that do: engagements
 * (consulting) and customer_configs (operator), with values drawn from
 * quotes and a billing rollup from invoices.
 *
 * Honesty note: the Operator's recurring price has no schema home yet
 * (pricing is doctrinally deferred), so this module does NOT compute an
 * MRR figure. The at-a-glance rollup surfaces only what invoices prove —
 * billed / paid / outstanding. MRR returns to the hub when a real
 * recurring-price source exists.
 */

import type { Invoice } from '../db/invoices'
import type { Engagement, EngagementStatus } from '../db/engagements'
import { ENGAGEMENT_STATUSES } from '../db/engagements'
import type { Quote } from '../db/quotes'
import type { EntityStage } from '../db/entities'
import { formatDollarsHalfUp } from '../format/money'

/**
 * Admin-facing stage badge for the Clients surface: an honest label plus
 * whether the record has crossed the signature line (drives badge tone).
 *
 * The eight canonical stages (`src/lib/db/entities.ts`) are unchanged; this
 * only maps them to the labels Clients shows. Two labels differ from the raw
 * enum on purpose: `meetings` reads "Assessment" (what the stage actually is),
 * and `engaged` reads "Client" (a signed quote at `proposing → engaged` is the
 * moment a record becomes one). `signed` is true from `engaged` onward.
 */
export interface ClientStageBadge {
  label: string
  signed: boolean
}

export function clientStageBadge(stage: EntityStage): ClientStageBadge {
  switch (stage) {
    case 'prospect':
      return { label: 'Prospect', signed: false }
    case 'meetings':
      return { label: 'Assessment', signed: false }
    case 'proposing':
      return { label: 'Proposing', signed: false }
    case 'engaged':
      return { label: 'Client', signed: true }
    case 'delivered':
      return { label: 'Delivered', signed: true }
    case 'ongoing':
      return { label: 'Ongoing', signed: true }
    case 'lost':
      return { label: 'Lost', signed: false }
    case 'signal':
    default:
      return { label: 'Signal', signed: false }
  }
}

export interface BillingRollup {
  /** Sum of all non-draft, non-void invoice amounts. */
  invoiced: number
  /** Sum of paid invoice amounts. */
  paid: number
  /** Sum of sent + overdue invoice amounts (money still owed). */
  outstanding: number
  /** True when any invoice is overdue. */
  hasOverdue: boolean
}

/** Statuses that count toward "invoiced". Drafts and voids are excluded. */
const COUNTED_INVOICE_STATUSES = new Set(['sent', 'paid', 'overdue'])

export function computeBillingRollup(invoices: Invoice[]): BillingRollup {
  let invoiced = 0
  let paid = 0
  let outstanding = 0
  let hasOverdue = false
  for (const inv of invoices) {
    if (!COUNTED_INVOICE_STATUSES.has(inv.status)) continue
    invoiced += inv.amount
    if (inv.status === 'paid') paid += inv.amount
    if (inv.status === 'sent' || inv.status === 'overdue') outstanding += inv.amount
    if (inv.status === 'overdue') hasOverdue = true
  }
  return { invoiced, paid, outstanding, hasOverdue }
}

export type ServiceTone = 'good' | 'attention' | 'alert' | 'muted'

/** A consulting service row, composed from an engagement + its quote. */
export interface ConsultingServiceRow {
  id: string
  title: string
  /** Formatted quote total, or null when the engagement has no priced quote. */
  value: string | null
  statusLabel: string
  tone: ServiceTone
  /** ISO date of the most relevant milestone, or null. */
  keyDateIso: string | null
  /** Human label for the key date (e.g. "Safety-net ends"). */
  keyDateKind: string | null
}

const ENGAGEMENT_TONE: Record<EngagementStatus, ServiceTone> = {
  scheduled: 'muted',
  active: 'good',
  handoff: 'attention',
  safety_net: 'attention',
  completed: 'muted',
  cancelled: 'muted',
}

export function engagementToServiceRow(eng: Engagement, quotes: Quote[]): ConsultingServiceRow {
  const quote = eng.quote_id ? (quotes.find((q) => q.id === eng.quote_id) ?? null) : null
  const status = eng.status as EngagementStatus
  const statusLabel = ENGAGEMENT_STATUSES.find((s) => s.value === status)?.label ?? eng.status

  let keyDateIso: string | null = null
  let keyDateKind: string | null = null
  if (status === 'safety_net' && eng.safety_net_end) {
    keyDateIso = eng.safety_net_end
    keyDateKind = 'Safety-net ends'
  } else if (eng.handoff_date) {
    keyDateIso = eng.handoff_date
    keyDateKind = 'Handoff'
  } else if (eng.estimated_end) {
    keyDateIso = eng.estimated_end
    keyDateKind = 'Est. completion'
  }

  return {
    id: eng.id,
    title: eng.scope_summary?.trim() || 'Consulting engagement',
    value: quote ? formatDollarsHalfUp(quote.total_price) : null,
    statusLabel,
    tone: ENGAGEMENT_TONE[status] ?? 'muted',
    keyDateIso,
    keyDateKind,
  }
}

/** Tailwind color class for a service status dot, by tone. */
export function serviceToneDotClass(tone: ServiceTone): string {
  switch (tone) {
    case 'good':
      return 'bg-[color:var(--ss-color-complete)]'
    case 'attention':
      return 'bg-[color:var(--ss-color-primary)]'
    case 'alert':
      return 'bg-[color:var(--ss-color-error)]'
    default:
      return 'bg-[color:var(--ss-color-text-muted)]'
  }
}
