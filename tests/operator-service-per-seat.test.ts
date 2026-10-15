/**
 * An Operator price belongs to the SEAT, not the client (migration 0126).
 *
 * The first Operator client is about to take a second seat. Before 0126 the
 * commercial record was keyed to the client entity, so authoring the second
 * seat's price overwrote the first seat's, its checkout charged the first
 * seat's amount on a NEW Stripe customer, the hub's billing controls acted on
 * whichever subscription row came back first, and revenue counted one price
 * once per seat.
 *
 * Every case here seeds ONE client with TWO seats, each priced differently,
 * on real migrated D1, and drives the real code: the writer, the portal start
 * route, the signed Stripe webhook route, the admin billing route, and the
 * revenue math. Only Stripe's network is faked (global fetch), and the email
 * sender.
 *
 * What would make these red: the setter going back to entity keying (seat 1
 * changes), the start route reading the entity's service or dropping
 * `customer=`, the webhook binding by entity, the billing route acting on an
 * unnamed seat, MRR summing per seat, the backfill linking an ambiguous
 * entity, or the invoice copy regaining its em dash.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

vi.mock('../src/lib/email/resend', () => ({
  sendEmail: vi.fn(async () => ({ success: true })),
}))

import { ORG_ID } from '../src/lib/constants'
import {
  listOperatorServicesForEntity,
  getOperatorServiceForSeat,
  listOperatorSeatCommercials,
  OperatorSeatNotFoundError,
  setOperatorSeatCommercials,
} from '../src/lib/db/services'
import { AmbiguousSubscriptionError, getSubscriptionForBilling } from '../src/lib/db/subscriptions'
import { operatorRecurring, seatLines } from '../src/lib/admin/billing-view'
import {
  operatorSubscriptionDescription,
  retainerInvoiceDescription,
} from '../src/lib/stripe/subscriptions'
import { handleRetainerInvoicePaid } from '../src/lib/webhooks/stripe-subscription-handler'
import { POST as startSubscription } from '../src/pages/api/portal/products/operator/[instance]/start-subscription'
import { POST as stripeWebhook } from '../src/pages/api/webhooks/stripe'
import { POST as subscriptionBilling } from '../src/pages/api/admin/clients/[id]/subscription-billing'
import { POST as operatorPrice } from '../src/pages/api/admin/clients/[id]/operator-price'

installWorkerdPolyfills()

const migrationsDir = resolve(process.cwd(), 'migrations')
const ENTITY = 'ent-firm'
const OTHER_ENTITY = 'ent-other'
const SEAT_1 = 'firm-legal'
const SEAT_2 = 'firm-marketing'
const CLERK_ID = 'user_firm_principal'
const USER_ID = 'u-firm-principal'
const WEBHOOK_SECRET = 'whsec_test_per_seat'

async function seedClient(db: D1Database): Promise<void> {
  await db
    .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
    .bind(ORG_ID, 'SMD Services', 'smd-services')
    .run()
  for (const [id, slug] of [
    [ENTITY, 'firm'],
    [OTHER_ENTITY, 'other'],
  ]) {
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind(id, ORG_ID, slug, slug)
      .run()
  }
  await db
    .prepare(
      `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
       VALUES (?, ?, 'principal@firm.example', 'Prin Cipal', 'client', ?, ?)`
    )
    .bind(USER_ID, ORG_ID, ENTITY, CLERK_ID)
    .run()
  await db
    .prepare(
      `INSERT INTO product_roles (id, org_id, user_id, entity_id, product_slug, role)
       VALUES ('pr-firm', ?, ?, ?, 'operator', 'principal')`
    )
    .bind(ORG_ID, USER_ID, ENTITY)
    .run()
}

async function seedSeat(
  db: D1Database,
  slug: string,
  opts: { entityId?: string; status?: string; stripeId?: string | null } = {}
): Promise<void> {
  const entityId = opts.entityId ?? ENTITY
  await db
    .prepare(
      `INSERT INTO customer_configs (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1', '[]', 'abc', datetime('now'))`
    )
    .bind(entityId, ORG_ID, slug)
    .run()
  await db
    .prepare(
      `INSERT INTO subscriptions (id, org_id, entity_id, product_slug, instance_slug, status, stripe_subscription_id, settings_json)
       VALUES (?, ?, ?, 'operator', ?, ?, ?, ?)`
    )
    .bind(
      `sub-${slug}`,
      ORG_ID,
      entityId,
      slug,
      opts.status ?? 'provisioning',
      opts.stripeId ?? null,
      opts.stripeId ? JSON.stringify({ stripe_customer_id: 'cus_mirror_stale' }) : null
    )
    .run()
}

async function seatRow(db: D1Database, slug: string) {
  return db
    .prepare('SELECT status, stripe_subscription_id, service_id FROM subscriptions WHERE id = ?')
    .bind(`sub-${slug}`)
    .first<{ status: string; stripe_subscription_id: string | null; service_id: string | null }>()
}

/** Seat 1 live on Stripe at 5000 by card; seat 2 provisioning at 2500 ACH, labelled. */
async function seedTwoPricedSeats(db: D1Database): Promise<void> {
  await seedClient(db)
  await seedSeat(db, SEAT_1, { status: 'active', stripeId: 'sub_stripe_seat1' })
  await seedSeat(db, SEAT_2)
  await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_1, { price: 5000, method: 'card' })
  await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_2, {
    price: 2500,
    method: 'ach',
    label: 'Marketing Operator',
  })
}

