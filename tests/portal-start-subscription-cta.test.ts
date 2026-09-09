/**
 * The Operator start door is one gate, offered where the client lands.
 *
 * 2026-09-09: the firm's partner wrote that it was time to start paying, and
 * the Captain, signed in as a client of that firm, could not find a way to
 * start the monthly subscription. The door existed, on Billing, under the
 * ledger, on one page only; Home said "Being set up" and the Operator page
 * said "Setup in progress". Neither pointed at it. This pins:
 *
 *   1. ONE predicate (canStartOperatorSubscription) decides the door, and all
 *      three surfaces read it, so they can never disagree.
 *   2. Home's Operator card carries the door as its needs-you action, ahead
 *      of the draft queue, with the authored price.
 *   3. The Operator hero renders the door (a POST to the start route) while
 *      provisioning, and the page wires it from the same predicate.
 *   4. Billing anchors the section the other two deep-link.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  BILLING_SUBSCRIPTIONS_HREF,
  canStartOperatorSubscription,
  formatWholeDollars,
  operatorMonthlyPriceCents,
} from '../src/lib/portal/billing'
import { loadHomeCards } from '../src/lib/portal/home-cards'
import { deriveOfferings } from '../src/lib/portal/offerings'
import type { SubscriptionRow } from '../src/lib/portal/product-access'

const ORG = 'org-1'
const ENTITY = 'entity-1'

function row(over: Partial<SubscriptionRow> = {}): SubscriptionRow {
  return {
    id: 'sub-op-1',
    org_id: ORG,
    entity_id: ENTITY,
    product_slug: 'operator',
    instance_slug: 'firm-a',
    status: 'provisioning',
    started_at: '2026-07-15 00:00:00',
    ended_at: null,
    settings_json: null,
    service_id: null,
    stripe_subscription_id: null,
    created_at: '2026-07-15 00:00:00',
    updated_at: '2026-07-15 00:00:00',
    ...over,
  }
}

/**
 * Fake of every D1 read the Home cards issue, dispatched on SQL text:
 * the operator service (price), fleet_status (aliveness), the runtime
 * summary (draft queue), and the invoice ledger. Anything else throws so
 * a new read cannot pass unnoticed.
 */
function makeDb(opts: {
  recurringPrice?: number | null
  draftQueueDepth?: number
  invoices?: Array<Record<string, unknown>>
}): D1Database {
  const db = {
    prepare(sql: string) {
      return {
        bind() {
          return {
            first() {
              if (sql.includes('FROM services')) {
                return Promise.resolve(
                  opts.recurringPrice === undefined
                    ? null
                    : { id: 'svc-1', type: 'operator', recurring_price: opts.recurringPrice }
                )
              }
              if (sql.includes('FROM fleet_status')) return Promise.resolve(null)
              if (sql.includes('operator_runtime_summary')) {
                return Promise.resolve({ draft_queue_depth: opts.draftQueueDepth ?? null })
              }
              throw new Error(`unexpected first() for sql: ${sql}`)
            },
            all() {
              if (sql.includes('FROM invoices')) {
                return Promise.resolve({ results: opts.invoices ?? [] })
              }
              throw new Error(`unexpected all() for sql: ${sql}`)
            },
            run() {
              throw new Error(`unexpected run() for sql: ${sql}`)
            },
          }
        },
      }
    },
  }
  return db as unknown as D1Database
}

function offeringsFor(sub: SubscriptionRow, hasInvoices = true) {
  return deriveOfferings({
    engagements: [],
    quotes: [],
    subscriptions: [sub],
    operatorConfigs: [{ customer_slug: 'firm-a', displayName: 'Firm A' }],
    hasInvoices,
  })
}

describe('canStartOperatorSubscription: the one start gate', () => {
  it('opens for a priced, provisioning operator row with no Stripe subscription', () => {
    expect(canStartOperatorSubscription(row(), 500000)).toBe(true)
  })

  it('closes once a Stripe subscription is attached (already started)', () => {
    expect(canStartOperatorSubscription(row({ stripe_subscription_id: 'sub_x' }), 500000)).toBe(
      false
    )
  })

  it('closes for any status other than provisioning', () => {
    expect(canStartOperatorSubscription(row({ status: 'active' }), 500000)).toBe(false)
    expect(canStartOperatorSubscription(row({ status: 'paused' }), 500000)).toBe(false)
    expect(canStartOperatorSubscription(row({ status: 'cancelled' }), 500000)).toBe(false)
  })

  it('closes without an authored price (no price, no door, nothing invented)', () => {
    expect(canStartOperatorSubscription(row(), null)).toBe(false)
  })

  it('closes for a slug-less row and for other products', () => {
    expect(canStartOperatorSubscription(row({ instance_slug: null }), 500000)).toBe(false)
    expect(canStartOperatorSubscription(row({ product_slug: 'hosted-agent' }), 500000)).toBe(false)
  })

  it('operatorMonthlyPriceCents accepts only a positive finite dollar figure', () => {
    expect(operatorMonthlyPriceCents(5000)).toBe(500000)
    expect(operatorMonthlyPriceCents(0)).toBeNull()
    expect(operatorMonthlyPriceCents(-1)).toBeNull()
    expect(operatorMonthlyPriceCents(null)).toBeNull()
    expect(operatorMonthlyPriceCents(undefined)).toBeNull()
    expect(operatorMonthlyPriceCents('5000')).toBeNull()
    expect(operatorMonthlyPriceCents(Number.NaN)).toBeNull()
  })

  it('formatWholeDollars renders whole dollars, like MoneyDisplay', () => {
    expect(formatWholeDollars(500000)).toBe('$5,000')
    expect(formatWholeDollars(123456)).toBe('$1,235')
  })
})

