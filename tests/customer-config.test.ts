/**
 * Tests for the customer_configs projection helpers
 * (src/lib/portal/customer-config.ts).
 *
 * Per ADR 0012, customer_configs is a read replica of `customer.yaml` (which
 * lives in a canonical git repo). The helpers under test parse projected JSON
 * columns into typed shapes.
 *
 * Each test seeds a customer_configs row directly via the test D1 — emulating
 * what CI will do post-merge, since the CI sync path lands in a follow-on
 * PR. The schema constraints (FK to entities, UNIQUE customer_slug, NOT NULL
 * on personas_json) are exercised through real seeds.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { ORG_ID } from '../src/lib/constants'
import {
  getCustomerConfig,
  getCustomerConfigBySlug,
  listCustomerConfigsForEntity,
  listCustomerConfigHistory,
  parseMcpConnector,
  type PersonaConfig,
} from '../src/lib/portal/customer-config'

const migrationsDir = resolve(process.cwd(), 'migrations')

interface SyncHistorySeed {
  customer_slug: string
  git_sha: string
  synced_at: string
  synced_by: string
  actor?: string | null
  prev_git_sha?: string | null
  r2_shadow_key?: string | null
}

async function seedSyncHistory(db: D1Database, opts: SyncHistorySeed): Promise<void> {
  await db
    .prepare(
      'INSERT INTO customer_config_history ' +
        '(customer_slug, git_sha, synced_at, synced_by, actor, prev_git_sha, r2_shadow_key) ' +
        'VALUES (?, ?, ?, ?, ?, ?, ?)'
    )
    .bind(
      opts.customer_slug,
      opts.git_sha,
      opts.synced_at,
      opts.synced_by,
      opts.actor ?? null,
      opts.prev_git_sha ?? null,
      opts.r2_shadow_key ?? null
    )
    .run()
}

const ENTITY_ID = 'entity-config-test'
const CUSTOMER_SLUG = 'smith-pi-firm'

async function freshDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

async function seedEntity(db: D1Database): Promise<void> {
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
    .bind(ENTITY_ID, ORG_ID, 'Smith PI Firm', 'smith-pi-firm')
    .run()
}

interface SeedOptions {
  entity_id?: string
  customer_slug?: string
  schema_version?: string
  personas?: PersonaConfig[]
  voice_library?: unknown
  escalation?: unknown
  business_hours?: unknown
  connectors?: unknown
  scope?: unknown
  /** Raw mcp_connector_json column value; undefined ⇒ NULL (fail-closed default). */
  mcp_connector_json?: string | null
  git_sha?: string
  synced_at?: string
}

