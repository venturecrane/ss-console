import { describe, it, expect } from 'vitest'
import { deriveOfferings } from '../src/lib/portal/offerings'
import { buildPortalNav, isNavDestinationActive } from '../src/lib/portal/nav'
import type { Engagement } from '../src/lib/db/engagements'
import type { Quote } from '../src/lib/db/quotes'
import type { SubscriptionRow } from '../src/lib/portal/product-access'

function engagement(status: string, id = `eng-${status}`): Engagement {
  return { id, status } as unknown as Engagement
}
function quote(status: string, id = `q-${status}`): Quote {
  return { id, status } as unknown as Quote
}
function subscription(slug: string, status = 'active'): SubscriptionRow {
  // Operator subs carry an instance_slug (the instance identity); for the tests
  // the slug equals the product slug so it humanizes back to "Operator".
  return {
    id: `sub-${slug}`,
    product_slug: slug,
    instance_slug: slug === 'operator' ? 'operator' : null,
    status,
    service_id: null,
  } as unknown as SubscriptionRow
}

const NOTHING = {
  engagements: [],
  quotes: [],
  subscriptions: [],
  operatorConfigs: [],
  hasInvoices: false,
}

describe('deriveOfferings', () => {
  it('nothing owned: no engagement presence, no products, no invoices', () => {
    const o = deriveOfferings(NOTHING)
    expect(o.engagement.present).toBe(false)
    expect(o.operators).toEqual([])
    expect(o.selfServe).toBeNull()
    expect(o.hasInvoices).toBe(false)
  })

  it('proposal-only: present with an open proposal and no active engagement', () => {
    const o = deriveOfferings({ ...NOTHING, quotes: [quote('sent')] })
    expect(o.engagement.present).toBe(true)
    expect(o.engagement.openProposal?.id).toBe('q-sent')
    expect(o.engagement.activeEngagement).toBeNull()
  })

  it('active engagement without proposals', () => {
    const o = deriveOfferings({ ...NOTHING, engagements: [engagement('in_progress')] })
    expect(o.engagement.activeEngagement?.id).toBe('eng-in_progress')
    expect(o.engagement.openProposal).toBeNull()
    expect(o.engagement.pastEngagements).toHaveLength(0)
  })

  it('active engagement AND open follow-on proposal are orthogonal (both set)', () => {
    const o = deriveOfferings({
      ...NOTHING,
      engagements: [engagement('in_progress')],
      quotes: [quote('sent')],
    })
    expect(o.engagement.activeEngagement).not.toBeNull()
    expect(o.engagement.openProposal).not.toBeNull()
  })

  it('past-only: completed engagements keep the destination present', () => {
    const o = deriveOfferings({
      ...NOTHING,
      engagements: [engagement('completed'), engagement('cancelled', 'eng-2')],
    })
    expect(o.engagement.present).toBe(true)
    expect(o.engagement.activeEngagement).toBeNull()
    expect(o.engagement.pastEngagements).toHaveLength(2)
  })

  it('signed/declined quotes do not create an open proposal but keep presence', () => {
    const o = deriveOfferings({ ...NOTHING, quotes: [quote('accepted'), quote('declined', 'q2')] })
    expect(o.engagement.present).toBe(true)
    expect(o.engagement.openProposal).toBeNull()
  })

  it('products-only: subscriptions resolve by slug, engagement absent', () => {
    const o = deriveOfferings({
      ...NOTHING,
      subscriptions: [subscription('hosted-agent', 'provisioning'), subscription('operator')],
    })
    expect(o.engagement.present).toBe(false)
    expect(o.operators).toHaveLength(1)
    expect(o.operators[0].slug).toBe('operator')
    expect(o.selfServe?.status).toBe('provisioning')
  })

  it('multi-operator: one entry per operator subscription, addressed by instance_slug', () => {
    const smd = {
      id: 'sub-smd',
      product_slug: 'operator',
      instance_slug: 'smd',
      status: 'active',
      service_id: null,
    } as unknown as SubscriptionRow
    const pilot = {
      id: 'sub-pilot',
      product_slug: 'operator',
      instance_slug: 'pilot-smokeball',
      status: 'active',
      service_id: null,
    } as unknown as SubscriptionRow
    const o = deriveOfferings({
      ...NOTHING,
      subscriptions: [smd, pilot],
      operatorConfigs: [{ customer_slug: 'smd', displayName: 'Crane' }],
    })
    expect(o.operators.map((op) => op.slug)).toEqual(['smd', 'pilot-smokeball'])
    // displayName from config when present; humanized slug as the honest fallback.
    expect(o.operators[0].displayName).toBe('Crane')
    expect(o.operators[1].displayName).toBe('Pilot Smokeball')
  })
})