function resetEnv(extra: Record<string, unknown>): void {
  for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
  Object.assign(testEnv, extra)
}

describe('the writer: pricing seat 2 never touches seat 1', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedClient(db)
    await seedSeat(db, SEAT_1, { status: 'active', stripeId: 'sub_stripe_seat1' })
    await seedSeat(db, SEAT_2)
  })

  it('gives each seat its own service, price, rail and label', async () => {
    const one = await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_1, {
      price: 5000,
      method: 'card',
    })
    const before = JSON.stringify(one)
    const two = await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_2, {
      price: 2500,
      method: 'ach',
      label: 'Marketing Operator',
    })
    expect(two.id).not.toBe(one.id)
    expect((await seatRow(db, SEAT_1))?.service_id).toBe(one.id)
    expect((await seatRow(db, SEAT_2))?.service_id).toBe(two.id)
    // Seat 1's line, re-read from the database, is byte-for-byte what it was.
    const oneAfter = await getOperatorServiceForSeat(db, ORG_ID, { service_id: one.id })
    expect(JSON.stringify(oneAfter)).toBe(before)
    expect(two).toMatchObject({
      recurring_price: 2500,
      payment_method: 'ach',
      label: 'Marketing Operator',
    })
    // Re-pricing seat 2 updates its own line in place, still not seat 1's.
    const twoAgain = await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_2, {
      price: 3000,
      method: null,
    })
    expect(twoAgain.id).toBe(two.id)
    expect(twoAgain).toMatchObject({
      recurring_price: 3000,
      payment_method: 'ach',
      label: 'Marketing Operator',
    })
    expect(
      JSON.stringify(await getOperatorServiceForSeat(db, ORG_ID, { service_id: one.id }))
    ).toBe(before)
  })

  it('one client, two lines: the entity holds both, each seat reads its own', async () => {
    const one = await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_1, {
      price: 5000,
      method: 'card',
    })
    const two = await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_2, {
      price: 2500,
      method: 'ach',
    })
    const lines = await listOperatorServicesForEntity(db, ORG_ID, ENTITY)
    expect(lines.map((s) => s.id).sort()).toEqual([one.id, two.id].sort())
    const seats = await listOperatorSeatCommercials(db, ORG_ID, ENTITY)
    expect(seats.map((s) => [s.instance_slug, s.recurring_price])).toEqual([
      [SEAT_1, 5000],
      [SEAT_2, 2500],
    ])
  })

  it("refuses another client's seat, and writes nothing", async () => {
    await seedSeat(db, 'other-seat', { entityId: OTHER_ENTITY })
    await expect(
      setOperatorSeatCommercials(db, ORG_ID, ENTITY, 'other-seat', { price: 1, method: 'ach' })
    ).rejects.toBeInstanceOf(OperatorSeatNotFoundError)
    const count = await db.prepare(`SELECT COUNT(*) AS n FROM services`).first<{ n: number }>()
    expect(count?.n).toBe(0)
  })

  it('the admin route prices the NAMED seat and refuses a foreign one', async () => {
    resetEnv({ DB: db })
    const call = (fields: Record<string, string>, entityId = ENTITY) => {
      const form = new FormData()
      for (const [k, v] of Object.entries(fields)) form.set(k, v)
      return operatorPrice({
        request: new Request('https://admin.smd.services/x', { method: 'POST', body: form }),
        locals: {
          session: { role: 'admin', orgId: ORG_ID, userId: 'a', email: 'a@x', expiresAt: '' },
        },
        params: { id: entityId },
        redirect: (url: string, status: number) =>
          new Response(null, { status, headers: { Location: url } }),
      } as unknown as Parameters<typeof operatorPrice>[0])
    }
    const ok = await call({
      instance: SEAT_2,
      monthly_price: '2500',
      payment_method: 'ach',
      label: 'Marketing Operator',
    })
    expect(ok.headers.get('Location')).toBe(`/admin/clients/${ENTITY}?priced=1`)
    const seats = await listOperatorSeatCommercials(db, ORG_ID, ENTITY)
    expect(seats.find((s) => s.instance_slug === SEAT_2)).toMatchObject({
      recurring_price: 2500,
      label: 'Marketing Operator',
    })
    expect(seats.find((s) => s.instance_slug === SEAT_1)?.service_id).toBeNull()

    await seedSeat(db, 'other-seat', { entityId: OTHER_ENTITY })
    const foreign = await call({ instance: 'other-seat', monthly_price: '1' })
    expect(foreign.headers.get('Location')).toBe(`/admin/clients/${ENTITY}?error=no_seat`)
    const missing = await call({ monthly_price: '1' })
    expect(missing.headers.get('Location')).toBe(`/admin/clients/${ENTITY}?error=no_seat`)
  })
})

