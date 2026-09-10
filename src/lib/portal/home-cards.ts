/**
 * Home dashboard cards (portal IA rebuild, Captain decision 3): one card
 * per owned offering, each composed from existing readers only. Every
 * status label derives from real state; a card with nothing true renders
 * nothing extra (no fabricated aliveness, no invented queues).
 */

import type { D1Database } from '@cloudflare/workers-types'
import type { PortalOfferings } from './offerings'
import { resolveEngagementLabel } from './status'
import { subscriptionStatusLabel, type SubscriptionStatus } from './operator/account-read'
import { resolveAlivenessSignal } from './operator/aliveness'
import { readDraftQueueDepth } from './operator/home'
import { resolveHostedAgentState } from './hosted-agent-state'
import { listInvoicesForEntity } from '../db/invoices'
import { getOperatorServiceForEntity } from '../db/services'
import { formatShortDate } from './formatters'
import {
  canStartOperatorSubscription,
  formatWholeDollars,
  operatorMonthlyCharge,
  operatorSubscriptionHref,
} from './billing'

export interface OfferingCard {
  /**
   * Stable card identity. Fixed literals for single-instance offerings; per-
   * instance (`operator:<slug>`) for operators, since a client can own several.
   */
  key: string
  label: string
  href: string
  statusLabel: string
  meta: string[]
  needsYou: { label: string; href: string } | null
}

function engagementCard(offerings: PortalOfferings): OfferingCard | null {
  const { activeEngagement, openProposal, pastEngagements, present } = offerings.engagement
  if (!present) return null

  const statusLabel = activeEngagement
    ? resolveEngagementLabel(activeEngagement.status)
    : openProposal
      ? 'Proposal awaiting your review'
      : 'Past work on record'

  const meta: string[] = []
  if (activeEngagement?.start_date) {
    meta.push(`Started ${formatShortDate(activeEngagement.start_date)}`)
  }
  if (!activeEngagement && pastEngagements.length > 0) {
    meta.push(
      `${pastEngagements.length} completed engagement${pastEngagements.length !== 1 ? 's' : ''}`
    )
  }

  return {
    key: 'engagement',
    label: 'Engagement',
    href: '/portal/engagement',
    statusLabel,
    meta,
    needsYou: openProposal
      ? {
          label: 'Proposal awaiting your signature',
          href: `/portal/engagement/proposals/${openProposal.id}`,
        }
      : null,
  }
}

/**
 * ONE "Operator" card for the product — never one card per instance (a client
 * with 50 operators must not get 50 cards). Labeled by the product, like the
 * Agent/Billing cards, and it opens the operator list.
 *
 *   0 operators  → no card
 *   1 operator   → product-labeled card with that operator's live status (parity
 *                  with the Agent card); the list page forwards straight to it
 *   many         → "N active" summary → the list, where the client picks one
 */
async function operatorSummaryCard(
  db: D1Database,
  orgId: string,
  entityId: string,
  offerings: PortalOfferings
): Promise<OfferingCard | null> {
  const ops = offerings.operators
  if (ops.length === 0) return null
  const href = '/portal/products/operator'

  if (ops.length > 1) {
    const activeCount = ops.filter((o) => o.status === 'active').length
    return {
      key: 'operator',
      label: 'Operators',
      href,
      statusLabel: `${activeCount} of ${ops.length} active`,
      meta: [],
      needsYou: null,
    }
  }

  const op = ops[0]
  const subStatus: SubscriptionStatus =
    op.status === 'provisioning' || op.status === 'active' || op.status === 'paused'
      ? op.status
      : 'unknown'
  const meta: string[] = []
  let needsYou: OfferingCard['needsYou'] = null

  // The start door (Captain, 2026-08-29: the retainer starts by the client's
  // own click). Until the client starts it, this is the one thing the
  // Operator needs from them, so it outranks the draft queue and the card
  // says so where they land (2026-09-09: the door lived only on Billing,
  // under the ledger, and the client did not find it).
  try {
    const service = await getOperatorServiceForEntity(db, orgId, entityId)
    const charge = operatorMonthlyCharge(service)
    if (charge && canStartOperatorSubscription(op.subscription, charge.priceCents)) {
      return {
        key: 'operator',
        label: 'Operator',
        href,
        statusLabel: 'Ready to start',
        // The amount charged each month on the authored rail: the price, plus
        // the card fee when the client pays by card (stated before payment).
        meta: [`${formatWholeDollars(charge.totalCents)} per month`],
        needsYou: { label: 'Start monthly subscription', href: operatorSubscriptionHref(op.slug) },
      }
    }
  } catch {
    // No price read, no door: the card falls through to the live status.
  }

  try {
    const signal = await resolveAlivenessSignal(db, op.subscription)
    if (signal?.lastActionAt) {
      meta.push(`Last action ${formatShortDate(signal.lastActionAt)}`)
    }
    const depth = await readDraftQueueDepth(db, op.slug)
    if (depth > 0) {
      needsYou = {
        label: `${depth} draft${depth !== 1 ? 's' : ''} waiting for review`,
        href,
      }
    }
  } catch {
    // Card falls back to the subscription status alone — honest absence.
  }

  return {
    key: 'operator',
    label: 'Operator',
    href,
    statusLabel: subscriptionStatusLabel(subStatus),
    meta,
    needsYou,
  }
}