async function seedConfig(db: D1Database, opts: SeedOptions = {}): Promise<void> {
  const personas = opts.personas ?? [makePersona({ slug: 'marcus' })]
  await db
    .prepare(
      `INSERT INTO customer_configs
        (entity_id, org_id, customer_slug, schema_version,
         personas_json, voice_library_json, escalation_json, business_hours_json,
         connectors_json, scope_json, mcp_connector_json, git_sha, synced_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .bind(
      opts.entity_id ?? ENTITY_ID,
      ORG_ID,
      opts.customer_slug ?? CUSTOMER_SLUG,
      opts.schema_version ?? '1.0.0',
      JSON.stringify(personas),
      opts.voice_library === undefined ? null : JSON.stringify(opts.voice_library),
      opts.escalation === undefined ? null : JSON.stringify(opts.escalation),
      opts.business_hours === undefined ? null : JSON.stringify(opts.business_hours),
      opts.connectors === undefined ? null : JSON.stringify(opts.connectors),
      opts.scope === undefined ? null : JSON.stringify(opts.scope),
      opts.mcp_connector_json === undefined ? null : opts.mcp_connector_json,
      opts.git_sha ?? 'a1b2c3d4e5f6',
      opts.synced_at ?? '2026-05-21T12:00:00Z'
    )
    .run()
}

function makePersona(overrides: Partial<PersonaConfig> = {}): PersonaConfig {
  return {
    slug: 'marcus',
    status: 'active',
    name: 'Marcus',
    title: 'AI Associate',
    signature_html: '<p>Marcus<br/>AI Associate</p>',
    tone: ['warm-but-professional'],
    send_as: { agentmail_identity: 'marcus@smith-pi-firm.agents.smd.services' },
    entitlements: { exposure: { external_send: 'draft_for_review' } },
    skills: [
      {
        name: 'inbox-triage-and-draft',
        initiation: { manual: true, scheduled: false, webhook: false },
      },
    ],
    channel_bindings: [{ integration: 'ms-graph', channels: ['primary-inbox'] }],
    ...overrides,
  }
}

describe('getCustomerConfig', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
  })

  it('returns null when no config row exists for the entity', async () => {
    await seedEntity(db)
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result).toBeNull()
  })

  it('parses personas_json and other JSON columns into typed shape', async () => {
    await seedEntity(db)
    await seedConfig(db, {
      personas: [makePersona()],
      escalation: { red_flag_recipients: ['partner@firm.com'] },
      business_hours: { start: '08:00', end: '18:00', tz: 'America/Phoenix' },
    })
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result).not.toBeNull()
    expect(result?.personas).toHaveLength(1)
    expect(result?.personas[0].name).toBe('Marcus')
    expect(result?.personas[0].skills[0].name).toBe('inbox-triage-and-draft')
    expect(result?.escalation).toEqual({ red_flag_recipients: ['partner@firm.com'] })
    expect(result?.business_hours).toEqual({ start: '08:00', end: '18:00', tz: 'America/Phoenix' })
  })

  it('returns the row’s schema_version and git_sha verbatim for drift detection', async () => {
    await seedEntity(db)
    await seedConfig(db, { schema_version: '2.3.1', git_sha: 'deadbeef1234' })
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result?.schema_version).toBe('2.3.1')
    expect(result?.git_sha).toBe('deadbeef1234')
  })

  it('returns null for nullable columns that were not populated', async () => {
    await seedEntity(db)
    await seedConfig(db, {
      personas: [makePersona()],
      // voice_library, escalation, business_hours, connectors, scope all omitted
    })
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result?.voice_library).toBeNull()
    expect(result?.escalation).toBeNull()
    expect(result?.business_hours).toBeNull()
    expect(result?.connectors).toBeNull()
    expect(result?.scope).toBeNull()
  })

  it('defaults compliance_enabled to false when the projection column was not populated', async () => {
    await seedEntity(db)
    await seedConfig(db, { personas: [makePersona()] })
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result?.compliance_enabled).toBe(false)
  })

  it('projects compliance_enabled=true and vertical when explicitly set (#895)', async () => {
    await seedEntity(db)
    await seedConfig(db, { personas: [makePersona()] })
    // Update directly — the seed helper doesn't accept the new columns
    // because they default; the row is freshly written without them.
    await db
      .prepare(
        `UPDATE customer_configs
            SET compliance_enabled = 1, vertical = 'law-firm'
          WHERE entity_id = ?`
      )
      .bind(ENTITY_ID)
      .run()
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result?.compliance_enabled).toBe(true)
    expect(result?.vertical).toBe('law-firm')
  })

  it('keeps vertical null when not yet projected', async () => {
    await seedEntity(db)
    await seedConfig(db, { personas: [makePersona()] })
    const result = await getCustomerConfig(db, ENTITY_ID)
    expect(result?.vertical).toBeNull()
  })
})

describe('customer_configs schema integrity', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
  })

  it('FK from entity_id to entities.id rejects an orphan insert', async () => {
    // Insert with a non-existent entity_id should fail
    await expect(seedConfig(db, { entity_id: 'no-such-entity' })).rejects.toThrow()
  })

  it('UNIQUE(customer_slug) rejects duplicate-slug insert across different entities', async () => {
    await seedEntity(db)
    await db
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind('entity-other', ORG_ID, 'Other Firm', 'other-firm')
      .run()

    await seedConfig(db, { entity_id: ENTITY_ID, customer_slug: 'same-slug' })
    await expect(
      seedConfig(db, { entity_id: 'entity-other', customer_slug: 'same-slug' })
    ).rejects.toThrow()
  })

  it('allows MANY configs per entity (multi-operator model, migration 0090)', async () => {
    // Before 0090 entity_id was the PK; a second config for the same entity was
    // a PK violation. Now an entity (one client) may own several operators.
    await seedEntity(db)
    await seedConfig(db, { entity_id: ENTITY_ID, customer_slug: 'smd' })
    await expect(
      seedConfig(db, { entity_id: ENTITY_ID, customer_slug: 'pilot-smokeball' })
    ).resolves.not.toThrow()
  })
})

describe('getCustomerConfigBySlug / listCustomerConfigsForEntity (multi-operator)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
    await seedEntity(db)
  })

  it('getCustomerConfigBySlug returns the row addressed by customer_slug', async () => {
    await seedConfig(db, { entity_id: ENTITY_ID, customer_slug: 'smd' })
    await seedConfig(db, {
      entity_id: ENTITY_ID,
      customer_slug: 'pilot-smokeball',
      personas: [makePersona({ slug: 'crane-law', name: 'Crane' })],
    })
    const smd = await getCustomerConfigBySlug(db, 'smd')
    const pilot = await getCustomerConfigBySlug(db, 'pilot-smokeball')
    expect(smd?.customer_slug).toBe('smd')
    expect(smd?.entity_id).toBe(ENTITY_ID)
    expect(pilot?.customer_slug).toBe('pilot-smokeball')
    expect(pilot?.personas[0].name).toBe('Crane')
  })

  it('getCustomerConfigBySlug returns null for an unknown slug', async () => {
    expect(await getCustomerConfigBySlug(db, 'no-such-slug')).toBeNull()
  })

  it('listCustomerConfigsForEntity returns every operator the entity owns', async () => {
    await seedConfig(db, { entity_id: ENTITY_ID, customer_slug: 'smd' })
    await seedConfig(db, { entity_id: ENTITY_ID, customer_slug: 'pilot-smokeball' })
    const list = await listCustomerConfigsForEntity(db, ENTITY_ID)
    // Order is created_at ASC; the two seeds can share a second-granularity
    // timestamp, so assert membership rather than a brittle exact order.
    expect(list.map((c) => c.customer_slug).sort()).toEqual(['pilot-smokeball', 'smd'])
  })

  it('listCustomerConfigsForEntity returns [] when the entity owns none', async () => {
    expect(await listCustomerConfigsForEntity(db, ENTITY_ID)).toEqual([])
  })
})

describe('mcp_connector projection column (migration 0071)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
    await seedEntity(db)
  })

  it('a NULL mcp_connector_json column reads back as the fail-closed default', async () => {
    // A row that predates the column, or a customer.yaml with no block.
    await seedConfig(db) // mcp_connector_json omitted ⇒ NULL
    const config = await getCustomerConfig(db, ENTITY_ID)
    expect(config?.mcp_connector.enabled).toBe(false)
    expect(config?.mcp_connector.access).toEqual([])
  })

  it('an authored, enabled mcp_connector reads back intact', async () => {
    await seedConfig(db, {
      mcp_connector_json: JSON.stringify({
        enabled: true,
        data_posture: 'firm_only',
        access: [{ email: 'owner@firm.com', profile: 'crane' }],
      }),
    })
    const config = await getCustomerConfig(db, ENTITY_ID)
    expect(config?.mcp_connector.enabled).toBe(true)
    expect(config?.mcp_connector.data_posture).toBe('firm_only')
    expect(config?.mcp_connector.access).toEqual([{ email: 'owner@firm.com', profile: 'crane' }])
  })
})

describe('parseMcpConnector (fail-closed, defensive)', () => {
  const FAIL_CLOSED = {
    enabled: false,
    data_posture: 'open',
    policy: 'allowlist',
    allowed_domains: [],
    default_profile: null,
    ttl_days: 30,
    access: [],
  }

  it('null / undefined ⇒ disabled, allowlist, empty access', () => {
    expect(parseMcpConnector(null)).toEqual(FAIL_CLOSED)
    expect(parseMcpConnector(undefined)).toEqual(FAIL_CLOSED)
  })

  it('malformed JSON ⇒ fail-closed (never throws)', () => {
    expect(parseMcpConnector('{not json')).toEqual(FAIL_CLOSED)
  })

  it('a non-object value ⇒ fail-closed', () => {
    expect(parseMcpConnector('"a string"')).toEqual(FAIL_CLOSED)
    expect(parseMcpConnector('42')).toEqual(FAIL_CLOSED)
  })

  it('an unknown data_posture falls back to open; enabled requires literal true', () => {
    const c = parseMcpConnector(
      JSON.stringify({ enabled: 'yes', data_posture: 'bogus', access: [] })
    )
    expect(c.enabled).toBe(false) // only literal true enables
    expect(c.data_posture).toBe('open')
  })

  it('drops malformed access entries, keeps well-formed ones', () => {
    const c = parseMcpConnector(
      JSON.stringify({
        enabled: true,
        data_posture: 'open',
        access: [
          { email: 'good@firm.com', profile: 'crane' },
          { email: 'no-profile@firm.com' },
          { profile: 'orphan' },
          'not-an-object',
          null,
        ],
      })
    )
    expect(c.access).toEqual([{ email: 'good@firm.com', profile: 'crane' }])
  })

  it('surfaces a well-formed open policy (lowercased domains, profile, ttl)', () => {
    const c = parseMcpConnector(
      JSON.stringify({
        enabled: true,
        policy: 'open',
        allowed_domains: ['Firm.com', 'bad domain', 'partners.firm.com'],
        default_profile: 'marcus',
        ttl_days: 14,
        access: [],
      })
    )
    expect(c.policy).toBe('open')
    expect(c.allowed_domains).toEqual(['firm.com', 'partners.firm.com'])
    expect(c.default_profile).toBe('marcus')
    expect(c.ttl_days).toBe(14)
  })

  it('falls back policy/ttl defensively on garbage values', () => {
    const c = parseMcpConnector(
      JSON.stringify({ enabled: true, policy: 'everyone', ttl_days: 9999, access: [] })
    )
    expect(c.policy).toBe('allowlist')
    expect(c.ttl_days).toBe(30)
    expect(c.allowed_domains).toEqual([])
    expect(c.default_profile).toBeNull()
  })
})

// ===========================================================================
// customer_config_history — ADR 0022 Stream 3 substrate
// ===========================================================================

describe('customer_config_history helpers', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
  })

  it('returns empty list when no history exists', async () => {
    const rows = await listCustomerConfigHistory(db, 'smith-pi-firm')
    expect(rows).toEqual([])
  })

  it('CHECK constraint rejects an unknown synced_by value', async () => {
    await expect(
      db
        .prepare(
          'INSERT INTO customer_config_history (customer_slug, git_sha, synced_at, synced_by) ' +
            'VALUES (?, ?, ?, ?)'
        )
        .bind('smith-pi-firm', 'sha-bogus', '2026-05-27T00:00:00Z', 'not-a-valid-source')
        .run()
    ).rejects.toThrow()
  })

  it('isolates history per customer_slug', async () => {
    await seedSyncHistory(db, {
      customer_slug: 'smith-pi-firm',
      git_sha: 'sha-aaa',
      synced_at: '2026-05-27T00:00:00Z',
      synced_by: 'ci',
    })
    await seedSyncHistory(db, {
      customer_slug: 'jones-pi-firm',
      git_sha: 'sha-xxx',
      synced_at: '2026-05-27T00:00:00Z',
      synced_by: 'ci',
    })
    const smith = await listCustomerConfigHistory(db, 'smith-pi-firm')
    const jones = await listCustomerConfigHistory(db, 'jones-pi-firm')
    expect(smith).toHaveLength(1)
    expect(jones).toHaveLength(1)
    expect(smith[0].git_sha).toBe('sha-aaa')
    expect(jones[0].git_sha).toBe('sha-xxx')
  })

  it('listCustomerConfigHistory respects the limit argument', async () => {
    for (let i = 0; i < 5; i++) {
      await seedSyncHistory(db, {
        customer_slug: 'smith-pi-firm',
        git_sha: `sha-${i}`,
        synced_at: `2026-05-27T00:0${i}:00Z`,
        synced_by: 'ci',
        prev_git_sha: i === 0 ? null : `sha-${i - 1}`,
      })
    }
    const limited = await listCustomerConfigHistory(db, 'smith-pi-firm', 3)
    expect(limited).toHaveLength(3)
    expect(limited[0].git_sha).toBe('sha-4')
  })
})
