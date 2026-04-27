/**
 * Tests for pipeline-settings DAL.
 *
 * Mocks D1Database so we can verify:
 *   - getPipelineSettings returns defaults when the table is empty
 *   - getPipelineSettings overlays stored values onto defaults
 *   - getPipelineSettings is graceful with corrupt/unparseable values
 *   - updatePipelineSettings rejects out-of-range values
 *   - updatePipelineSettings writes audit rows on real changes only
 *   - updatePipelineSettings is a no-op when value matches stored
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import {
  getPipelineSettings,
  updatePipelineSettings,
  SETTING_SPECS,
  type PipelineId,
} from './pipeline-settings'

interface StatementChain {
  bind: (...args: unknown[]) => StatementChain
  first: () => Promise<unknown>
  all: () => Promise<{ results: unknown[] }>
  run: () => Promise<{ success: boolean }>
}

interface MockD1State {
  // Stored settings rows: keyed by `${pipeline}:${key}` -> value string.
  settings: Map<string, string>
  // Audit rows captured for assertions.
  audits: Array<{
    pipeline: string
    key: string
    old_value: string | null
    new_value: string
  }>
  // Inserts captured for assertions.
  upserts: Array<{ pipeline: string; key: string; value: string }>
}

function makeMockD1(state: MockD1State): D1Database {
  const prepare = vi.fn((sql: string): StatementChain => {
    let pipeline = ''
    let pendingInsert: { pipeline: string; key: string; value: string } | null = null
    let pendingAudit: {
      pipeline: string
      key: string
      old_value: string | null
      new_value: string
    } | null = null

    const chain: StatementChain = {
      bind(...args: unknown[]) {
        // Heuristic dispatch on SQL substring — sufficient for tests.
        if (
          sql.includes('FROM pipeline_settings') &&
          !sql.includes('audit') &&
          sql.includes('WHERE org_id')
        ) {
          pipeline = String(args[1])
        } else if (sql.includes('INSERT INTO pipeline_settings_audit')) {
          // (id, org_id, pipeline, key, old_value, new_value, ...)
          pendingAudit = {
            pipeline: String(args[2]),
            key: String(args[3]),
            old_value: args[4] === null ? null : String(args[4]),
            new_value: String(args[5]),
          }
        } else if (sql.includes('INSERT INTO pipeline_settings')) {
          // (id, org_id, pipeline, key, value, ...)
          pendingInsert = {
            pipeline: String(args[2]),
            key: String(args[3]),
            value: String(args[4]),
          }
        }
        return chain
      },
      async first() {
        return null
      },
      async all() {
        if (sql.includes('SELECT key, value FROM pipeline_settings')) {
          // Internal read inside updatePipelineSettings — return existing.
          const out: Array<{ key: string; value: string }> = []
          for (const [k, v] of state.settings.entries()) {
            const [p, key] = k.split(':')
            if (p === pipeline) out.push({ key, value: v })
          }
          return { results: out }
        }
        if (sql.includes('FROM pipeline_settings') && sql.includes('WHERE org_id')) {
          const out: Array<{ pipeline: string; key: string; value: string }> = []
          for (const [k, v] of state.settings.entries()) {
            const [p, key] = k.split(':')
            if (p === pipeline) out.push({ pipeline: p, key, value: v })
          }
          return { results: out }
        }
        return { results: [] }
      },
      async run() {
        if (pendingInsert) {
          state.upserts.push(pendingInsert)
          state.settings.set(`${pendingInsert.pipeline}:${pendingInsert.key}`, pendingInsert.value)
          pendingInsert = null
        }
        if (pendingAudit) {
          state.audits.push(pendingAudit)
          pendingAudit = null
        }
        return { success: true }
      },
    }
    return chain
  })

  return { prepare } as unknown as D1Database
}

const ORG = 'org-test-001'
const ACTOR = { userId: 'u-1', email: 'admin@example.com' }

describe('getPipelineSettings', () => {
  it('returns hardcoded defaults when no rows exist', async () => {
    const state: MockD1State = { settings: new Map(), audits: [], upserts: [] }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'review_mining')
    expect(settings.pain_threshold).toBe(7)
    expect(settings.max_review_checks).toBe(200)
    expect(settings.outscraper_budget_usd_per_run).toBe(1.0)
  })

  it('overlays stored values onto defaults', async () => {
    const state: MockD1State = {
      settings: new Map([
        ['review_mining:pain_threshold', '5'],
        ['review_mining:max_review_checks', '500'],
      ]),
      audits: [],
      upserts: [],
    }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'review_mining')
    expect(settings.pain_threshold).toBe(5)
    expect(settings.max_review_checks).toBe(500)
    // Untouched key falls back to default
    expect(settings.outscraper_budget_usd_per_run).toBe(1.0)
  })

  it('returns default for the same pipeline when value is unparseable', async () => {
    const state: MockD1State = {
      settings: new Map([['review_mining:pain_threshold', 'not-a-number']]),
      audits: [],
      upserts: [],
    }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'review_mining')
    expect(settings.pain_threshold).toBe(7) // default fallback
  })

  it('parses floats correctly for budget setting', async () => {
    const state: MockD1State = {
      settings: new Map([['review_mining:outscraper_budget_usd_per_run', '2.5']]),
      audits: [],
      upserts: [],
    }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'review_mining')
    expect(settings.outscraper_budget_usd_per_run).toBe(2.5)
  })

  it('returns defaults for job_monitor pain_threshold', async () => {
    const state: MockD1State = { settings: new Map(), audits: [], upserts: [] }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'job_monitor')
    expect(settings.pain_threshold).toBe(7)
  })

  it('returns defaults for new_business pain_threshold', async () => {
    const state: MockD1State = { settings: new Map(), audits: [], upserts: [] }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'new_business')
    expect(settings.pain_threshold).toBe(1)
  })

  it('ignores unknown keys silently', async () => {
    const state: MockD1State = {
      settings: new Map([['review_mining:unknown_key', '99']]),
      audits: [],
      upserts: [],
    }
    const db = makeMockD1(state)
    const settings = await getPipelineSettings(db, ORG, 'review_mining')
    // Defaults intact; no throw.
    expect(settings.pain_threshold).toBe(7)
  })
})

describe('updatePipelineSettings validation', () => {
  let state: MockD1State
  beforeEach(() => {
    state = { settings: new Map(), audits: [], upserts: [] }
  })

  it('rejects unknown setting keys', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'totally_made_up', value: 5 }],
      ACTOR
    )
    expect(res.ok).toBe(false)
    expect(res.errors[0]).toContain('Unknown setting')
    expect(state.upserts).toHaveLength(0)
  })

  it('rejects out-of-range pain_threshold (below min)', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: 0 }],
      ACTOR
    )
    expect(res.ok).toBe(false)
    expect(res.errors[0]).toContain('between')
    expect(state.upserts).toHaveLength(0)
  })

  it('rejects out-of-range pain_threshold (above max)', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: 11 }],
      ACTOR
    )
    expect(res.ok).toBe(false)
    expect(state.upserts).toHaveLength(0)
  })

  it('rejects non-integer when spec.type is int', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: 5.5 }],
      ACTOR
    )
    expect(res.ok).toBe(false)
    expect(res.errors[0]).toContain('whole number')
  })

  it('rejects NaN', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: NaN }],
      ACTOR
    )
    expect(res.ok).toBe(false)
  })

  it('rejects the entire batch if any input is invalid (all-or-nothing)', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [
        { key: 'pain_threshold', value: 8 },
        { key: 'max_review_checks', value: -1 },
      ],
      ACTOR
    )
    expect(res.ok).toBe(false)
    expect(state.upserts).toHaveLength(0)
  })
})

describe('updatePipelineSettings write path', () => {
  let state: MockD1State
  beforeEach(() => {
    state = { settings: new Map(), audits: [], upserts: [] }
  })

  it('writes a setting row and an audit row on initial save', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: 8 }],
      ACTOR
    )
    expect(res.ok).toBe(true)
    expect(res.changed).toBe(1)
    expect(state.upserts).toHaveLength(1)
    expect(state.upserts[0]).toMatchObject({
      pipeline: 'review_mining',
      key: 'pain_threshold',
      value: '8',
    })
    expect(state.audits).toHaveLength(1)
    expect(state.audits[0]).toMatchObject({
      pipeline: 'review_mining',
      key: 'pain_threshold',
      old_value: null,
      new_value: '8',
    })
  })

  it('records old_value in audit when updating an existing setting', async () => {
    state.settings.set('review_mining:pain_threshold', '7')
    const db = makeMockD1(state)
    await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: 9 }],
      ACTOR
    )
    expect(state.audits).toHaveLength(1)
    expect(state.audits[0]).toMatchObject({
      old_value: '7',
      new_value: '9',
    })
  })

  it('skips writes and audit when value is unchanged', async () => {
    state.settings.set('review_mining:pain_threshold', '7')
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [{ key: 'pain_threshold', value: 7 }],
      ACTOR
    )
    expect(res.ok).toBe(true)
    expect(res.changed).toBe(0)
    expect(state.upserts).toHaveLength(0)
    expect(state.audits).toHaveLength(0)
  })

  it('writes multiple settings in one batch', async () => {
    const db = makeMockD1(state)
    const res = await updatePipelineSettings(
      db,
      ORG,
      'review_mining',
      [
        { key: 'pain_threshold', value: 6 },
        { key: 'max_review_checks', value: 300 },
        { key: 'outscraper_budget_usd_per_run', value: 2.0 },
      ],
      ACTOR
    )
    expect(res.ok).toBe(true)
    expect(res.changed).toBe(3)
    expect(state.upserts).toHaveLength(3)
    expect(state.audits).toHaveLength(3)
  })
})

describe('SETTING_SPECS coverage', () => {
  it('has pain_threshold defined for all three pipelines named in issue #595', () => {
    const required: PipelineId[] = ['review_mining', 'job_monitor', 'new_business']
    for (const p of required) {
      expect(SETTING_SPECS[p].pain_threshold).toBeDefined()
    }
  })

  it('every setting default sits within its declared range', () => {
    for (const [, specs] of Object.entries(SETTING_SPECS)) {
      for (const [, spec] of Object.entries(specs)) {
        expect(spec.default).toBeGreaterThanOrEqual(spec.min)
        expect(spec.default).toBeLessThanOrEqual(spec.max)
      }
    }
  })
})