describe('Home: the Operator card carries the start door', () => {
  const input = (sub: SubscriptionRow) => ({
    orgId: ORG,
    entityId: ENTITY,
    userId: 'user-1',
    offerings: offeringsFor(sub),
  })

  it('a startable operator reads "Ready to start", shows the price, and the needs-you action is the door', async () => {
    const cards = await loadHomeCards(makeDb({ recurringPrice: 5000 }), input(row()))
    const operator = cards.find((c) => c.key === 'operator')
    expect(operator).toBeDefined()
    expect(operator?.statusLabel).toBe('Ready to start')
    expect(operator?.meta).toEqual(['$5,000 per month'])
    expect(operator?.needsYou).toEqual({
      label: 'Start monthly subscription',
      href: BILLING_SUBSCRIPTIONS_HREF,
    })
  })

  it('the door outranks the draft queue: money first, drafts after', async () => {
    const cards = await loadHomeCards(
      makeDb({ recurringPrice: 5000, draftQueueDepth: 3 }),
      input(row())
    )
    expect(cards.find((c) => c.key === 'operator')?.needsYou?.label).toBe(
      'Start monthly subscription'
    )
  })

  it('once started, the card is the live status again and the drafts action returns', async () => {
    const cards = await loadHomeCards(
      makeDb({ recurringPrice: 5000, draftQueueDepth: 2 }),
      input(row({ status: 'active', stripe_subscription_id: 'sub_x' }))
    )
    const operator = cards.find((c) => c.key === 'operator')
    expect(operator?.statusLabel).toBe('Active')
    expect(operator?.needsYou?.label).toBe('2 drafts waiting for review')
  })

  it('unpriced: no door, the honest setup posture stays', async () => {
    const cards = await loadHomeCards(makeDb({ recurringPrice: null }), input(row()))
    const operator = cards.find((c) => c.key === 'operator')
    expect(operator?.statusLabel).toBe('Being set up')
    expect(operator?.needsYou).toBeNull()
  })

  it('the Billing card still carries the pending invoice: two doors, both visible', async () => {
    const cards = await loadHomeCards(
      makeDb({
        recurringPrice: 5000,
        invoices: [
          {
            id: 'inv-1',
            status: 'sent',
            amount: 4000,
            due_date: '2026-09-28',
            paid_at: null,
            created_at: '2026-08-29',
          },
        ],
      }),
      input(row())
    )
    expect(cards.find((c) => c.key === 'billing')?.needsYou).toEqual({
      label: 'Pay invoice',
      href: '/portal/billing/invoices/inv-1',
    })
    expect(cards.find((c) => c.key === 'operator')?.needsYou?.label).toBe(
      'Start monthly subscription'
    )
  })
})

describe('the three surfaces read the one gate', () => {
  const read = (p: string) => readFileSync(resolve(p), 'utf-8')

  it('the Operator hero renders the door as a POST to the start route while provisioning', () => {
    const hero = read('src/components/portal/operator/facets/OperatorHero.astro')
    expect(hero).toContain('provisioning && start ?')
    expect(hero).toContain('Ready to start')
    expect(hero).toContain('<form method="POST" action={start.action}')
    expect(hero).toContain('Start monthly subscription')
    // The plain setup posture survives for an unpriced row.
    expect(hero).toContain('Being set up')
  })

  it('the Operator page decides with the shared predicate and passes the door to the hero', () => {
    const page = read('src/pages/portal/products/operator/[instance]/index.astro')
    expect(page).toContain('canStartOperatorSubscription(subscription, priceCents)')
    expect(page).toContain('/start-subscription`')
    expect(page).toContain('start={startDoor}')
  })

  it('Billing reads the shared predicate and anchors the section Home deep-links', () => {
    const billing = read('src/pages/portal/billing/index.astro')
    expect(billing).toContain('canStartOperatorSubscription(sub, operatorPriceCents)')
    expect(billing).toContain('id="subscriptions"')
    expect(billing).toContain('Start monthly subscription')
    expect(BILLING_SUBSCRIPTIONS_HREF).toBe('/portal/billing#subscriptions')
  })

  it('the server route still re-checks the same facts (provisioning, no Stripe id, authored price)', () => {
    const route = read('src/pages/api/portal/products/operator/[instance]/start-subscription.ts')
    expect(route).toContain("sub.status !== 'provisioning' || sub.stripe_subscription_id")
    expect(route).toContain('getOperatorServiceForEntity')
  })
})