describe('buildPortalNav', () => {
  it('nothing owned: Home only', () => {
    const nav = buildPortalNav(deriveOfferings(NOTHING))
    expect(nav.map((d) => d.label)).toEqual(['Home'])
  })

  it('everything owned: four destinations in fixed order, one Operator tab for both Operators (ADR 0100)', () => {
    const nav = buildPortalNav(
      deriveOfferings({
        engagements: [engagement('in_progress')],
        quotes: [],
        subscriptions: [subscription('operator'), subscription('hosted-agent')],
        operatorConfigs: [],
        hasInvoices: true,
      })
    )
    expect(nav.map((d) => d.label)).toEqual(['Home', 'Engagement', 'Operator', 'Billing'])
  })

  it('self-serve-only subscriber: Home, Operator, Billing (one product; subscription implies billing)', () => {
    const nav = buildPortalNav(
      deriveOfferings({ ...NOTHING, subscriptions: [subscription('hosted-agent')] })
    )
    expect(nav.map((d) => d.label)).toEqual(['Home', 'Operator', 'Billing'])
    // The Operator tab owns the setup pages: they light it, nothing else.
    const operatorTab = nav.find((d) => d.label === 'Operator')!
    expect(operatorTab.href).toBe('/portal/products/operator')
    expect(isNavDestinationActive(operatorTab, '/portal/products/operator/setup/intake')).toBe(true)
  })

  it('invoices alone light up Billing', () => {
    const nav = buildPortalNav(deriveOfferings({ ...NOTHING, hasInvoices: true }))
    expect(nav.map((d) => d.label)).toEqual(['Home', 'Billing'])
  })

  it('a provisioning-only operator: no Billing, and Home is hidden (pre-go-live lands on the operator)', () => {
    const o = deriveOfferings({
      ...NOTHING,
      subscriptions: [subscription('operator', 'provisioning')],
    })
    expect(o.hasBillingRelationship).toBe(false)
    expect(o.preGoLiveLanding).toBe('/portal/products/operator/operator')
    const nav = buildPortalNav(o)
    expect(nav.map((d) => d.label)).toEqual(['Operator'])
  })

  it('prior invoices keep Billing visible even while an operator is provisioning', () => {
    const o = deriveOfferings({
      ...NOTHING,
      subscriptions: [subscription('operator', 'provisioning')],
      hasInvoices: true,
    })
    expect(o.hasBillingRelationship).toBe(true)
    expect(buildPortalNav(o).map((d) => d.label)).toEqual(['Home', 'Operator', 'Billing'])
  })

  it('go-live (active) establishes the billing relationship', () => {
    const o = deriveOfferings({ ...NOTHING, subscriptions: [subscription('operator', 'active')] })
    expect(o.hasBillingRelationship).toBe(true)
  })

  it('MANY operators still produce exactly ONE Operator tab (no per-instance tabs)', () => {
    const op = (slug: string) =>
      ({
        id: `sub-${slug}`,
        product_slug: 'operator',
        instance_slug: slug,
        status: 'active',
        service_id: null,
      }) as unknown as SubscriptionRow
    const nav = buildPortalNav(
      deriveOfferings({
        ...NOTHING,
        subscriptions: [op('smd'), op('pilot-smokeball'), op('third')],
        operatorConfigs: [],
      })
    )
    const operatorTabs = nav.filter((d) => d.label === 'Operator')
    expect(operatorTabs).toHaveLength(1)
    expect(operatorTabs[0].href).toBe('/portal/products/operator')
    // The whole nav stays the small category set regardless of operator count.
    expect(nav.map((d) => d.label)).toEqual(['Home', 'Operator', 'Billing'])
  })
})

