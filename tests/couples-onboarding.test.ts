/**
 * Couples onboarding (ADR 0090 decision 4, ADR 0091 s5 and R7), against a real
 * D1 schema (all migrations applied).
 *
 * Each block carries a FALSIFIER: a case that passes only because the rule it
 * names exists.
 *
 *   - A partner claims only the row SMD authored for their verified email, and
 *     the partner role admits them to the couples surfaces and to nothing the
 *     single-person SKU gates on `principal`.
 *   - A partner never sees the other partner's private answers (R7): the view
 *     every couples page renders from carries none of them.
 *   - A partner's form writes only their own row.
 *   - The joint section is not final with one confirmation; an edit re-opens
 *     it; a confirmation of a version the partner was not shown is refused.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'
import {
  claimPartnerByEmail,
  createCouplesInstance,
  getCouplesInstance,
  listPeople,
  recordPersonIntake,
  type CouplesPersonRow,
} from '../src/lib/db/couples-onboarding'
import { confirmJointSettings, getJointState, saveJointSettings } from '../src/lib/db/couples-joint'
import { listHostedAgentQueue } from '../src/lib/db/hosted-agent-intake'
import { resolveCouplesAccess } from '../src/lib/portal/couples-access'
import { resolveHostedAgentAccess } from '../src/lib/portal/hosted-agent-access'
import { loadCouplesView } from '../src/lib/portal/couples-view'
import { parseJointSettings } from '../src/lib/hosted-agent/couples/joint-settings'
import { JOINT } from './fixtures/couples'
import { POST as intakePost } from '../src/pages/api/portal/products/hosted-agent/couples/intake'
import { POST as jointPost } from '../src/pages/api/portal/products/hosted-agent/couples/joint'

const CLERK_ORG = 'org_couple'

let db: D1Database

async function seedInstance(): Promise<{
  intakeId: string
  p1: CouplesPersonRow
  p2: CouplesPersonRow
}> {
  const intakeId = await createCouplesInstance(db, {
    coupleName: 'Alex and Sam',
    agentName: 'Juniper',
    partners: [
      { name: 'Alex Example', email: 'Alex@Example.com' },
      { name: 'Sam Example', email: 'sam@example.com' },
    ],
  })
  const inst = await getCouplesInstance(db, intakeId)
  await db
    .prepare('UPDATE entities SET clerk_org_id = ? WHERE id = ?')
    .bind(CLERK_ORG, inst!.entity_id)
    .run()
  const [p1, p2] = await listPeople(db, intakeId)
  return { intakeId, p1: p1, p2: p2 }
}

/** Astro locals for a Clerk session whose verified primary email is `email`. */
function localsFor(clerkUserId: string, email: string, verified = true): App.Locals {
  return {
    auth: () => ({ userId: clerkUserId, orgId: CLERK_ORG, sessionId: `sess_${clerkUserId}` }),
    currentUser: async () => ({
      primaryEmailAddress: {
        emailAddress: email,
        verification: { status: verified ? 'verified' : 'unverified' },
      },
      firstName: 'Test',
      lastName: 'User',
    }),
    session: null,
  } as unknown as App.Locals
}

async function allowed(locals: App.Locals) {
  const access = await resolveCouplesAccess(db, locals)
  if (access.kind !== 'allowed') throw new Error(`expected allowed, got redirect ${access.to}`)
  return access
}

function form(fields: Record<string, string | string[]>): FormData {
  const f = new FormData()
  for (const [k, v] of Object.entries(fields)) {
    for (const item of Array.isArray(v) ? v : [v]) f.append(k, item)
  }
  return f
}

function ctx(locals: App.Locals, body: FormData) {
  const request = new Request('http://test.local/x', { method: 'POST', body })
  return { locals, request, params: {} } as unknown as Parameters<typeof intakePost>[0]
}

beforeEach(async () => {
  db = createTestD1()
  await runMigrations(db, {
    files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
  })
  Object.assign(testEnv, { DB: db })
})

describe('instance creation', () => {
  it('creates an SMD-paid provisioning subscription, a couples intake and two partner rows', async () => {
    const { intakeId, p1, p2 } = await seedInstance()
    const inst = await getCouplesInstance(db, intakeId)
    expect(inst?.agent_name).toBe('Juniper')
    const sub = await db
      .prepare(
        'SELECT status, stripe_subscription_id, settings_json FROM subscriptions WHERE id = ?'
      )
      .bind(inst!.subscription_id)
      .first<{ status: string; stripe_subscription_id: string | null; settings_json: string }>()
    expect(sub?.status).toBe('provisioning')
    expect(sub?.stripe_subscription_id).toBeNull()
    expect(JSON.parse(sub!.settings_json)).toEqual({ plan: 'smd-paid', role: 'couples' })
    expect([p1.person_key, p2.person_key]).toEqual(['p1', 'p2'])
    expect(p1.email).toBe('alex@example.com') // normalized
  })

  it('stays out of the single-person concierge queue', async () => {
    await seedInstance()
    expect(await listHostedAgentQueue(db)).toEqual([])
  })
})

