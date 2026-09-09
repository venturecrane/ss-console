/**
 * Tests for the provisioning authoring + validation path
 * (src/lib/admin/provisioning.ts) — admin Operator console §4.5.
 *
 * Two halves: the pure assembly/serialization/validation helpers (no DB), and
 * the intent ledger against a real D1. The invariant that matters: the surface
 * VALIDATES (via the real validator + secret scan) and RECORDS INTENT — it never
 * stands up a Machine or writes git. The ledger has no entities() FK because the
 * customer does not exist yet at authoring time.
 */

import { describe, it, expect, beforeAll, beforeEach } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { env as testEnv } from 'cloudflare:workers'
import {
  splitList,
  parseProvisioningForm,
  buildCandidateDoc,
  serializeCustomerYaml,
  validateCandidate,
  verticalOptions,
  trustCeilingOptions,
  userRoleOptions,
  capabilityOptions,
  recordProvisioningIntent,
  listProvisioningIntent,
  type ProvisioningInput,
} from '../src/lib/admin/provisioning'

installWorkerdPolyfills()
const migrationsDir = path.resolve(__dirname, '../migrations')

function validInput(): ProvisioningInput {
  return {
    customer_id: 'smith-pi-firm',
    customer_name: 'Smith PI Firm',
    vertical: 'law-firm',
    addons: [],
    practice_areas: ['personal-injury'],
    fly_region: 'lax',
    model: 'claude-opus-4-7',
    hermes_ref: 'v2026.5.7@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0',
    machine_size: 'shared-cpu-1x',
    machine_memory_mb: 1024,
    user_email: 'partner@firm.com',
    user_role: 'principal',
    user_full_name: 'Jane Smith',
    persona_slug: 'marcus',
    persona_name: 'Marcus',
    persona_title: 'AI Associate',
    persona_tone: ['warm-but-professional', 'concise'],
    skill_name: 'matter-inbox-router',
    exposure_level: 'draft_for_review',
    connectors: [
      { capability: 'Email', adapter: 'm365-mail', backend: 'mcp:m365-mail', enabled: true },
    ],
  }
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

describe('splitList', () => {
  it('splits on commas and newlines, trims, drops empties', () => {
    expect(splitList('a, b\nc ,, ')).toEqual(['a', 'b', 'c'])
    expect(splitList('   ')).toEqual([])
  })
})

describe('option sources are real schema constants', () => {
  it('exposes verticals, ceilings, roles, capabilities', () => {
    expect(verticalOptions()).toContain('law-firm')
    expect(trustCeilingOptions()).toEqual(['autonomous', 'confirm', 'draft_for_review', 'refused'])
    expect(userRoleOptions()).toEqual(['principal', 'staff', 'compliance'])
    expect(capabilityOptions()).toContain('Email')
  })
})

describe('parseProvisioningForm', () => {
  it('parses scalars, lists, and positional connector rows', () => {
    const form = new FormData()
    form.set('customer_id', ' smith-pi-firm ')
    form.set('customer_name', 'Smith PI Firm')
    form.set('vertical', 'law-firm')
    form.set('practice_areas', 'personal-injury, workers-comp')
    form.set('fly_region', 'lax')
    form.set('model', 'claude-opus-4-7')
    form.set('hermes_ref', 'v2026.5.7@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0')
    form.set('machine_size', 'shared-cpu-1x')
    form.set('machine_memory_mb', '1024')
    form.set('user_email', 'partner@firm.com')
    form.set('user_role', 'principal')
    form.set('user_full_name', 'Jane Smith')
    form.set('persona_slug', 'marcus')
    form.set('persona_name', 'Marcus')
    form.set('persona_title', 'AI Associate')
    form.set('persona_tone', 'warm, concise')
    form.set('skill_name', 'matter-inbox-router')
    form.set('exposure_level', 'draft_for_review')
    // Two connector rows + one wholly-blank slot that must be skipped.
    form.append('connector_capability', 'Email')
    form.append('connector_adapter', 'm365-mail')
    form.append('connector_backend', 'mcp:m365-mail')
    form.append('connector_capability', 'Calendar')
    form.append('connector_adapter', 'm365-calendar')
    form.append('connector_backend', 'mcp:m365-calendar')
    form.append('connector_capability', '')
    form.append('connector_adapter', '')
    form.append('connector_backend', '')

    const input = parseProvisioningForm(form)
    expect(input.customer_id).toBe('smith-pi-firm') // trimmed
    expect(input.practice_areas).toEqual(['personal-injury', 'workers-comp'])
    expect(input.machine_memory_mb).toBe(1024)
    expect(input.connectors).toHaveLength(2)
    expect(input.connectors[1]).toEqual({
      capability: 'Calendar',
      adapter: 'm365-calendar',
      backend: 'mcp:m365-calendar',
      enabled: true,
    })
  })

  it('represents an unparseable memory value as NaN (not a coerced default)', () => {
    const form = new FormData()
    form.set('machine_memory_mb', 'lots')
    expect(Number.isNaN(parseProvisioningForm(form).machine_memory_mb)).toBe(true)
  })
})

describe('buildCandidateDoc + validateCandidate', () => {
  it('a complete authored input validates clean', () => {
    const { result } = validateCandidate(buildCandidateDoc(validInput()))
    expect(result.ok).toBe(true)
  })

  it('derives isolation invariants from the slug', () => {
    const doc = buildCandidateDoc(validInput())
    expect(doc.memory).toEqual({
      d1_namespace: 'smith-pi-firm',
      r2_vault_path: 'vaults/smith-pi-firm/',
      vectorize_index: 'hermes-smith-pi-firm-vault',
    })
  })

  it('omits authority so the validator resolves the fail-closed default', () => {
    const doc = buildCandidateDoc(validInput())
    expect('authority' in doc).toBe(false)
    const { result } = validateCandidate(doc)
    expect(result.ok).toBe(true)
  })

  it('reports validator errors for a bad slug and a bad hermes_ref', () => {
    const bad = validInput()
    bad.customer_id = 'Bad Slug!'
    bad.hermes_ref = 'not-a-pin'
    const { result } = validateCandidate(buildCandidateDoc(bad))
    expect(result.ok).toBe(false)
    if (!result.ok) {
      const paths = result.errors.map((e) => e.path)
      expect(paths).toContain('customer_id')
      expect(paths).toContain('hermes_ref')
    }
  })

  it('fails closed when an authored field carries an inline secret', () => {
    const planted = validInput()
    planted.user_full_name = 'Jane AKIAIOSFODNN7EXAMPLE Smith'
    const { result } = validateCandidate(buildCandidateDoc(planted))
    expect(result.ok).toBe(false)
  })
})

describe('serializeCustomerYaml', () => {
  it('renders scalars, string lists, nested maps, and list-of-objects', () => {
    const yaml = serializeCustomerYaml(buildCandidateDoc(validInput()))
    expect(yaml).toContain("customer_id: 'smith-pi-firm'")
    expect(yaml).toContain('schema_version: 1')
    // list-of-objects: the bullet carries the first key, the rest align under it
    expect(yaml).toMatch(/personas:\n {2}- slug: 'marcus'\n {4}status: 'active'/)
    // empty list renders inline
    expect(yaml).toContain('addons: []')
    // nested map
    expect(yaml).toMatch(/machine:\n {2}size: 'shared-cpu-1x'\n {2}memory_mb: 1024/)
  })

  it('single-quotes strings and doubles internal quotes', () => {
    const yaml = serializeCustomerYaml({ name: "O'Brien & Co" })
    expect(yaml).toBe("name: 'O''Brien & Co'\n")
  })
})

// ---------------------------------------------------------------------------
// Intent ledger (real D1)
// ---------------------------------------------------------------------------

describe('provisioning intent ledger', () => {
  beforeAll(() => {
    expect(discoverNumericMigrations(migrationsDir).length).toBeGreaterThan(0)
  })

  beforeEach(async () => {
    const db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    for (const k of Object.keys(testEnv)) delete (testEnv as unknown as Record<string, unknown>)[k]
    Object.assign(testEnv, { DB: db })
  })

  it('records a validated attempt with the candidate YAML and the SMD actor', async () => {
    const { result, yamlText } = validateCandidate(buildCandidateDoc(validInput()))
    expect(result.ok).toBe(true)
    const id = await recordProvisioningIntent(testEnv.DB, {
      customer_id: 'smith-pi-firm',
      customer_name: 'Smith PI Firm',
      vertical: 'law-firm',
      actor_user_id: 'usr-captain',
      actor_email: 'captain@example.com',
      actor_role: 'admin',
      outcome: 'validated',
      error_count: 0,
      candidate_yaml: yamlText,
    })
    expect(id).toBeGreaterThan(0)

    const rows = await listProvisioningIntent(testEnv.DB)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({
      customer_id: 'smith-pi-firm',
      vertical: 'law-firm',
      outcome: 'validated',
      error_count: 0,
      actor_email: 'captain@example.com',
    })
  })

  it('records a rejected attempt with the error count and empty YAML', async () => {
    await recordProvisioningIntent(testEnv.DB, {
      customer_id: 'broken',
      customer_name: 'Broken Co',
      vertical: 'law-firm',
      actor_user_id: 'usr-captain',
      actor_email: 'captain@example.com',
      actor_role: 'admin',
      outcome: 'rejected',
      error_count: 3,
      candidate_yaml: '',
    })
    const rows = await listProvisioningIntent(testEnv.DB)
    expect(rows[0]).toMatchObject({ outcome: 'rejected', error_count: 3 })
  })

  it('returns newest first', async () => {
    for (const slug of ['a-co', 'b-co', 'c-co']) {
      await recordProvisioningIntent(testEnv.DB, {
        customer_id: slug,
        customer_name: slug,
        vertical: 'mixed',
        actor_user_id: 'usr-captain',
        actor_email: 'captain@example.com',
        actor_role: 'admin',
        outcome: 'validated',
        error_count: 0,
        candidate_yaml: 'x',
      })
    }
    const rows = await listProvisioningIntent(testEnv.DB)
    expect(rows.map((r) => r.customer_id)).toEqual(['c-co', 'b-co', 'a-co'])
  })
})
