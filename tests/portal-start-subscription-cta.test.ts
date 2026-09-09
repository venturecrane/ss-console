/**
 * The Operator start door is one gate, offered where the client lands, and
 * acted on in one place.
 *
 * 2026-09-09, morning: the firm's partner wrote that it was time to start
 * paying, and the Captain, signed in as a client of that firm, could not
 * find a way to start the monthly subscription. The door existed, on
 * Billing, under the ledger, on one page only.
 *
 * 2026-09-09, later: with the door surfaced, the Captain read Billing as
 * confusing: a subscription block with its own button, a Paid-to-date /
 * Balance-due pair, then the invoice. Two primaries on one screen (Rule 3),
 * the balance restated beside the one invoice (Rule 2), "Being set up"
 * beside "Start" (Rule 1). Billing is now a ledger of ticket rows, and the
 * act lives on the subscription page.
 *
 * This pins:
 *   1. ONE predicate (canStartOperatorSubscription) decides the door.
 *   2. Home's Operator card carries the door as its needs-you action, ahead
 *      of the draft queue, with the authored price, linking to the
 *      subscription page.
 *   3. The Operator hero links to the same page; it does not POST.
 *   4. Billing renders subscriptions as PortalListItem rows stamped by
 *      subscriptionStamp, with no inline start button and no KPI pair.
 *   5. The subscription page holds the one POST to the start route, through
 *      the form kit, and the Manage-billing door once started.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { D1Database } from '@cloudflare/workers-types'
import {
  canStartOperatorSubscription,
  formatWholeDollars,
  operatorMonthlyPriceCents,
  operatorSubscriptionHref,
  subscriptionStamp,
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

  it('operatorSubscriptionHref is the one page every door lands on', () => {
    expect(operatorSubscriptionHref('firm-a')).toBe('/portal/billing/subscriptions/firm-a')
  })
})

describe('subscriptionStamp: one stamp per row, the state at scan time (Rule 1)', () => {
  it('a startable row reads NOT STARTED, never "being set up"', () => {
    expect(subscriptionStamp(row(), true)).toEqual({ tone: 'warning', label: 'NOT STARTED' })
  })

  it('an unpriced provisioning row is honestly being set up', () => {
    expect(subscriptionStamp(row(), false)).toEqual({ tone: 'neutral', label: 'BEING SET UP' })
  })

  it('active, paused, and scheduled cancellation each get one stamp', () => {
    expect(subscriptionStamp(row({ status: 'active' }), false)).toEqual({
      tone: 'success',
      label: 'ACTIVE',
    })
    expect(subscriptionStamp(row({ status: 'paused' }), false)).toEqual({
      tone: 'danger',
      label: 'PAUSED',
    })
    expect(
      subscriptionStamp(
        row({ status: 'active', settings_json: JSON.stringify({ cancel_at: '2026-10-08' }) }),
        false
      )
    ).toEqual({ tone: 'neutral', label: 'CANCELS' })
  })

  it('anything else is archived, not invented', () => {
    expect(subscriptionStamp(row({ status: 'cancelled' }), false).label).toBe('ARCHIVED')
  })
})

describe('Home: the Operator card carries the start door', () => {
  const input = (sub: SubscriptionRow) => ({
    orgId: ORG,
    entityId: ENTITY,
    userId: 'user-1',
    offerings: offeringsFor(sub),
  })

  it('a startable operator reads "Ready to start", shows the price, and the needs-you action lands on the subscription page', async () => {
    const cards = await loadHomeCards(makeDb({ recurringPrice: 5000 }), input(row()))
    const operator = cards.find((c) => c.key === 'operator')
    expect(operator).toBeDefined()
    expect(operator?.statusLabel).toBe('Ready to start')
    expect(operator?.meta).toEqual(['$5,000 per month'])
    expect(operator?.needsYou).toEqual({
      label: 'Start monthly subscription',
      href: '/portal/billing/subscriptions/firm-a',
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

describe('the surfaces read the one gate and act in one place', () => {
  const read = (p: string) => readFileSync(resolve(p), 'utf-8')

  it('the Operator hero links to the subscription page while provisioning; it does not POST', () => {
    const hero = read('src/components/portal/operator/facets/OperatorHero.astro')
    expect(hero).toContain('provisioning && start ?')
    expect(hero).toContain('Ready to start')
    expect(hero).toContain('href={start.href}')
    expect(hero).toContain('Start monthly subscription')
    expect(hero).not.toContain('start-subscription')
    // The plain setup posture survives for an unpriced row.
    expect(hero).toContain('Being set up')
  })

  it('the Operator page decides with the shared predicate and hands the hero the page link', () => {
    const page = read('src/pages/portal/products/operator/[instance]/index.astro')
    expect(page).toContain('canStartOperatorSubscription(subscription, priceCents)')
    expect(page).toContain('operatorSubscriptionHref(instance)')
    expect(page).toContain('start={startDoor}')
  })

  it('Billing is a ledger: stamped rows through PortalListItem, no inline start, no KPI pair', () => {
    const billing = read('src/pages/portal/billing/index.astro')
    expect(billing).toContain('canStartOperatorSubscription(sub, operatorPriceCents)')
    expect(billing).toContain('subscriptionStamp(sub, canStart(sub))')
    expect(billing).toContain('operatorSubscriptionHref(sub.instance_slug)')
    expect(billing).toContain('id="subscriptions"')
    // No POST to the start route from the ledger (the comment naming the
    // route file is documentation, not a form).
    expect(billing).not.toContain('/start-subscription`')
    expect(billing).not.toContain('Paid to date')
    expect(billing).not.toContain('Balance due')
    // The row's stamp is the status; no second status line beside it.
    expect(billing).not.toContain('subscriptionStatusLabel')
    expect(billing).not.toContain('subscriptionStatusProse')
    expect(billing).toContain('due`')
  })

  it('the subscription page holds the one POST to the start route, through the form kit', () => {
    const page = read('src/pages/portal/billing/subscriptions/[instance].astro')
    expect(page).toContain('/start-subscription`')
    expect(page).toContain(
      "import SubmitButton from '../../../../components/portal/form/SubmitButton.astro'"
    )
    expect(page).toContain('label="Start monthly subscription" tone="primary"')
    expect(page).toContain('/api/portal/billing/manage')
    expect(page).toContain('canStartOperatorSubscription(subscription, priceCents)')
    expect(page).toContain("roles.includes('principal')")
    // Exactly one primary on the page: the start form renders only when startable.
    expect(page.match(/tone="primary"/g)?.length).toBe(1)
  })

  it('the server route still re-checks the same facts (provisioning, no Stripe id, authored price)', () => {
    const route = read('src/pages/api/portal/products/operator/[instance]/start-subscription.ts')
    expect(route).toContain("sub.status !== 'provisioning' || sub.stripe_subscription_id")
    expect(route).toContain('getOperatorServiceForEntity')
  })
})

describe('the two money detail pages share one shape (Captain, 2026-09-09: cohesive, not made without knowledge of each other)', () => {
  const read = (p: string) => readFileSync(resolve(p), 'utf-8')
  const invoice = () => read('src/pages/portal/billing/invoices/[id].astro')
  const subscription = () => read('src/pages/portal/billing/subscriptions/[instance].astro')

  it('both render their facts through PortalDetailTable / PortalDetailRow', () => {
    for (const page of [invoice(), subscription()]) {
      expect(page).toContain('components/portal/PortalDetailTable.astro')
      expect(page).toContain('components/portal/PortalDetailRow.astro')
      expect(page).toContain('<PortalDetailTable eyebrow=')
    }
  })

  it('both carry one primary action with a mono caption under it, in the kit geometry', () => {
    expect(subscription()).toContain('label="Start monthly subscription" tone="primary"')
    expect(invoice()).toContain('components/portal/PortalActionLink.astro')
    expect(invoice()).toContain('tone="primary"')
    expect(invoice().match(/tone="primary"/g)?.length).toBe(1)
    for (const page of [invoice(), subscription()]) {
      expect(page).toContain('Opens secure')
      expect(page).toContain('mt-3 font-mono text-label uppercase tracking-[0.14em]')
    }
  })

  it('the invoice page left the calm card register (ADR 0082) and restates nothing the head already says', () => {
    const page = invoice()
    expect(page).not.toContain('rounded-lg')
    expect(page).not.toContain('Remaining balance')
    expect(page).not.toContain('material-symbols-outlined')
    expect(page).not.toContain('md:sticky')
    // The dates live in the head meta, once.
    expect(page).not.toContain('calendar_today')
    // The safety facts from #419 survive: Stripe link only when real; a
    // pending state that is prose, never a link to a server route.
    expect(page).toContain('stripe_hosted_url')
    expect(page).toContain("'#dev-mode'")
    expect(page).toContain('Payment link pending')
    // The #419 comment names the old route; no link may point at it.
    expect(page).not.toMatch(/href=["'{`][^"'}`]*\/api\/invoices\//)
  })

  it('the subscription page states no payment method (Stripe asks at checkout)', () => {
    expect(subscription()).not.toContain('Payment method</')
    expect(subscription()).not.toContain('label="Payment method"')
    expect(subscription()).not.toContain('Bank account or card')
  })

  it('PortalActionLink renders the same tones and geometry as the kit SubmitButton', () => {
    const link = read('src/components/portal/PortalActionLink.astro')
    const button = read('src/components/portal/form/SubmitButton.astro')
    const tone = (src: string, name: 'neutral' | 'primary') =>
      src.match(new RegExp(`${name}:\\s*\\n?\\s*'([^']+)'`))?.[1]
    expect(tone(link, 'primary')).toBeDefined()
    expect(tone(link, 'primary')).toBe(tone(button, 'primary'))
    expect(tone(link, 'neutral')).toBe(tone(button, 'neutral'))
    for (const cls of ['h-11', 'border-[3px]', 'px-5', 'font-bold uppercase tracking-[0.08em]']) {
      expect(link).toContain(cls)
      expect(button).toContain(cls)
    }
  })
})