describe('partner access (widened gate, not principal-only)', () => {
  it('each partner claims their own row by verified email and is admitted', async () => {
    const { p1, p2 } = await seedInstance()
    const a = await allowed(localsFor('clerk_a', 'alex@example.com'))
    const b = await allowed(localsFor('clerk_b', 'sam@example.com'))
    expect(a.person.id).toBe(p1.id)
    expect(b.person.id).toBe(p2.id)
    const roles = await db
      .prepare(`SELECT role FROM product_roles WHERE product_slug = 'hosted-agent'`)
      .all<{ role: string }>()
    expect(roles.results.map((r) => r.role)).toEqual(['partner', 'partner'])
  })

  it('FALSIFIER: a signed-in org member whose email SMD did not author is refused', async () => {
    await seedInstance()
    const access = await resolveCouplesAccess(db, localsFor('clerk_x', 'stranger@example.com'))
    expect(access).toEqual({ kind: 'redirect', to: '/auth/sign-in?status=no_subscription' })
  })

  it('FALSIFIER: an unverified primary email never claims a partner row', async () => {
    await seedInstance()
    const access = await resolveCouplesAccess(db, localsFor('clerk_a', 'alex@example.com', false))
    expect(access.kind).toBe('redirect')
    const people = await db
      .prepare('SELECT COUNT(*) AS n FROM hosted_agent_people WHERE user_id IS NOT NULL')
      .first<{ n: number }>()
    expect(people?.n).toBe(0)
  })

  it('a claimed row cannot be claimed by a second account with the same email', async () => {
    const { intakeId } = await seedInstance()
    await allowed(localsFor('clerk_a', 'alex@example.com'))
    const inst = await getCouplesInstance(db, intakeId)
    const other = await claimPartnerByEmail(
      db,
      intakeId,
      { id: 'some-other-user', email: 'alex@example.com' },
      inst!.entity_id
    )
    expect(other).toBeNull()
  })

  it('FALSIFIER: the partner role does not open the principal-only Hosted Agent surfaces', async () => {
    await seedInstance()
    const locals = localsFor('clerk_a', 'alex@example.com')
    await allowed(locals)
    const single = await resolveHostedAgentAccess(db, locals, { allowedRoles: ['principal'] })
    expect(single.kind).toBe('redirect')
  })
})

describe('R7: a partner sees their own items and shared items only', () => {
  it("the view carries none of the other partner's private answers", async () => {
    const { intakeId, p2 } = await seedInstance()
    // Make B's private answers distinctive, so their absence is meaningful.
    await recordPersonIntake(db, p2.id, { name: 'Sam Example', consent: true })
    await db
      .prepare(`UPDATE hosted_agent_people SET telegram_id = '987654321' WHERE id = ?`)
      .bind(p2.id)
      .run()
    const a = await allowed(localsFor('clerk_a', 'alex@example.com'))
    const view = await loadCouplesView(db, a.instance, a.person)
    const serialized = JSON.stringify(view)
    expect(serialized).not.toContain('sam@example.com')
    expect(serialized).not.toContain('987654321')
    const b = await db
      .prepare('SELECT consent_at FROM hosted_agent_people WHERE id = ?')
      .bind(p2.id)
      .first<{ consent_at: string }>()
    expect(serialized).not.toContain(b!.consent_at)
    // What A may see of B: the name.
    expect(view.partner).toEqual({ personKey: 'p2', name: 'Sam Example', confirmedCurrent: false })
    expect(view.me.email).toBe('alex@example.com')
    void intakeId
  })

  it("FALSIFIER: a partner's form writes their own row, never the other's", async () => {
    const { p2 } = await seedInstance()
    const locals = localsFor('clerk_a', 'alex@example.com')
    const res = await intakePost(
      ctx(locals, form({ name: 'Alex E.', consent: '1', person_id: p2.id }))
    )
    expect(res.status).toBe(303)
    const people = await db
      .prepare('SELECT person_key, name, consent_at FROM hosted_agent_people ORDER BY person_key')
      .all<{ person_key: string; name: string; consent_at: string | null }>()
    expect(people.results[0]).toMatchObject({ person_key: 'p1', name: 'Alex E.' })
    expect(people.results[0].consent_at).not.toBeNull()
    expect(people.results[1]).toMatchObject({
      person_key: 'p2',
      name: 'Sam Example',
      consent_at: null,
    })
  })

  it('consent is required and recorded with its time and wording version', async () => {
    await seedInstance()
    const locals = localsFor('clerk_a', 'alex@example.com')
    const refused = await intakePost(ctx(locals, form({ name: 'Alex' })))
    expect(refused.headers.get('Location')).toContain('st=consent_required')
    await intakePost(ctx(locals, form({ name: 'Alex', consent: '1' })))
    const row = await db
      .prepare(
        `SELECT consent_at, consent_version FROM hosted_agent_people WHERE person_key = 'p1'`
      )
      .first<{ consent_at: string | null; consent_version: string | null }>()
    expect(row?.consent_at).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
    expect(row?.consent_version).toBe('couples-2026-10-17')
  })
})

