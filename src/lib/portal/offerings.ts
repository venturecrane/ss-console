/**
 * THE portal offerings resolver (portal IA rebuild, 2026-07-07).
 *
 * One module answers "what does this entity own?" for every portal
 * surface: the nav (buildPortalNav), the Home dashboard cards, and the
 * engagement destination's states all consume the same object, so the
 * tab set can never drift page-to-page again (the defect that triggered
 * the rebuild: per-page boolean props).
 *
 * Engagement facts are ORTHOGONAL, not a single state: an active client
 * can hold an open follow-on proposal at the same time (Decision #27
 * posture), and the engagement page renders the proposal spotlight above
 * the active workspace when both are set.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { listEngagements, type Engagement } from '../db/engagements'
import { listQuotesForEntity, type Quote } from '../db/quotes'
import { listActiveSubscriptionsForEntity, type SubscriptionRow } from './product-access'
import { listCustomerConfigsForEntity } from './customer-config'

const ENGAGEMENT_TERMINAL_STATUSES = new Set(['completed', 'cancelled'])

export interface EngagementOfferings {
  /** Any engagement row or portal-visible quote exists. */
  present: boolean
  activeEngagement: Engagement | null
  /** Most recent quote awaiting the client (status 'sent'). */
  openProposal: Quote | null
  pastEngagements: Engagement[]
}

/**
 * One operator the client owns (multi-operator model). An operator instance is a
 * subscription row + its config, addressed by `slug` (the config's customer_slug,
 * carried on the subscription as instance_slug). `displayName` is the active
 * persona's name (fallback: humanized slug) — the label shown in nav/home.
 */
export interface OperatorInstance {
  slug: string
  subscription: SubscriptionRow
  displayName: string
  status: string
}

/** Lite view of an operator config, passed into the pure derivation. */
export interface OperatorConfigLite {
  customer_slug: string
  displayName: string
}

export interface PortalOfferings {
  engagement: EngagementOfferings
  /** Every operator the client owns, in subscription created_at order. */
  operators: OperatorInstance[]
  /**
   * The self-serve offering's subscription (stored product_slug 'hosted-agent'),
   * or null. It is one of the client's Operators (ADR 0100: one product, the
   * offering is commercial terms plus a starting preset); it is listed apart
   * from `operators` only because it has no instance slug until SMD names it.
   */
  selfServe: SubscriptionRow | null
  /** Any portal-visible invoice exists (drives the Billing destination). */
  hasInvoices: boolean
  /**
   * A billing relationship EXISTS: invoice history, or a subscription that
   * has gone live (any status past `provisioning`). Every billing surface
   * (nav tab, home card, operator Settings' Plan & billing) gates on this ONE
   * predicate: a client easing into the product pre-go-live has no commercial
   * plane to read, so none renders (empty-section rule applied to billing —
   * Captain, 2026-07-15). The first invoice or the go-live flip reveals it
   * everywhere at once; a client who has billed with us on a prior engagement
   * keeps seeing Billing throughout.
   */
  hasBillingRelationship: boolean
  subscriptions: SubscriptionRow[]
  /**
   * When set, the client lands directly on the operator area instead of the
   * Home hub, and the Home tab is hidden (Captain, 2026-07-16). Non-null ONLY
   * while the client is pre-go-live and their whole relationship is operators:
   * nothing initiated yet (`hasBillingRelationship` false), no engagement or
   * proposal, and at least one operator. In that window the portal exists to
   * review and configure, so we skip the empty Home hub:
   *   - one operator   → its instance page, deep-linked (skips the
   *                      list→instance redirect hop that caused a visible delay)
   *   - many operators → the operator list, to pick one (standing up several
   *                      operators at once, mid-onboarding, is a valid state)
   * The instant ANY subscription goes live (`hasBillingRelationship` flips true)
   * or an engagement appears, this is null again — Home and the full tab set
   * (including Billing) return. A self-serve subscription never triggers it: a
   * self-serve client only receives portal access after implementation, when the
   * subscription is already initiated, so they are never in this window.
   */
  preGoLiveLanding: string | null
}