describe("the portal start route: seat 2 is charged seat 2's price on the firm's existing Stripe customer", () => {
  let db: D1Database
  const calls: Array<{ url: string; method: string; body: URLSearchParams | null }> = []

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedTwoPricedSeats(db)
    resetEnv({
      DB: db,
      STRIPE_API_KEY: 'sk_test_per_seat',
      PORTAL_BASE_URL: 'https://portal.smd.services',
    })
    calls.length = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init?: { method?: string; body?: string }) => {
        const url = String(input)
        const method = init?.method ?? 'GET'
        calls.push({ url, method, body: init?.body ? new URLSearchParams(init.body) : null })
        if (url.includes('/subscriptions/sub_stripe_seat1')) {
          return new Response(JSON.stringify({ id: 'sub_stripe_seat1', customer: 'cus_firm' }))
        }
        if (url.includes('/products/search')) {
          return new Response(JSON.stringify({ data: [{ id: 'prod_retainer' }] }))
        }
        if (url.endsWith('/checkout/sessions') && method === 'POST') {
          return new Response(
            JSON.stringify({ id: 'cs_seat2', url: 'https://checkout.stripe.com/c/cs_seat2' })
          )
        }
        return new Response('unexpected', { status: 500 })
      })
    )
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  function locals(): App.Locals {
    return {
      auth: () => ({ userId: CLERK_ID, orgId: null, sessionId: null }),
      currentUser: async () => ({
        primaryEmailAddress: {
          emailAddress: 'principal@firm.example',
          verification: { status: 'verified' },
        },
        emailAddresses: [
          { emailAddress: 'principal@firm.example', verification: { status: 'verified' } },
        ],
        firstName: 'Prin',
        lastName: 'Cipal',
        username: null,
      }),
      cfContext: undefined,
    } as unknown as App.Locals
  }

  it("sends seat 2's price, rail and label, and customer= seat 1's Stripe customer", async () => {
    const res = await startSubscription({
      params: { instance: SEAT_2 },
      locals: locals(),
    } as unknown as Parameters<typeof startSubscription>[0])
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe('https://checkout.stripe.com/c/cs_seat2')

    // The customer came from Stripe (the authority), not the stale local mirror.
    expect(calls.some((c) => c.url.endsWith('/subscriptions/sub_stripe_seat1'))).toBe(true)
    const session = calls.find((c) => c.url.endsWith('/checkout/sessions'))?.body
    expect(session).toBeTruthy()
    expect(session!.get('customer')).toBe('cus_firm')
    expect(session!.has('customer_email')).toBe(false)
    expect(session!.get('line_items[0][price_data][unit_amount]')).toBe('250000')
    // Seat 2 is ACH: no card-fee line, bank account only.
    expect(session!.has('line_items[1][price_data][unit_amount]')).toBe(false)
    expect(session!.get('payment_method_types[]')).toBe('us_bank_account')
    expect(session!.get('subscription_data[description]')).toBe('Marketing Operator')
    expect(session!.get('metadata[smd_subscription_id]')).toBe(`sub-${SEAT_2}`)
    const seat2Service = (await seatRow(db, SEAT_2))?.service_id
    expect(session!.get('metadata[smd_service_id]')).toBe(seat2Service)
    expect(session!.get('subscription_data[metadata][smd_service_id]')).toBe(seat2Service)
  })

  it('a first seat, with no sibling on Stripe, keys a new customer by email as before', async () => {
    await db
      .prepare(`UPDATE subscriptions SET stripe_subscription_id = NULL WHERE id = ?`)
      .bind(`sub-${SEAT_1}`)
      .run()
    await startSubscription({
      params: { instance: SEAT_2 },
      locals: locals(),
    } as unknown as Parameters<typeof startSubscription>[0])
    const session = calls.find((c) => c.url.endsWith('/checkout/sessions'))?.body
    expect(session!.get('customer_email')).toBe('principal@firm.example')
    expect(session!.has('customer')).toBe(false)
  })

  it('an unpriced seat 2 is refused even though seat 1 is priced', async () => {
    await setOperatorSeatCommercials(db, ORG_ID, ENTITY, SEAT_2, { price: null, method: null })
    const res = await startSubscription({
      params: { instance: SEAT_2 },
      locals: locals(),
    } as unknown as Parameters<typeof startSubscription>[0])
    expect(res.headers.get('Location')).toBe('/portal/billing?start=unpriced')
    expect(calls.some((c) => c.url.endsWith('/checkout/sessions'))).toBe(false)
  })
})