async function hostedAgentCard(
  db: D1Database,
  offerings: PortalOfferings,
  userId: string
): Promise<OfferingCard | null> {
  const sub = offerings.hostedAgent
  if (!sub) return null

  const state = await resolveHostedAgentState(db, sub.entity_id, userId)
  const statusLabel =
    state.surfaceState === 'live'
      ? `${state.intake?.agent_name ?? 'Your agent'} is live`
      : state.surfaceState === 'paused'
        ? 'Paused'
        : 'Setup in progress'

  let needsYou: OfferingCard['needsYou'] = null
  if (state.needsIntake) {
    needsYou = {
      label: 'Complete the setup questionnaire',
      href: '/portal/products/hosted-agent/intake',
    }
  } else if (state.needsKey) {
    needsYou = {
      label: 'Add your Anthropic API key',
      href: '/portal/products/hosted-agent/api-key',
    }
  }

  return {
    key: 'hosted-agent',
    label: 'Agent',
    href: '/portal/products/hosted-agent',
    statusLabel,
    meta: [],
    needsYou,
  }
}

async function billingCard(
  db: D1Database,
  orgId: string,
  entityId: string,
  offerings: PortalOfferings
): Promise<OfferingCard | null> {
  // Same gate as the nav tab: no billing relationship, no billing card
  // (pre-go-live clients have no commercial plane to read).
  if (!offerings.hasBillingRelationship) return null

  let needsYou: OfferingCard['needsYou'] = null
  // "Up to date" is a claim about invoice history — it needs history to be
  // true of. A client who has never been invoiced sees the honest fact
  // instead (Captain finding, 2026-07-15: the label read as a placeholder).
  let statusLabel = offerings.hasInvoices ? 'Up to date' : 'No invoices yet'
  const meta: string[] = []

  try {
    const invoices = await listInvoicesForEntity(db, orgId, entityId)
    const pending = invoices.find((i) => i.status === 'sent' || i.status === 'overdue') ?? null
    if (pending) {
      statusLabel = pending.status === 'overdue' ? 'Invoice overdue' : 'Invoice due'
      if (pending.due_date) meta.push(`Due ${formatShortDate(pending.due_date)}`)
      needsYou = { label: 'Pay invoice', href: `/portal/billing/invoices/${pending.id}` }
    }
  } catch {
    // Honest fallback: the card still links to the ledger.
  }

  return {
    key: 'billing',
    label: 'Billing',
    href: '/portal/billing',
    statusLabel,
    meta,
    needsYou,
  }
}

export async function loadHomeCards(
  db: D1Database,
  input: { orgId: string; entityId: string; userId: string; offerings: PortalOfferings }
): Promise<OfferingCard[]> {
  const [operator, hostedAgent, billing] = await Promise.all([
    operatorSummaryCard(db, input.orgId, input.entityId, input.offerings),
    hostedAgentCard(db, input.offerings, input.userId),
    billingCard(db, input.orgId, input.entityId, input.offerings),
  ])
  const cards = [engagementCard(input.offerings), operator, hostedAgent, billing]
  return cards.filter((c): c is OfferingCard => c !== null)
}