describe('joint section: final only when both partners confirm', () => {
  it('FALSIFIER: one confirmation is not final; the second makes it final', async () => {
    const { intakeId, p1, p2 } = await seedInstance()
    const v1 = await saveJointSettings(db, intakeId, p1.id, JOINT)
    let state = await getJointState(db, intakeId)
    expect(state?.confirmedBy).toEqual([p1.id])
    expect(state?.final).toBe(false)
    expect(await confirmJointSettings(db, intakeId, p2.id, v1)).toBe('confirmed')
    state = await getJointState(db, intakeId)
    expect(state?.final).toBe(true)
  })

  it('FALSIFIER: an edit by one partner re-opens the section for the other', async () => {
    const { intakeId, p1, p2 } = await seedInstance()
    const v1 = await saveJointSettings(db, intakeId, p1.id, JOINT)
    await confirmJointSettings(db, intakeId, p2.id, v1)
    const v2 = await saveJointSettings(db, intakeId, p2.id, {
      ...JOINT,
      checkin_topics: ['money'],
    })
    const state = await getJointState(db, intakeId)
    expect(state?.version).toBe(v2)
    expect(state?.confirmedBy).toEqual([p2.id])
    expect(state?.final).toBe(false)
  })

  it('FALSIFIER: confirming a version the partner was not shown is refused', async () => {
    const { intakeId, p1, p2 } = await seedInstance()
    const v1 = await saveJointSettings(db, intakeId, p1.id, JOINT)
    await saveJointSettings(db, intakeId, p1.id, { ...JOINT, timezone: 'America/Denver' })
    expect(await confirmJointSettings(db, intakeId, p2.id, v1)).toBe('stale')
    expect((await getJointState(db, intakeId))?.final).toBe(false)
  })

  it('the portal refuses joint actions before consent, and confirms through the session', async () => {
    const { intakeId } = await seedInstance()
    const a = localsFor('clerk_a', 'alex@example.com')
    const b = localsFor('clerk_b', 'sam@example.com')
    const early = await jointPost(ctx(a, form({ action: 'save' })))
    expect(early.headers.get('Location')).toContain('st=consent_first')
    await intakePost(ctx(a, form({ name: 'Alex', consent: '1' })))
    await intakePost(ctx(b, form({ name: 'Sam', consent: '1' })))
    const saved = await jointPost(
      ctx(
        a,
        form({
          action: 'save',
          timezone: 'America/Phoenix',
          checkin_topics: 'money\ntime together',
          checkin_p1_days: ['mon'],
          checkin_p1_time: '18:00',
          checkin_p2_days: ['tue', 'mon'],
          checkin_p2_time: '18:30',
          gratitude_p1_days: ['wed'],
          gratitude_p1_time: '20:00',
          gratitude_p2_days: ['wed'],
          gratitude_p2_time: '20:00',
          daily_reminder_time: '08:00',
          weekly_checkin_day: 'sun',
          weekly_checkin_time: '19:00',
          quiet_start: '21:00',
          quiet_end: '08:00',
        })
      )
    )
    expect(saved.headers.get('Location')).toContain('st=saved')
    const state = await getJointState(db, intakeId)
    expect(state?.settings.checkin.p2.days).toEqual(['mon', 'tue'])
    expect(state?.final).toBe(false)
    const confirmed = await jointPost(
      ctx(b, form({ action: 'confirm', version: String(state!.version) }))
    )
    expect(confirmed.headers.get('Location')).toContain('st=confirmed')
    expect((await getJointState(db, intakeId))?.final).toBe(true)
  })
})

describe('joint settings parsing', () => {
  it('accepts the canonical shape', () => {
    expect(parseJointSettings(JOINT)).toEqual(JOINT)
  })
  it.each([
    ['bad timezone', { ...JOINT, timezone: 'Mars/Olympus' }],
    ['no topics', { ...JOINT, checkin_topics: [] }],
    ['topic with a separator', { ...JOINT, checkin_topics: ['a; b'] }],
    ['bad time', { ...JOINT, daily_reminder_time: '25:00' }],
    ['no days', { ...JOINT, checkin: { ...JOINT.checkin, p1: { days: [], time: '18:00' } } }],
    ['equal quiet bounds', { ...JOINT, quiet_hours: { start: '21:00', end: '21:00' } }],
  ])('rejects %s', (_label, value) => {
    expect(parseJointSettings(value)).toBeNull()
  })
})