async function signedPost(body: string): Promise<Response> {
  const ts = Math.floor(Date.now() / 1000)
  const enc = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(WEBHOOK_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, enc.encode(`${ts}.${body}`))
  const sig = Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
  return stripeWebhook({
    request: new Request('http://test.local/api/webhooks/stripe', {
      method: 'POST',
      headers: new Headers({
        'Content-Type': 'application/json',
        'stripe-signature': `t=${ts},v1=${sig}`,
      }),
      body,
    }),
    params: {},
    locals: {},
    redirect: (url: string, status: number) =>
      new Response(null, { status, headers: { Location: url } }),
  } as unknown as Parameters<typeof stripeWebhook>[0])
}

describe('the signed checkout.session.completed for seat 2 binds seat 2 only', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedTwoPricedSeats(db)
    resetEnv({ DB: db, STRIPE_WEBHOOK_SECRET: WEBHOOK_SECRET })
    vi.spyOn(console, 'log').mockImplementation(() => {})
  })
  afterEach(() => vi.restoreAllMocks())

  it('attaches the new Stripe subscription to seat 2 and leaves seat 1 exactly as it was', async () => {
    const seat1Before = await seatRow(db, SEAT_1)
    const res = await signedPost(
      JSON.stringify({
        id: 'evt_seat2',
        object: 'event',
        type: 'checkout.session.completed',
        created: 1785000000,
        data: {
          object: {
            id: 'cs_seat2',
            object: 'checkout.session',
            client_reference_id: USER_ID,
            customer: 'cus_firm',
            subscription: 'sub_stripe_seat2',
            amount_total: 250000,
            customer_details: { email: 'principal@firm.example', name: 'Prin' },
            metadata: {
              product_slug: 'operator',
              smd_entity_id: ENTITY,
              smd_subscription_id: `sub-${SEAT_2}`,
            },
            payment_status: 'paid',
          },
        },
      })
    )
    expect(res.status).toBe(200)
    expect(await seatRow(db, SEAT_2)).toMatchObject({
      status: 'active',
      stripe_subscription_id: 'sub_stripe_seat2',
    })
    expect(await seatRow(db, SEAT_1)).toEqual(seat1Before)
  })
})

