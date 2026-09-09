import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import type { CustomerYaml } from '../src/lib/operator/customer-yaml/types'
import {
  projectCustomerYamlToConfigRow,
  buildProjectionSql,
  escapeSqlLiteral,
} from '../src/lib/portal/customer-config-projection'
import { projectRow, type PersonaConfig } from '../src/lib/portal/customer-config'

const CTX = {
  entityId: 'entity-123',
  orgId: 'org-123',
  gitSha: 'abc123def456',
  syncedAt: '2026-06-10T18:00:00.000Z',
}

/**
 * Load + validate a FROZEN copy of customer-zero's customer.yaml, taken when
 * the `smd` seat was retired (2026-09-03). These tests cover projection SHAPES
 * -- an autonomous external_send, an enabled mcp_connector -- that no live
 * seat authors today, and coverage must not shrink because a seat did. It is
 * not a seat directory, so it re-arms neither the R2 publisher nor the
 * reconcilers (tests/customer-slug-pattern.test.ts, 'retired seats').
 */
function smdYaml(): CustomerYaml {
  const parsed = parseYaml(
    readFileSync(resolve('tests/fixtures/customer-yaml/retired-smd.customer.yaml'), 'utf-8')
  )
  const result = validate(parsed)
  if (!result.ok) {
    throw new Error('smd customer.yaml failed validation: ' + JSON.stringify(result.errors))
  }
  return result.value
}

/** Simulate SQLite's single-quote unescaping of a literal produced by escapeSqlLiteral. */
function unescapeSqlLiteral(literal: string): string | null {
  if (literal === 'NULL') return null
  return literal.slice(1, -1).replace(/''/g, "'")
}

describe('customer-config projection: real smd yaml', () => {
  it('round-trips through the read-side projectRow without throwing', () => {
    const row = projectCustomerYamlToConfigRow(smdYaml(), CTX)
    expect(() => projectRow(row)).not.toThrow()
    const config = projectRow(row)
    expect(config.customer_slug).toBe('smd')
    expect(config.vertical).toBe('mixed')
    expect(config.entity_id).toBe('entity-123')
  })

  it('absent compliance_enabled projects to 0 (never NaN)', () => {
    const row = projectCustomerYamlToConfigRow(smdYaml(), CTX)
    expect(row.compliance_enabled).toBe(0)
  })

  it('carries authored skill settings into personas_json (#2005 — the ashton-price pair)', () => {
    const parsed = parseYaml(
      readFileSync(resolve('operator/customers/ashton-price/customer.yaml'), 'utf-8')
    )
    const result = validate(parsed)
    if (!result.ok) {
      throw new Error(
        'ashton-price customer.yaml failed validation: ' + JSON.stringify(result.errors)
      )
    }
    const row = projectCustomerYamlToConfigRow(result.value, CTX)
    const personas = JSON.parse(row.personas_json) as PersonaConfig[]
    const skills = personas.flatMap((p) => p.skills)
    expect(skills.find((s) => s.name === 'client-verification-tracker')?.settings).toEqual({
      escalate_after_attempts: 3,
    })
    expect(skills.find((s) => s.name === 'medical-chronology-maintainer')?.settings).toEqual({
      treatment_gap_flag_days: 45,
      chronology_package_document_allowance_per_month: 2000,
      chronology_package_page_allowance_per_month: 15000,
    })
    // Skills without authored settings must project WITHOUT the key (byte-stable).
    expect('settings' in (skills.find((s) => s.name === 'discovery-served-watch') ?? {})).toBe(
      false
    )
  })

  it('preserves persona exposure and skill initiation in the read-side shape', () => {
    const row = projectCustomerYamlToConfigRow(smdYaml(), CTX)
    const personas = JSON.parse(row.personas_json) as PersonaConfig[]
    const crane = personas.find((p) => p.slug === 'crane')
    expect(crane).toBeDefined()
    expect(crane!.name).toBe('Crane')
    expect(crane!.entitlements.exposure.external_send).toBe('autonomous')
    expect(crane!.skills.length).toBeGreaterThan(0)
    for (const skill of crane!.skills) {
      expect(Object.keys(skill).sort()).toEqual(['initiation', 'name'])
      expect(skill.initiation.manual).toBe(true)
    }
  })

  it('authority + credential custody survive the read-side resolvers', () => {
    const config = projectRow(projectCustomerYamlToConfigRow(smdYaml(), CTX))
    expect(config.authority).toBeDefined()
    expect(config.credential_custody_default).toBeTruthy()
  })

  it('mcp_connector survives the round-trip (smd authors an enabled connector)', () => {
    // smd's customer.yaml authors the Phase-1 MCP connector: enabled, with Scott
    // (scott@smd.services) bound to the crane persona. The projection must carry
    // the authored values through the write → read round-trip intact.
    const row = projectCustomerYamlToConfigRow(smdYaml(), CTX)
    expect(row.mcp_connector_json).not.toBeNull()
    const config = projectRow(row)
    expect(config.mcp_connector.enabled).toBe(true)
    expect(config.mcp_connector.access).toEqual([
      {
        email: 'scott@smd.services',
        profile: 'crane',
        clerk_subjects: ['user_3EEs0aMBRgu6PRxBa4g5YhHjggD', 'user_3E1RPGrTMxkSqciXMTyybUNSJWu'],
      },
    ])
  })
})

