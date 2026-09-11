/**
 * Behavioral coverage for the SOW data-shaping layer (2026-08-14 code
 * review, Testing #3): the props the signed contract PDF is rendered
 * from, exercised through the real generate-pdf route against a real
 * migrated D1. Forme WASM cannot load under vitest (tests/sow-render.test.ts
 * stays skipped for the byte-level render), so the render seam
 * (createSOWRevisionForQuote) is mocked to CAPTURE the assembled
 * SOWTemplateProps — everything upstream of the WASM boundary runs real:
 * payment-schedule selection (40-hour milestone threshold, 40/30/30 vs
 * deposit-pct two-part), currency formatting, deliverables-over-line-items
 * preference, and the fabrication gates (no contact name / no authored
 * overview -> refuse to render; the pre-#378 'Business Owner' fallback
 * must never come back).
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
import { createEntity } from '../src/lib/db/entities'
import { createQuote, updateQuote } from '../src/lib/db/quotes'
import type { SOWTemplateProps } from '../src/lib/pdf/sow-template'

const createSOWRevisionForQuote = vi.fn()
vi.mock('../src/lib/sow/service', () => ({
  createSOWRevisionForQuote: (args: unknown) => createSOWRevisionForQuote(args),
}))

// Import AFTER the mock so the route binds the mocked module.
import { POST } from '../src/pages/api/admin/quotes/[id]'

const migrationsDir = resolve(process.cwd(), 'migrations')
const ASSESSMENT_ID = 'assess-sow-props'

function adminLocals(): App.Locals {
  return {
    session: {
      userId: 'u-admin-sow',
      orgId: ORG_ID,
      role: 'admin',
      email: 'admin@example.com',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    },
  } as unknown as App.Locals
}

async function generatePdf(quoteId: string): Promise<Response> {
  const form = new FormData()
  form.set('action', 'generate-pdf')
  const request = new Request('https://admin.smd.services/api/admin/quotes/x', {
    method: 'POST',
    body: form,
  })
  return POST({
    request,
    locals: adminLocals(),
    params: { id: quoteId },
    redirect: (path: string, status?: number) =>
      new Response(null, { status: status ?? 302, headers: { Location: path } }),
  } as unknown as Parameters<typeof POST>[0])
}

function capturedProps(): SOWTemplateProps {
  expect(createSOWRevisionForQuote).toHaveBeenCalledTimes(1)
  const args = createSOWRevisionForQuote.mock.calls[0][0] as {
    templateProps: SOWTemplateProps
  }
  return args.templateProps
}

describe('SOW template props (generate-pdf, real D1 upstream of WASM)', () => {
  let db: D1Database
  let entityId: string

  beforeEach(async () => {
    createSOWRevisionForQuote.mockReset()
    createSOWRevisionForQuote.mockResolvedValue({ id: 'rev-1' })

    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db, STORAGE: {} })

    await db
      .prepare('INSERT OR IGNORE INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'SMD Services', 'smd-services')
      .run()
    const entity = await createEntity(db, ORG_ID, {
      name: 'Sonoran Comfort HVAC',
      stage: 'proposing',
    })
    entityId = entity.id
    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status) VALUES (?, ?, ?, 'completed')`
      )
      .bind(ASSESSMENT_ID, ORG_ID, entityId)
      .run()
  })

  async function seedContact(name = 'Reyna Alvarez', title: string | null = 'Owner') {
    await db
      .prepare(`INSERT INTO contacts (id, org_id, entity_id, name, title) VALUES (?, ?, ?, ?, ?)`)
      .bind('contact-sow', ORG_ID, entityId, name, title)
      .run()
  }

  async function seedQuote(opts: {
    hours: number
    rate?: number
    depositPct?: number
    overview?: string | null
    deliverables?: { title: string; body: string }[] | null
    milestoneLabel?: string
  }): Promise<string> {
    const q = await createQuote(db, ORG_ID, {
      entityId,
      assessmentId: ASSESSMENT_ID,
      lineItems: [
        {
          problem: 'Dispatch board',
          description: 'Rebuild scheduling flow',
          estimated_hours: opts.hours,
        },
      ],
      rate: opts.rate ?? 175,
      depositPct: opts.depositPct,
      originatingSignalId: null,
    })
    await updateQuote(db, ORG_ID, q.id, {
      engagementOverview:
        opts.overview === undefined ? 'Dispatch redesign scoped in assessment.' : opts.overview,
      ...(opts.deliverables !== undefined ? { deliverables: opts.deliverables } : {}),
      ...(opts.milestoneLabel !== undefined ? { milestoneLabel: opts.milestoneLabel } : {}),
    })
    return q.id
  }

  it('two-part schedule below 40 hours: deposit/completion split by the quote deposit_pct', async () => {
    await seedContact()
    const quoteId = await seedQuote({ hours: 30, rate: 175, depositPct: 0.5 })

    const res = await generatePdf(quoteId)
    expect(res.headers.get('Location')).toContain('saved=1')

    const props = capturedProps()
    expect(props.payment.schedule).toBe('two_part')
    expect(props.payment.totalPrice).toBe('$5,250')
    expect(props.payment.deposit).toBe('$2,625')
    expect(props.payment.completion).toBe('$2,625')
  })

  it('three-milestone schedule at >= 40 hours: 40/30/30 split with authored milestone label', async () => {
    await seedContact()
    const quoteId = await seedQuote({
      hours: 40,
      rate: 200, // total $8,000
      milestoneLabel: 'pilot rollout',
    })

    await generatePdf(quoteId)

    const props = capturedProps()
    expect(props.payment.schedule).toBe('three_milestone')
    expect(props.payment.totalPrice).toBe('$8,000')
    expect(props.payment.deposit).toBe('$3,200') // 40%
    expect(props.payment).toMatchObject({ milestone: '$2,400', completion: '$2,400' }) // 30/30
    expect(props.payment.milestoneLabel).toBe('pilot rollout')
  })

  it('items prefer authored deliverables over raw line items', async () => {
    await seedContact()
    const quoteId = await seedQuote({
      hours: 10,
      deliverables: [{ title: 'Dispatch SOP', body: 'A documented dispatch procedure.' }],
    })

    await generatePdf(quoteId)

    expect(capturedProps().items).toEqual([
      { name: 'Dispatch SOP', description: 'A documented dispatch procedure.' },
    ])
  })

  it('falls back to line items only when no deliverables are authored', async () => {
    await seedContact()
    const quoteId = await seedQuote({ hours: 10 })

    await generatePdf(quoteId)

    expect(capturedProps().items).toEqual([
      { name: 'Dispatch board', description: 'Rebuild scheduling flow' },
    ])
  })

  it('client block carries the real contact — never a fabricated placeholder', async () => {
    await seedContact('Reyna Alvarez', 'Owner')
    const quoteId = await seedQuote({ hours: 10 })

    await generatePdf(quoteId)

    const props = capturedProps()
    expect(props.client.businessName).toBe('Sonoran Comfort HVAC')
    expect(props.client.contactName).toBe('Reyna Alvarez')
    expect(props.client.contactTitle).toBe('Owner')
    expect(JSON.stringify(props)).not.toContain('Business Owner') // pre-#378 Pattern B fallback
  })

  it('refuses to render without a named primary contact (no fabricated signer)', async () => {
    // No contact seeded.
    const quoteId = await seedQuote({ hours: 10 })

    const res = await generatePdf(quoteId)
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toContain('add%20a%20primary%20contact')
    expect(createSOWRevisionForQuote).not.toHaveBeenCalled()
  })

  it('refuses to render without an authored engagement overview (no borrowed copy)', async () => {
    await seedContact()
    const quoteId = await seedQuote({ hours: 10, overview: null })

    const res = await generatePdf(quoteId)
    expect(res.status).toBe(302)
    expect(res.headers.get('Location')).toContain('author%20the%20engagement%20overview')
    expect(createSOWRevisionForQuote).not.toHaveBeenCalled()
  })
})