describe('admin billing controls act on the named seat', () => {
  let db: D1Database
  const stripeCalls: string[] = []
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedTwoPricedSeats(db)
    await db
      .prepare(
        `UPDATE subscriptions SET status = 'active', stripe_subscription_id = 'sub_stripe_seat2' WHERE id = ?`
      )
      .bind(`sub-${SEAT_2}`)
      .run()
    resetEnv({ DB: db, STRIPE_API_KEY: 'sk_test_per_seat' })
    stripeCalls.length = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init?: { method?: string }) => {
        stripeCalls.push(`${init?.method ?? 'GET'} ${String(input)}`)
        const id = String(input).split('/').pop()
        return new Response(JSON.stringify({ id, status: 'active' }))
      })
    )
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  const call = (fields: Record<string, string>) => {
    const form = new FormData()
    for (const [k, v] of Object.entries(fields)) form.set(k, v)
    return subscriptionBilling({
      request: new Request('https://admin.smd.services/x', { method: 'POST', body: form }),
      locals: {
        session: { role: 'admin', orgId: ORG_ID, userId: 'a', email: 'a@x', expiresAt: '' },
      },
      params: { id: ENTITY },
      redirect: (url: string, status: number) =>
        new Response(null, { status, headers: { Location: url } }),
    } as unknown as Parameters<typeof subscriptionBilling>[0])
  }

  it('pause with instance=seat 2 pauses seat 2 at Stripe and locally, and nothing else', async () => {
    const res = await call({ action: 'pause', instance: SEAT_2 })
    expect(res.headers.get('Location')).toBe(`/admin/clients/${ENTITY}?billing=paused`)
    expect(stripeCalls).toEqual(['POST https://api.stripe.com/v1/subscriptions/sub_stripe_seat2'])
    expect((await seatRow(db, SEAT_2))?.status).toBe('paused')
    expect((await seatRow(db, SEAT_1))?.status).toBe('active')
  })

  it('an unnamed action on a two-seat client is refused and reaches Stripe for neither', async () => {
    const res = await call({ action: 'pause' })
    expect(res.headers.get('Location')).toBe(`/admin/clients/${ENTITY}?error=seat_required`)
    expect(stripeCalls).toEqual([])
    expect((await seatRow(db, SEAT_1))?.status).toBe('active')
    expect((await seatRow(db, SEAT_2))?.status).toBe('active')
  })

  it('the billing read refuses to guess without a seat', async () => {
    await expect(getSubscriptionForBilling(db, ENTITY, 'operator')).rejects.toBeInstanceOf(
      AmbiguousSubscriptionError
    )
    expect((await getSubscriptionForBilling(db, ENTITY, 'operator', SEAT_2))?.id).toBe(
      `sub-${SEAT_2}`
    )
  })
})

describe('revenue counts each commercial line once', () => {
  it('two seats sharing one line count it once; two lines count both; unpriced seats are named', () => {
    const lines = seatLines([
      {
        instance_slug: 'smd-a',
        service_id: 'svc_smd',
        recurring_price: 100,
        service_status: 'active',
      },
      {
        instance_slug: 'smd-b',
        service_id: 'svc_smd',
        recurring_price: 100,
        service_status: 'active',
      },
      {
        instance_slug: SEAT_1,
        service_id: 'svc_1',
        recurring_price: 5000,
        service_status: 'active',
      },
      {
        instance_slug: SEAT_2,
        service_id: 'svc_2',
        recurring_price: 2500,
        service_status: 'active',
      },
      {
        instance_slug: 'churned',
        service_id: 'svc_old',
        recurring_price: 900,
        service_status: 'churned',
      },
      { instance_slug: 'unlinked', service_id: null, recurring_price: null, service_status: null },
    ])
    const r = operatorRecurring(
      ['smd-a', 'smd-b', SEAT_1, SEAT_2, 'churned', 'unlinked', 'no-row'],
      lines
    )
    expect(r.mrr).toBe(100 + 5000 + 2500)
    expect(r.activeCount).toBe(7)
    expect(r.unpricedCount).toBe(3) // churned line, unlinked seat, seat with no row
  })

  it('on real D1, both of the client’s seats are priced from their own lines', async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedTwoPricedSeats(db)
    const r = operatorRecurring(
      [SEAT_1, SEAT_2],
      seatLines(await listOperatorSeatCommercials(db, ORG_ID))
    )
    expect(r).toEqual({ mrr: 7500, activeCount: 2, unpricedCount: 0 })
  })
})

