/**
 * Tests for the governance matrix — the cell resolver
 * (src/lib/admin/governance.ts) and the ceiling-set endpoint
 * (POST /api/admin/operator/[customer]/governance). Design §5.3, ADR 0025/0035.
 *
 * The keystone assertion (ADR 0035 / foundations §8): an action class with no
 * authored ceiling resolves to `fail_closed` with a null effective value —
 * NEVER a presumed draft_for_review. The endpoint tests confirm a raise above a
 * vertical floor is rejected and an accepted change lands in config_change_audit
 * without mutating the live replica.
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { POST } from '../src/pages/api/admin/operator/[customer]/governance'
import { env as testEnv } from 'cloudflare:workers'
import { readGovernanceConfig, resolveCell, resolveSkillCells } from '../src/lib/admin/governance'
import { VERTICAL_FLOORS } from '../src/lib/operator/vertical-floors'
import type { ActionClass } from '../src/lib/operator/customer-yaml/types'
import { getCustomerConfig } from '../src/lib/portal/customer-config'
import type { AuthoredExposureActionClass } from '../src/lib/operator/customer-yaml/types'
import type {
  Ceiling,
  ChangeDirection,
  ConfigChangeOutcome,
  ConfigChangeType,
} from '../src/lib/portal/operator/config-governance'

interface ConfigChangeAuditRow {
  id: number
  created_at: string
  source: string
  actor_email: string
  change_type: ConfigChangeType
  persona_slug: string | null
  skill_name: string | null
  action_class: string | null
  old_value: string | null
  new_value: string | null
  outcome: ConfigChangeOutcome
  outcome_reason: string | null
  direction: ChangeDirection
}

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')

async function configChangeAudit(entityId: string): Promise<ConfigChangeAuditRow[]> {
  const result = await testEnv.DB.prepare(
    'SELECT id, created_at, source, actor_email, change_type, persona_slug, skill_name, ' +
      'action_class, old_value, new_value, outcome, outcome_reason, direction ' +
      'FROM config_change_audit WHERE entity_id = ? ORDER BY created_at DESC, id DESC LIMIT 50'
  )
    .bind(entityId)
    .all<ConfigChangeAuditRow>()
  return result.results ?? []
}
const ORG_ID = 'org-1'
const ENTITY_ID = 'ent-gov'
const SLUG = 'acme-law'

function exposure(
  overrides: Partial<Record<AuthoredExposureActionClass, Ceiling>> = {}
): Partial<Record<AuthoredExposureActionClass, Ceiling>> {
  return overrides
}

describe('resolveCell — the ADR-0035 keystone', () => {
  it('internal_write is governed by persona exposure when authored', () => {
    const cell = resolveCell(exposure({ internal_write: 'autonomous' }), 'internal_write', null)
    expect(cell.status).toBe('authored')
    expect(cell.effective).toBe('autonomous')
  })

  it('external_send with no exposure entry is unconfigured → fail-closed (NEVER draft)', () => {
    const cell = resolveCell(exposure(), 'external_send', null)
    expect(cell.status).toBe('fail_closed')
    expect(cell.authored).toBeNull()
    expect(cell.effective).toBeNull() // no invented value
  })

  it('an authored action ceiling resolves to that value', () => {
    const cell = resolveCell(exposure({ external_send: 'draft_for_review' }), 'external_send', null)
    expect(cell.status).toBe('authored')
    expect(cell.effective).toBe('draft_for_review')
  })

  it('law-firm authored autonomous is NOT floored (floor removed 2026-07, ADR 0073)', () => {
    // THE 2026-07 behavior change: outside-send is the firm's authored dial.
    const cell = resolveCell(exposure({ external_send: 'autonomous' }), 'external_send', 'law-firm')
    expect(cell.floor).toBeNull()
    expect(cell.effective).toBe('autonomous')
  })

  it('a declared vertical floor wins when more restrictive than the authored value (synthetic)', () => {
    const floors = VERTICAL_FLOORS as Record<string, Partial<Record<ActionClass, Ceiling>>>
    floors['floored-test-vertical'] = { external_send: 'draft_for_review' }
    try {
      const cell = resolveCell(
        exposure({ external_send: 'autonomous' }),
        'external_send',
        'floored-test-vertical'
      )
      expect(cell.floor).toBe('draft_for_review')
      expect(cell.effective).toBe('draft_for_review')
    } finally {
      delete floors['floored-test-vertical']
    }
  })

  it('resolveSkillCells covers all nine action classes', () => {
    const cells = resolveSkillCells(exposure({ internal_write: 'draft_for_review' }), null)
    expect(cells.map((c) => c.actionClass)).toEqual([
      'read',
      'internal_write',
      'external_send',
      'external_send_internal',
      'external_send_client',
      'external_send_vendor',
      'commitment',
      'destructive',
      'code_execution',
    ])
    // Read is always allowed by enforcement; only the authored class is also
    // configured. The rest are fail-closed.
    expect(cells.filter((c) => c.status === 'authored').map((c) => c.actionClass)).toEqual([
      'read',
      'internal_write',
    ])
  })
})

interface MinimalSession {
  userId: string
  orgId: string
  role: string
  email: string
  expiresAt: string
}

function adminSession(): MinimalSession {
  return {
    userId: 'usr-captain',
    orgId: ORG_ID,
    role: 'admin',
    email: 'captain@example.com',
    expiresAt: '2099-12-31T00:00:00Z',
  }
}

function buildCtx(opts: {
  session: MinimalSession | null
  slug: string
  form: Record<string, string>
}): Parameters<typeof POST>[0] {
  const request = new Request(`http://test.local/api/admin/operator/${opts.slug}/governance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(opts.form).toString(),
  })
  return {
    request,
    params: { customer: opts.slug },
    locals: { session: opts.session },
  } as unknown as Parameters<typeof POST>[0]
}

function locationOf(res: Response): string {
  return res.headers.get('Location') ?? ''
}

async function seedConfig(vertical: string | null): Promise<void> {
  const personas = [
    {
      slug: 'marcus',
      status: 'active',
      name: 'Marcus',
      entitlements: { exposure: { external_send: 'draft_for_review' } },
      skills: [
        {
          name: 'inbox-triage',
          enabled: true,
          initiation: { manual: true, scheduled: false, webhook: false },
        },
      ],
    },
  ]
  await testEnv.DB.prepare(
    `INSERT INTO customer_configs
       (entity_id, org_id, customer_slug, schema_version, personas_json, vertical, git_sha, synced_at)
     VALUES (?, ?, ?, '1.0.0', ?, ?, 'sha', '2026-06-08T00:00:00Z')`
  )
    .bind(ENTITY_ID, ORG_ID, SLUG, JSON.stringify(personas), vertical)
    .run()
}

describe('readGovernanceConfig', () => {
  beforeEach(async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES (?, 'Org', 'org', datetime('now'), datetime('now'))`
      )
      .bind(ORG_ID)
      .run()
    await db
      .prepare(`INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, 'Acme Law', ?)`)
      .bind(ENTITY_ID, ORG_ID, SLUG)
      .run()
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
  })

  it('returns not_found for an unknown slug', async () => {
    expect(await readGovernanceConfig(testEnv.DB, 'ghost')).toEqual({
      ok: false,
      error: 'not_found',
    })
  })

  it('parses persona exposure + skill initiation', async () => {
    await testEnv.DB.prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, vertical, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', ?, 'law-firm', 'sha', '2026-06-08T00:00:00Z')`
    )
      .bind(
        ENTITY_ID,
        ORG_ID,
        SLUG,
        JSON.stringify([
          {
            slug: 'marcus',
            status: 'active',
            name: 'Marcus',
            entitlements: { exposure: { external_send: 'draft_for_review' } },
            skills: [
              {
                name: 'inbox-triage',
                enabled: true,
                initiation: { manual: true, scheduled: false, webhook: true },
              },
            ],
          },
        ])
      )
      .run()
    const res = await readGovernanceConfig(testEnv.DB, SLUG)
    expect(res.ok).toBe(true)
    if (!res.ok) return
    expect(res.vertical).toBe('law-firm')
    expect(res.personas[0].exposure).toEqual({ external_send: 'draft_for_review' })
    expect(res.personas[0].skills[0].initiation).toEqual({
      manual: true,
      scheduled: false,
      webhook: true,
    })
  })

  it('returns malformed on bad JSON', async () => {
    await testEnv.DB.prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '{bad', 'sha', '2026-06-08T00:00:00Z')`
    )
      .bind(ENTITY_ID, ORG_ID, SLUG)
      .run()
    expect(await readGovernanceConfig(testEnv.DB, SLUG)).toEqual({ ok: false, error: 'malformed' })
  })
})

describe('POST /api/admin/operator/[customer]/governance', () => {
  beforeAll(() => {
    expect(discoverNumericMigrations(migrationsDir).length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES (?, 'Org', 'org', datetime('now'), datetime('now'))`
      )
      .bind(ORG_ID)
      .run()
    await db
      .prepare(`INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, 'Acme Law', ?)`)
      .bind(ENTITY_ID, ORG_ID, SLUG)
      .run()
    // config_change_audit.actor_user_id FKs to users(id) (migration 0047).
    await db
      .prepare(
        `INSERT INTO users (id, org_id, email, name, role, created_at)
         VALUES ('usr-captain', ?, 'captain@example.com', 'Captain', 'admin', datetime('now'))`
      )
      .bind(ORG_ID)
      .run()
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
  })

  it('rejects a non-admin session', async () => {
    const res = await POST(
      buildCtx({
        session: null,
        slug: SLUG,
        form: { skill_name: 'inbox-triage', action_class: 'external_send', level: 'refused' },
      })
    )
    expect(res.status).toBe(401)
  })

  it('accepts a law-firm external_send raise to autonomous (floor removed, ADR 0073)', async () => {
    // THE 2026-07 behavior change: outside-send is the firm's authored dial.
    await seedConfig('law-firm')
    const res = await POST(
      buildCtx({
        session: adminSession(),
        slug: SLUG,
        form: {
          persona_slug: 'marcus',
          skill_name: 'inbox-triage',
          action_class: 'external_send',
          level: 'autonomous',
        },
      })
    )
    expect(locationOf(res)).toContain('status=saved')
    const audit = await configChangeAudit(ENTITY_ID)
    expect(audit).toHaveLength(1)
    expect(audit[0].outcome).toBe('accepted')
    expect(audit[0].direction).toBe('raise')
  })

  it('blocks a raise above a declared vertical floor and records the rejection (synthetic)', async () => {
    // Machinery coverage: no production vertical declares a floor today, so a
    // synthetic one is injected for the duration of the test.
    const floors = VERTICAL_FLOORS as Record<string, Partial<Record<ActionClass, Ceiling>>>
    floors['floored-test-vertical'] = { external_send: 'draft_for_review' }
    try {
      await seedConfig('floored-test-vertical')
      const res = await POST(
        buildCtx({
          session: adminSession(),
          slug: SLUG,
          form: {
            persona_slug: 'marcus',
            skill_name: 'inbox-triage',
            action_class: 'external_send',
            level: 'autonomous',
          },
        })
      )
      expect(locationOf(res)).toContain('status=floor_blocked')
      // The rejected attempt is itself an audited compliance event.
      const audit = await configChangeAudit(ENTITY_ID)
      expect(audit).toHaveLength(1)
      expect(audit[0].outcome).toBe('rejected_floor')
    } finally {
      delete floors['floored-test-vertical']
    }
  })

  it('accepts an in-floor exposure change, records it, and does NOT mutate the replica', async () => {
    await seedConfig('law-firm')
    const res = await POST(
      buildCtx({
        session: adminSession(),
        slug: SLUG,
        form: {
          persona_slug: 'marcus',
          skill_name: 'inbox-triage',
          action_class: 'external_send',
          level: 'draft_for_review',
        },
      })
    )
    expect(locationOf(res)).toContain('status=saved')
    const audit = await configChangeAudit(ENTITY_ID)
    expect(audit[0].outcome).toBe('accepted')
    expect(audit[0].change_type).toBe('entitlement_exposure')
    expect(audit[0].new_value).toBe('draft_for_review')

    // Live replica untouched — the authored exposure stays at its projected value.
    const config = await getCustomerConfig(testEnv.DB, ENTITY_ID)
    expect(config?.personas[0].entitlements.exposure.external_send).toBe('draft_for_review')
  })

  it('404s when the skill does not exist', async () => {
    await seedConfig(null)
    const res = await POST(
      buildCtx({
        session: adminSession(),
        slug: SLUG,
        form: {
          persona_slug: 'marcus',
          skill_name: 'ghost-skill',
          action_class: 'external_send',
          level: 'refused',
        },
      })
    )
    expect(locationOf(res)).toContain('status=not_found')
  })
})