describe('customer-config projection: null normalization', () => {
  it('serializes absent nullable persona fields as JSON null (key present, not dropped)', () => {
    const yaml = smdYaml()
    // Force the nullable fields undefined to prove they normalize to null.
    yaml.personas[0] = {
      ...yaml.personas[0],
      title: undefined as unknown as string | null,
      signature_html: undefined as unknown as string | null,
      send_as: undefined as unknown as null,
    }
    const row = projectCustomerYamlToConfigRow(yaml, CTX)
    // The raw JSON text must contain explicit nulls, not omit the keys.
    expect(row.personas_json).toContain('"title":null')
    expect(row.personas_json).toContain('"signature_html":null')
    expect(row.personas_json).toContain('"send_as":null')
    const personas = JSON.parse(row.personas_json) as PersonaConfig[]
    expect(personas[0]).toHaveProperty('title', null)
    expect(personas[0]).toHaveProperty('signature_html', null)
    expect(personas[0]).toHaveProperty('send_as', null)
    expect(() => projectRow(row)).not.toThrow()
  })

  it('compliance_enabled true projects to 1', () => {
    const yaml = smdYaml()
    yaml.compliance_enabled = true
    expect(projectCustomerYamlToConfigRow(yaml, CTX).compliance_enabled).toBe(1)
  })
})

describe('customer-config projection: SQL escaping (adversarial characters)', () => {
  const ADVERSARIAL = `O'Brien said "hi"; <script>alert(1)</script>\nline2 -- not a comment`

  it('escapeSqlLiteral is lossless through a simulated SQLite unescape', () => {
    for (const v of [ADVERSARIAL, "''", "a'b'c", 'plain', '']) {
      expect(unescapeSqlLiteral(escapeSqlLiteral(v))).toBe(v)
    }
    expect(escapeSqlLiteral(null)).toBe('NULL')
    expect(unescapeSqlLiteral('NULL')).toBeNull()
  })

  it('adversarial signature_html survives projection + escape + unescape + parse', () => {
    const yaml = smdYaml()
    yaml.personas[0] = {
      ...yaml.personas[0],
      signature_html: ADVERSARIAL,
      tone: ["it's", 'a "quote"', 'semi;colon'],
    }
    const row = projectCustomerYamlToConfigRow(yaml, CTX)
    // Round-trip the escaped JSON literal back to the JSON, then parse it.
    const recovered = unescapeSqlLiteral(escapeSqlLiteral(row.personas_json))
    expect(recovered).toBe(row.personas_json)
    const personas = JSON.parse(recovered!) as PersonaConfig[]
    expect(personas[0].signature_html).toBe(ADVERSARIAL)
    expect(personas[0].tone).toEqual(["it's", 'a "quote"', 'semi;colon'])
  })
})

describe('customer-config projection: SQL document', () => {
  it('builds an idempotent UPSERT + a guarded manual history event', () => {
    const sql = buildProjectionSql(
      projectCustomerYamlToConfigRow(smdYaml(), CTX),
      'scott@smd.services'
    )
    expect(sql).toContain('INSERT INTO customer_configs')
    expect(sql).toContain('ON CONFLICT(customer_slug) DO UPDATE SET')
    // entity_id must NOT be in the update set — a re-projection never silently
    // repoints a config to a different owning entity (cross-tenant guard).
    expect(sql).not.toContain('entity_id = excluded.entity_id')
    expect(sql).toContain('INSERT INTO customer_config_history')
    expect(sql).toContain("'manual'")
    // No-op guard + prev_git_sha lineage.
    expect(sql).toContain('WHERE NOT EXISTS')
    expect(sql).toContain('ORDER BY id DESC LIMIT 1')
  })
})
