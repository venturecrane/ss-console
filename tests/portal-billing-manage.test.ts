/**
 * Behavioral coverage for POST /api/portal/billing/manage (2026-08-14 code
 * review, Testing #2 — this route creates live Stripe Billing Portal
 * sessions and had zero coverage).
 *
 * Real migrated D1 + the real route handler; only two seams are faked:
 * Stripe (createBillingPortalSession is mocked — no network) and Clerk
 * (locals.auth()/currentUser(), same seam middleware-behavior.test.ts
 * fakes). Every gate is exercised: sign-in bounce, slug allowlist,
 * principal role gate, missing Stripe customer, the operator
 * instance-addressed return path, the happy 303 to Stripe, and the
 * Stripe-failure fallback.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

import { ORG_ID } from '../src/lib/constants'

const createBillingPortalSession = vi.fn()
vi.mock('../src/lib/stripe/checkout', () => ({
  createBillingPortalSession: (...args: unknown[]) => createBillingPortalSession(...args),
}))

// Import AFTER the mock so the route binds the mocked module.
import { POST } from '../src/pages/api/portal/billing/manage'

const migrationsDir = resolve(process.cwd(), 'migrations')

const CLERK_ID = 'user_billing_manage'
const USER_ID = 'u-billing-manage'
const ENTITY_ID = 'entity-billing-manage'

function makeLocals(opts: { signedIn?: boolean } = {}): App.Locals {
  const signedIn = opts.signedIn ?? true
  return {
    auth: () => ({
      userId: signedIn ? CLERK_ID : null,
      orgId: null,
      sessionId: null,
    }),
    currentUser: async () =>
      signedIn
        ? {
            primaryEmailAddress: {
              emailAddress: 'principal@example.com',
              verification: { status: 'verified' },
            },
            emailAddresses: [
              {
                emailAddress: 'principal@example.com',
                verification: { status: 'verified' },
              },
            ],
            firstName: 'Prin',
            lastName: 'Cipal',
            username: null,
          }
        : null,
    cfContext: undefined,
  } as unknown as App.Locals
}

function makeRequest(fields: Record<string, string>): Request {
  const form = new FormData()
  for (const [k, v] of Object.entries(fields)) form.set(k, v)
  return new Request('https://portal.smd.services/api/portal/billing/manage', {
    method: 'POST',
    body: form,
  })
}

async function callRoute(
  fields: Record<string, string>,
  opts: { signedIn?: boolean } = {}
): Promise<Response> {
  // The route only reads locals + request.
  return POST({
    locals: makeLocals(opts),
    request: makeRequest(fields),
  } as unknown as Parameters<typeof POST>[0])
}

describe('POST /api/portal/billing/manage', () => {
  let db: D1Database

  beforeEach(async () => {
    createBillingPortalSession.mockReset()
    createBillingPortalSession.mockResolvedValue('https://billing.stripe.com/p/session_test_123')

    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, {
      DB: db,
      STRIPE_API_KEY: 'sk_test_fake',
      PORTAL_BASE_URL: 'https://portal.smd.services',
    })

    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind(ENTITY_ID, ORG_ID, 'Billing Manage Biz', 'billing-manage-biz')
      .run()
    // Clerk-bound user directly bound to the entity (single-user portal path).
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id)
         VALUES (?, ?, ?, ?, 'client', ?, ?)`
      )
      .bind(USER_ID, ORG_ID, 'principal@example.com', 'Prin Cipal', ENTITY_ID, CLERK_ID)
      .run()
  })

  async function seedSubscription(opts: {
    slug: string
    settingsJson?: string | null
    instanceSlug?: string | null
  }): Promise<void> {
    await db
      .prepare(
        `INSERT INTO subscriptions (id, org_id, entity_id, product_slug, instance_slug, status, settings_json)
         VALUES (?, ?, ?, ?, ?, 'active', ?)`
      )
      .bind(
        `sub-${opts.slug}-${opts.instanceSlug ?? 'default'}`,
        ORG_ID,
        ENTITY_ID,
        opts.slug,
        opts.instanceSlug ?? null,
        opts.settingsJson ?? null
      )
      .run()
  }

  async function seedRole(role: string, slug: string): Promise<void> {
    await db
      .prepare(
        `INSERT INTO product_roles (id, org_id, user_id, entity_id, product_slug, role)
         VALUES (?, ?, ?, ?, ?, ?)`
      )
      .bind(`pr-${role}-${slug}`, ORG_ID, USER_ID, ENTITY_ID, slug, role)
      .run()
  }

  it('bounces an unauthenticated request to sign-in and never calls Stripe', async () => {
    const res = await callRoute({ product_slug: 'hosted-agent' }, { signedIn: false })
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe('/auth/sign-in')
    expect(createBillingPortalSession).not.toHaveBeenCalled()
  })

  it('rejects a product slug outside the allowlist', async () => {
    const res = await callRoute({ product_slug: 'not-a-product' })
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe('/portal?cs=billing_invalid')
    expect(createBillingPortalSession).not.toHaveBeenCalled()
  })

  it('refuses a member who is not principal', async () => {
    await seedSubscription({
      slug: 'hosted-agent',
      settingsJson: JSON.stringify({ stripe_customer_id: 'cus_test_1' }),
    })
    await seedRole('member', 'hosted-agent')

    const res = await callRoute({ product_slug: 'hosted-agent' })
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe('/portal/products/hosted-agent?cs=billing_forbidden')
    expect(createBillingPortalSession).not.toHaveBeenCalled()
  })

  it('refuses when there is no subscription at all (no access resolves)', async () => {
    await seedRole('principal', 'hosted-agent')
    const res = await callRoute({ product_slug: 'hosted-agent' })
    expect(res.headers.get('Location')).toBe('/portal/products/hosted-agent?cs=billing_forbidden')
  })

  it('reports billing_unavailable when the subscription has no Stripe customer id', async () => {
    await seedSubscription({ slug: 'hosted-agent', settingsJson: null })
    await seedRole('principal', 'hosted-agent')

    const res = await callRoute({ product_slug: 'hosted-agent' })
    expect(res.headers.get('Location')).toBe('/portal/products/hosted-agent?cs=billing_unavailable')
    expect(createBillingPortalSession).not.toHaveBeenCalled()
  })

  it('happy path: principal + Stripe customer -> 303 to the Stripe portal session', async () => {
    await seedSubscription({
      slug: 'hosted-agent',
      settingsJson: JSON.stringify({ stripe_customer_id: 'cus_test_1' }),
    })
    await seedRole('principal', 'hosted-agent')

    const res = await callRoute({ product_slug: 'hosted-agent' })
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe('https://billing.stripe.com/p/session_test_123')
    expect(createBillingPortalSession).toHaveBeenCalledWith(
      'sk_test_fake',
      'cus_test_1',
      'https://portal.smd.services/portal/products/hosted-agent'
    )
  })

  it('operator with an instance returns to that instance settings page', async () => {
    await seedSubscription({
      slug: 'operator',
      instanceSlug: 'acme-firm',
      settingsJson: JSON.stringify({ stripe_customer_id: 'cus_op_1' }),
    })
    await seedRole('principal', 'operator')

    const res = await callRoute({ product_slug: 'operator', instance: 'acme-firm' })
    expect(res.status).toBe(303)
    expect(createBillingPortalSession).toHaveBeenCalledWith(
      'sk_test_fake',
      'cus_op_1',
      'https://portal.smd.services/portal/products/operator/acme-firm/settings'
    )
  })

  it('falls back to billing_error when Stripe throws, never a 500', async () => {
    await seedSubscription({
      slug: 'hosted-agent',
      settingsJson: JSON.stringify({ stripe_customer_id: 'cus_test_1' }),
    })
    await seedRole('principal', 'hosted-agent')
    createBillingPortalSession.mockRejectedValue(new Error('stripe down'))

    const res = await callRoute({ product_slug: 'hosted-agent' })
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toBe('/portal/products/hosted-agent?cs=billing_error')
  })
})
