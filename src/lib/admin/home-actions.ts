/**
 * Home "Needs you today" action queue (ADR 0046).
 *
 * Composes a single, severity-ranked worklist from real state across the
 * surfaces — overdue invoices, at-risk services, overdue follow-ups —
 * each item deep-linking to where it gets resolved. Pure and testable.
 */

import type { Invoice } from '../db/invoices'
import type { ServiceListRow } from './services-list'
import { formatDollarsHalfUp } from '../format/money'

export type ActionTone = 'alert' | 'warn' | 'muted'

export interface ActionItem {
  tone: ActionTone
  type: string
  text: string
  href: string
}

const TONE_RANK: Record<ActionTone, number> = { alert: 0, warn: 1, muted: 2 }

export interface BuildActionQueueInput {
  overdueInvoices: Invoice[]
  atRiskServices: ServiceListRow[]
  overdueFollowUpCount: number
  clientName: (entityId: string) => string
  /** Max items to surface. */
  limit?: number
}

export function buildActionQueue(input: BuildActionQueueInput): ActionItem[] {
  const items: ActionItem[] = []

  for (const inv of input.overdueInvoices) {
    items.push({
      tone: 'alert',
      type: 'Invoice',
      text: `${input.clientName(inv.entity_id)} — ${formatDollarsHalfUp(inv.amount)} overdue`,
      href: '/admin/billing',
    })
  }

  for (const svc of input.atRiskServices) {
    items.push({
      tone: svc.riskTone === 'alert' ? 'alert' : 'warn',
      type: svc.kind === 'operator' ? 'Operator' : 'Delivery',
      text: `${svc.clientName} — ${svc.risk}`,
      href: svc.href,
    })
  }

  if (input.overdueFollowUpCount > 0) {
    items.push({
      tone: 'warn',
      type: 'Follow-up',
      text: `${input.overdueFollowUpCount} follow-up${input.overdueFollowUpCount === 1 ? '' : 's'} overdue`,
      href: '/admin/follow-ups',
    })
  }

  items.sort((a, b) => TONE_RANK[a.tone] - TONE_RANK[b.tone])
  return items.slice(0, input.limit ?? 7)
}
