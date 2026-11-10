/**
 * Services list composition (ADR 0046).
 *
 * The global, cross-client delivery view. The `service` spine table does
 * not exist yet, so this composes a unified list view-side from the two
 * delivery sources that do: consulting engagements and operators
 * (customer_configs + runtime summary). Risk-sorted so what needs
 * attention floats to the top.
 *
 * Pure and deterministic — `now` is injected, never read from the clock,
 * so the risk ranking is unit-testable.
 */

import type { Engagement, EngagementStatus } from '../db/engagements'
import { ENGAGEMENT_STATUSES } from '../db/engagements'
import type { Quote } from '../db/quotes'
import type { ServiceTone } from './client-hub'
import { formatDollarsHalfUp } from '../format/money'

export type ServiceKind = 'consulting' | 'operator'
export type RiskTone = 'alert' | 'warn' | 'muted'

export interface ServiceListRow {
  kind: ServiceKind
  clientId: string
  clientName: string
  title: string
  statusLabel: string
  tone: ServiceTone
  /** Formatted value, or null when no real figure exists. */
  value: string | null
  risk: string
  riskTone: RiskTone
  /** Lower = more urgent; the sort key. */
  riskRank: number
  /** Where the row drills to. */
  href: string
}

/** Consulting statuses that are still "in motion". */
const IN_FLIGHT: EngagementStatus[] = ['scheduled', 'active', 'handoff', 'safety_net']

const ENGAGEMENT_TONE: Record<EngagementStatus, ServiceTone> = {
  scheduled: 'muted',
  active: 'good',
  handoff: 'attention',
  safety_net: 'attention',
  completed: 'muted',
  cancelled: 'muted',
}

const DAY_MS = 24 * 60 * 60 * 1000

export interface OperatorInput {
  entityId: string
  clientName: string
  configError: string | null
  /** From the runtime summary; null when no runtime row has been pushed. */
  openAlerts: number | null
  hasRuntime: boolean
  /**
   * Monthly price from the spine (`services.recurring_price`), joined by
   * entity_id. Null when no operator service exists yet or its price is
   * unauthored — rendered as "unpriced", never fabricated. (ADR 0046.)
   */
  recurringPrice: number | null
}

export interface BuildServiceListInput {
  engagements: Engagement[]
  entityName: (entityId: string) => string
  quotesById: Map<string, Quote>
  operators: OperatorInput[]
  now: Date
}

interface RiskInfo {
  risk: string
  riskTone: RiskTone
  riskRank: number
}

function consultingRisk(eng: Engagement, status: EngagementStatus, now: Date): RiskInfo {
  if (status === 'handoff' && eng.handoff_date) {
    const overdueDays = Math.floor((now.getTime() - new Date(eng.handoff_date).getTime()) / DAY_MS)
    return overdueDays > 0
      ? { risk: `Handoff overdue ${overdueDays}d`, riskTone: 'alert', riskRank: 1 }
      : { risk: 'In handoff', riskTone: 'warn', riskRank: 3 }
  }
  if (status === 'safety_net' && eng.safety_net_end) {
    const daysLeft = Math.ceil((new Date(eng.safety_net_end).getTime() - now.getTime()) / DAY_MS)
    const risk =
      daysLeft >= 0 ? `Safety-net ends in ${daysLeft}d` : `Safety-net ended ${-daysLeft}d ago`
    return daysLeft <= 7
      ? { risk, riskTone: 'warn', riskRank: 2 }
      : { risk, riskTone: 'muted', riskRank: 4 }
  }
  if (status === 'scheduled') {
    return {
      risk: eng.start_date ? 'Kickoff scheduled' : 'Scheduled',
      riskTone: 'muted',
      riskRank: 5,
    }
  }
  return { risk: 'Active', riskTone: 'muted', riskRank: 4 }
}

function consultingRow(eng: Engagement, quotesById: Map<string, Quote>, now: Date): ServiceListRow {
  const status = eng.status as EngagementStatus
  const statusLabel = ENGAGEMENT_STATUSES.find((s) => s.value === status)?.label ?? eng.status
  const quote = eng.quote_id ? (quotesById.get(eng.quote_id) ?? null) : null
  return {
    kind: 'consulting',
    clientId: eng.entity_id,
    clientName: '',
    title: eng.scope_summary?.trim() || 'Consulting engagement',
    statusLabel,
    tone: ENGAGEMENT_TONE[status] ?? 'muted',
    value: quote ? formatDollarsHalfUp(quote.total_price) : null,
    ...consultingRisk(eng, status, now),
    href: `/admin/clients/${eng.entity_id}`,
  }
}

interface OperatorStatus {
  statusLabel: string
  tone: ServiceTone
  risk: string
  riskTone: RiskTone
  riskRank: number
}

function operatorStatus(op: OperatorInput): OperatorStatus {
  if (!op.hasRuntime) {
    return {
      statusLabel: 'Provisioning',
      tone: 'muted',
      risk: 'No runtime yet',
      riskTone: 'muted',
      riskRank: 4,
    }
  }
  if ((op.openAlerts ?? 0) > 0) {
    const n = op.openAlerts ?? 0
    return {
      statusLabel: 'Alerting',
      tone: 'alert',
      risk: `${n} alert${n === 1 ? '' : 's'}`,
      riskTone: 'alert',
      riskRank: 0,
    }
  }
  if (op.configError) {
    return {
      statusLabel: 'Config issue',
      tone: 'attention',
      risk: 'Config needs attention',
      riskTone: 'warn',
      riskRank: 2,
    }
  }
  return { statusLabel: 'Healthy', tone: 'good', risk: 'Healthy', riskTone: 'muted', riskRank: 6 }
}

function operatorRow(op: OperatorInput): ServiceListRow {
  const s = operatorStatus(op)
  return {
    kind: 'operator',
    clientId: op.entityId,
    clientName: op.clientName,
    title: 'Operator',
    statusLabel: s.statusLabel,
    tone: s.tone,
    // Monthly price from the spine; null renders as "—" (unpriced), never fabricated.
    value: op.recurringPrice == null ? null : `${formatDollarsHalfUp(op.recurringPrice)}/mo`,
    risk: s.risk,
    riskTone: s.riskTone,
    riskRank: s.riskRank,
    href: '/admin/operator',
  }
}

export function buildServiceList(input: BuildServiceListInput): ServiceListRow[] {
  const rows: ServiceListRow[] = []

  for (const eng of input.engagements) {
    if (!IN_FLIGHT.includes(eng.status as EngagementStatus)) continue
    const row = consultingRow(eng, input.quotesById, input.now)
    row.clientName = input.entityName(eng.entity_id)
    rows.push(row)
  }

  for (const op of input.operators) {
    rows.push(operatorRow(op))
  }

  rows.sort((a, b) => a.riskRank - b.riskRank || a.clientName.localeCompare(b.clientName))
  return rows
}

export interface ServiceListStats {
  inMotion: number
  consulting: number
  operator: number
  atRisk: number
}

export function serviceListStats(rows: ServiceListRow[]): ServiceListStats {
  return {
    inMotion: rows.length,
    consulting: rows.filter((r) => r.kind === 'consulting').length,
    operator: rows.filter((r) => r.kind === 'operator').length,
    atRisk: rows.filter((r) => r.riskTone === 'alert' || r.riskTone === 'warn').length,
  }
}