describe('preGoLiveLanding (pre-go-live operator shortcut)', () => {
  const op = (slug: string, status = 'provisioning') =>
    ({
      id: `sub-${slug}`,
      product_slug: 'operator',
      instance_slug: slug,
      status,
      service_id: null,
    }) as unknown as SubscriptionRow

  it('single provisioning operator, nothing else → deep-links the instance', () => {
    const o = deriveOfferings({ ...NOTHING, subscriptions: [op('ashton-price')] })
    expect(o.preGoLiveLanding).toBe('/portal/products/operator/ashton-price')
  })

  it('many provisioning operators → the operator list (pick one)', () => {
    const o = deriveOfferings({ ...NOTHING, subscriptions: [op('ashton-price'), op('second-op')] })
    expect(o.preGoLiveLanding).toBe('/portal/products/operator')
    // Home is hidden; the single Operator tab remains.
    expect(buildPortalNav(o).map((d) => d.label)).toEqual(['Operator'])
  })

  it('go-live of ANY operator dissolves it → Home and Billing return', () => {
    const o = deriveOfferings({ ...NOTHING, subscriptions: [op('a', 'active'), op('b')] })
    expect(o.hasBillingRelationship).toBe(true)
    expect(o.preGoLiveLanding).toBeNull()
    expect(buildPortalNav(o).map((d) => d.label)).toEqual(['Home', 'Operator', 'Billing'])
  })

  it('an engagement suppresses it (Home has content to show)', () => {
    const o = deriveOfferings({
      ...NOTHING,
      engagements: [engagement('in_progress')],
      subscriptions: [op('ashton-price')],
    })
    expect(o.preGoLiveLanding).toBeNull()
  })

  it('prior invoices suppress it (a billing relationship already exists)', () => {
    const o = deriveOfferings({
      ...NOTHING,
      subscriptions: [op('ashton-price')],
      hasInvoices: true,
    })
    expect(o.preGoLiveLanding).toBeNull()
  })

  it('no operators → null (nothing-owned shows the welcome Home; hosted-agent never pre-go-live)', () => {
    expect(deriveOfferings(NOTHING).preGoLiveLanding).toBeNull()
    const ha = deriveOfferings({
      ...NOTHING,
      subscriptions: [subscription('hosted-agent', 'provisioning')],
    })
    expect(ha.preGoLiveLanding).toBeNull()
  })
})

describe('isNavDestinationActive', () => {
  const nav = buildPortalNav(
    deriveOfferings({
      engagements: [engagement('in_progress')],
      quotes: [],
      subscriptions: [subscription('operator')],
      operatorConfigs: [],
      hasInvoices: true,
    })
  )
  const byLabel = (label: string) => nav.find((d) => d.label === label)!

  it('Home is active only on the exact root', () => {
    expect(isNavDestinationActive(byLabel('Home'), '/portal')).toBe(true)
    expect(isNavDestinationActive(byLabel('Home'), '/portal/')).toBe(true)
    expect(isNavDestinationActive(byLabel('Home'), '/portal/engagement')).toBe(false)
  })

  it('destinations match self and descendants', () => {
    expect(isNavDestinationActive(byLabel('Engagement'), '/portal/engagement')).toBe(true)
    expect(isNavDestinationActive(byLabel('Engagement'), '/portal/engagement/proposals/abc')).toBe(
      true
    )
    expect(isNavDestinationActive(byLabel('Operator'), '/portal/engagement')).toBe(false)
  })
})