describe('the invoice and Stripe copy', () => {
  it('reads as plain words with no em dash, and carries the seat label when set', () => {
    expect(retainerInvoiceDescription(null)).toBe('Operator retainer, monthly')
    expect(retainerInvoiceDescription('  ')).toBe('Operator retainer, monthly')
    expect(retainerInvoiceDescription('Marketing Operator')).toBe(
      'Operator retainer, monthly (Marketing Operator)'
    )
    expect(operatorSubscriptionDescription(null)).toBe('Operator retainer')
    expect(operatorSubscriptionDescription('Marketing Operator')).toBe('Marketing Operator')
    for (const s of [
      retainerInvoiceDescription(null),
      retainerInvoiceDescription('Marketing Operator'),
      operatorSubscriptionDescription(null),
    ]) {
      expect(s).not.toMatch(/—/)
    }
  })

  it("a paid cycle invoice for seat 2 is mirrored with seat 2's label", async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await seedTwoPricedSeats(db)
    await db
      .prepare(
        `UPDATE subscriptions SET status = 'active', stripe_subscription_id = 'sub_stripe_seat2' WHERE id = ?`
      )
      .bind(`sub-${SEAT_2}`)
      .run()
    vi.spyOn(console, 'log').mockImplementation(() => {})
    await handleRetainerInvoicePaid(db, undefined, 'sub_stripe_seat2', {
      id: 'in_seat2',
      amount_due: 250000,
      amount_paid: 250000,
      hosted_invoice_url: null,
      due_date: null,
      status_transitions: { paid_at: 1785100000 },
      customer: 'cus_firm',
    })
    const row = await db
      .prepare(`SELECT description FROM invoices WHERE stripe_invoice_id = 'in_seat2'`)
      .first<{ description: string }>()
    expect(row?.description).toBe('Operator retainer, monthly (Marketing Operator)')
    vi.restoreAllMocks()
  })
})

describe('migration 0126 backfill', () => {
  it('links a seat only when its entity has exactly one operator service', async () => {
    const db = createTestD1()
    const all = discoverNumericMigrations(migrationsDir)
    const upTo = all.filter((f) => !/0126_operator_service_per_seat\.sql$/.test(String(f)))
    const m0126 = all.filter((f) => /0126_operator_service_per_seat\.sql$/.test(String(f)))
    expect(m0126).toHaveLength(1)
    await runMigrations(db, { files: upTo })
    await seedClient(db)
    // OTHER_ENTITY: one service, two seats -> both linked. ENTITY: no service -> unlinked.
    await db
      .prepare(
        `INSERT INTO services (id, org_id, entity_id, type, cadence, status, recurring_price)
         VALUES ('svc_only', ?, ?, 'operator', 'recurring', 'active', 0)`
      )
      .bind(ORG_ID, OTHER_ENTITY)
      .run()
    for (const [id, entity, slug, svc] of [
      ['s-a', OTHER_ENTITY, 'o-a', null],
      ['s-b', OTHER_ENTITY, 'o-b', null],
      ['s-c', ENTITY, 'f-c', null],
      ['s-d', OTHER_ENTITY, 'o-d', 'svc_kept'],
    ]) {
      await db
        .prepare(
          `INSERT INTO subscriptions (id, org_id, entity_id, product_slug, instance_slug, status, service_id)
           VALUES (?, ?, ?, 'operator', ?, 'active', ?)`
        )
        .bind(id, ORG_ID, entity, slug, svc)
        .run()
    }
    await runMigrations(db, { files: m0126 })
    const rows = await db
      .prepare(`SELECT id, service_id FROM subscriptions ORDER BY id`)
      .all<{ id: string; service_id: string | null }>()
    expect(rows.results).toEqual([
      { id: 's-a', service_id: 'svc_only' },
      { id: 's-b', service_id: 'svc_only' },
      { id: 's-c', service_id: null },
      { id: 's-d', service_id: 'svc_kept' }, // an existing link is never overwritten
    ])
    // The index is gone: a second operator service on one entity is now legal.
    await db
      .prepare(
        `INSERT INTO services (id, org_id, entity_id, type, cadence, status) VALUES ('svc_two', ?, ?, 'operator', 'recurring', 'active')`
      )
      .bind(ORG_ID, OTHER_ENTITY)
      .run()
  })
})