/** Title-case a kebab slug for a fallback display name (e.g. "pilot-smokeball" → "Pilot Smokeball"). */
export function humanizeSlug(slug: string): string {
  return slug
    .split('-')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

/** Pure derivation from already-fetched rows; unit-tested in isolation. */
export function deriveOfferings(input: {
  engagements: Engagement[]
  quotes: Quote[]
  subscriptions: SubscriptionRow[]
  operatorConfigs: OperatorConfigLite[]
  hasInvoices: boolean
}): PortalOfferings {
  const activeEngagement =
    input.engagements.find((e) => !ENGAGEMENT_TERMINAL_STATUSES.has(e.status)) ?? null
  const pastEngagements = input.engagements.filter((e) =>
    ENGAGEMENT_TERMINAL_STATUSES.has(e.status)
  )
  const openProposal = input.quotes.find((q) => q.status === 'sent') ?? null

  const bySlug = (slug: string) => input.subscriptions.find((s) => s.product_slug === slug) ?? null
  const displayNameFor = (slug: string) =>
    input.operatorConfigs.find((c) => c.customer_slug === slug)?.displayName ?? humanizeSlug(slug)

  // One entry per operator subscription. instance_slug is the instance identity;
  // a defensive filter drops any malformed operator sub with no instance_slug so
  // it never renders a broken (slug-less) card/URL.
  const operators: OperatorInstance[] = input.subscriptions
    .filter((s) => s.product_slug === 'operator' && !!s.instance_slug)
    .map((s) => ({
      slug: s.instance_slug as string,
      subscription: s,
      displayName: displayNameFor(s.instance_slug as string),
      status: s.status,
    }))

  const engagementPresent =
    activeEngagement !== null || pastEngagements.length > 0 || input.quotes.length > 0
  const hasBillingRelationship =
    input.hasInvoices || input.subscriptions.some((s) => s.status !== 'provisioning')

  // Pre-go-live operator shortcut (see preGoLiveLanding on PortalOfferings).
  // Fires only when nothing is live and the client's whole world is operators:
  // one → deep-link its instance (skipping the list hop); many → the list.
  const preGoLiveLanding =
    !hasBillingRelationship && !engagementPresent && operators.length > 0
      ? operators.length === 1
        ? `/portal/products/operator/${operators[0].slug}`
        : '/portal/products/operator'
      : null

  return {
    engagement: {
      present: engagementPresent,
      activeEngagement,
      openProposal,
      pastEngagements,
    },
    operators,
    selfServe: bySlug('hosted-agent'),
    hasInvoices: input.hasInvoices,
    hasBillingRelationship,
    subscriptions: input.subscriptions,
    preGoLiveLanding,
  }
}

export async function hasPortalVisibleInvoices(db: D1Database, entityId: string): Promise<boolean> {
  const row = await db
    .prepare(
      `SELECT 1 AS present FROM invoices
        WHERE entity_id = ? AND status IN ('sent', 'paid', 'overdue') LIMIT 1`
    )
    .bind(entityId)
    .first<{ present: number }>()
  return row !== null
}

export async function resolvePortalOfferings(
  db: D1Database,
  orgId: string,
  entityId: string
): Promise<PortalOfferings> {
  const [engagements, quotes, subscriptions, configs, hasInvoices] = await Promise.all([
    listEngagements(db, orgId, entityId),
    listQuotesForEntity(db, orgId, entityId),
    listActiveSubscriptionsForEntity(db, entityId),
    listCustomerConfigsForEntity(db, entityId),
    hasPortalVisibleInvoices(db, entityId),
  ])
  const operatorConfigs: OperatorConfigLite[] = configs.map((c) => ({
    customer_slug: c.customer_slug,
    displayName: operatorDisplayName(c.personas, c.customer_slug),
  }))
  return deriveOfferings({ engagements, quotes, subscriptions, operatorConfigs, hasInvoices })
}

/**
 * The label an operator shows in nav/home: its active persona's name, falling
 * back to the humanized slug when no persona is active/authored. Never fabricated.
 */
function operatorDisplayName(personas: { status: string; name: string }[], slug: string): string {
  const active = personas.find((p) => p.status === 'active')
  return active?.name?.trim() || humanizeSlug(slug)
}
