import { describe, it, expect, vi } from 'vitest'
import {
  ORG_INPUT_DRIVER,
  ORG_OUTPUT_DRIVER,
  ORG_SLUG,
  UNMAPPED_SLUG,
  loadWorkspaceMapping,
  runIngest,
  type AnthropicSource,
  type AnthropicUsageRow,
  type CentralDb,
} from './ingest'
import { anthropicPricing, computeAnthropicCents } from './pricing'
import { run, type Env } from './index'

/** In-memory fake of the central D1 binding slice the ingest uses. */
class FakeDb implements CentralDb {
  public mappingRows: Array<{ customer_slug: string; anthropic_workspace_id: string }> = []
  public writes: Array<{ sql: string; params: unknown[] }> = []

  prepare(sql: string) {
    return {
      bind: (...params: unknown[]) => ({
        run: async () => {
          this.writes.push({ sql, params })
        },
        all: async <T>() => ({ results: [] as T[] }),
      }),
      all: async <T>() => {
        if (sql.includes('FROM customer_configs')) {
          return { results: this.mappingRows as unknown as T[] }
        }
        return { results: [] as T[] }
      },
    }
  }
}

class FakeAnthropic implements AnthropicSource {
  constructor(
    private rows: AnthropicUsageRow[],
    private error?: Error
  ) {}
  async fetchDailyUsage(): Promise<AnthropicUsageRow[]> {
    if (this.error) throw this.error
    return this.rows
  }
}

function paramsFor(db: FakeDb, slug: string, driver: string): unknown[] | undefined {
  return db.writes.find((w) => w.params[0] === slug && w.params[2] === driver)?.params
}

describe('pricing JSON shape', () => {
  it('anthropic pricing has expected models', () => {
    expect(anthropicPricing.models['claude-opus-4-7']).toBeDefined()
    expect(anthropicPricing.models['claude-opus-4-7'].input_per_million_cents).toBe(1500)
    expect(anthropicPricing.models['claude-opus-4-7'].output_per_million_cents).toBe(7500)
    // #1658 added claude-opus-4-8; the fleet model selection depends on it.
    expect(anthropicPricing.models['claude-opus-4-8']).toBeDefined()
    // 2026-08-27: the A&P medchron pipeline (engagements tools/medchron/models.py) defaults every
    // stage to these two ids; without rows the whole pipeline costed to zero with a warning.
    expect(anthropicPricing.models['claude-opus-5'].input_per_million_cents).toBe(500)
    expect(anthropicPricing.models['claude-opus-5'].output_per_million_cents).toBe(2500)
    expect(anthropicPricing.models['claude-sonnet-5'].input_per_million_cents).toBe(200)
    expect(anthropicPricing.models['claude-sonnet-5'].output_per_million_cents).toBe(1000)
  })
})

describe('cents math', () => {
  it('computes anthropic cents from token counts', () => {
    const r = computeAnthropicCents('claude-opus-4-7', 2_000_000, 1_000_000)
    expect(r.inputCents).toBe(3000)
    expect(r.outputCents).toBe(7500)
    expect(r.warning).toBeNull()
  })

  it('returns zero cents and a warning for unknown anthropic model', () => {
    const r = computeAnthropicCents('mystery', 1_000_000, 1_000_000)
    expect(r.inputCents).toBe(0)
    expect(r.outputCents).toBe(0)
    expect(r.warning).toContain('mystery')
  })
})

describe('loadWorkspaceMapping', () => {
  it('maps workspace ids to customer slugs', async () => {
    const db = new FakeDb()
    db.mappingRows = [
      { customer_slug: 'acme', anthropic_workspace_id: 'wrkspc_1' },
      { customer_slug: 'globex', anthropic_workspace_id: 'wrkspc_2' },
    ]
    const map = await loadWorkspaceMapping(db)
    expect(map.get('wrkspc_1')).toBe('acme')
    expect(map.get('wrkspc_2')).toBe('globex')
    expect(map.size).toBe(2)
  })
})

describe('runIngest', () => {
  it('writes per-seat rows keyed by the mapped customer_slug', async () => {
    const db = new FakeDb()
    db.mappingRows = [{ customer_slug: 'acme', anthropic_workspace_id: 'wrkspc_1' }]
    const src = new FakeAnthropic([
      {
        workspaceId: 'wrkspc_1',
        model: 'claude-opus-4-7',
        inputTokens: 1_000_000,
        outputTokens: 500_000,
      },
    ])
    const result = await runIngest(db, src, 'admin-key', '2026-07-02')
    expect(result.ok).toBe(true)

    const input = paramsFor(db, 'acme', 'claude_api_input_tokens')
    const output = paramsFor(db, 'acme', 'claude_api_output_tokens')
    expect(input).toBeDefined()
    expect(output).toBeDefined()
    expect(input![1]).toBe('2026-07-02')
    expect(input![3]).toBe(1500) // 1M input tokens at 1500c/M
    expect(input![4]).toBe(1_000_000)
    expect(output![3]).toBe(3750) // 0.5M output tokens at 7500c/M
    expect(result.centsWritten).toBe(1500 + 3750)
  })

  it('aggregates multiple models within one workspace into one row pair', async () => {
    const db = new FakeDb()
    db.mappingRows = [{ customer_slug: 'acme', anthropic_workspace_id: 'wrkspc_1' }]
    const src = new FakeAnthropic([
      {
        workspaceId: 'wrkspc_1',
        model: 'claude-opus-4-7',
        inputTokens: 1_000_000,
        outputTokens: 0,
      },
      {
        workspaceId: 'wrkspc_1',
        model: 'claude-opus-4-8',
        inputTokens: 1_000_000,
        outputTokens: 0,
      },
    ])
    await runIngest(db, src, 'admin-key', '2026-07-02')
    const input = paramsFor(db, 'acme', 'claude_api_input_tokens')
    expect(input![4]).toBe(2_000_000)
    // acme input+output(absent) + _org pair
    expect(db.writes.filter((w) => w.params[0] === 'acme')).toHaveLength(1)
  })

  it('routes unmapped workspace usage to _unmapped and names the workspace', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const db = new FakeDb()
    const src = new FakeAnthropic([
      {
        workspaceId: 'wrkspc_ghost',
        model: 'claude-opus-4-7',
        inputTokens: 100_000,
        outputTokens: 100_000,
      },
    ])
    const result = await runIngest(db, src, 'admin-key', '2026-07-02')
    expect(result.unmappedWorkspaceIds).toEqual(['wrkspc_ghost'])
    expect(paramsFor(db, UNMAPPED_SLUG, 'claude_api_input_tokens')).toBeDefined()
    expect(warn.mock.calls.some((c) => String(c[0]).includes('wrkspc_ghost'))).toBe(true)
    warn.mockRestore()
  })

  it('routes default-workspace (null) usage to _unmapped', async () => {
    const db = new FakeDb()
    const src = new FakeAnthropic([
      { workspaceId: null, model: 'claude-opus-4-7', inputTokens: 10_000, outputTokens: 0 },
    ])
    const result = await runIngest(db, src, 'admin-key', '2026-07-02')
    expect(paramsFor(db, UNMAPPED_SLUG, 'claude_api_input_tokens')).toBeDefined()
    expect(result.unmappedWorkspaceIds).toEqual([])
  })

  it('always writes the _org reconciliation pair, summing all workspaces', async () => {
    const db = new FakeDb()
    db.mappingRows = [{ customer_slug: 'acme', anthropic_workspace_id: 'wrkspc_1' }]
    const src = new FakeAnthropic([
      {
        workspaceId: 'wrkspc_1',
        model: 'claude-opus-4-7',
        inputTokens: 1_000_000,
        outputTokens: 0,
      },
      { workspaceId: null, model: 'claude-opus-4-7', inputTokens: 1_000_000, outputTokens: 0 },
    ])
    const result = await runIngest(db, src, 'admin-key', '2026-07-02')
    const orgInput = paramsFor(db, ORG_SLUG, ORG_INPUT_DRIVER)
    const orgOutput = paramsFor(db, ORG_SLUG, ORG_OUTPUT_DRIVER)
    expect(orgInput![4]).toBe(2_000_000)
    expect(orgInput![3]).toBe(3000)
    expect(orgOutput![3]).toBe(0)
    // org rows are reconciliation, not attribution: excluded from centsWritten
    expect(result.centsWritten).toBe(3000)
    expect(result.slugs).toContain(ORG_SLUG)
  })

  it('writes the zero _org pair even with no usage at all', async () => {
    const db = new FakeDb()
    const result = await runIngest(db, new FakeAnthropic([]), 'admin-key', '2026-07-02')
    expect(result.ok).toBe(true)
    expect(paramsFor(db, ORG_SLUG, ORG_INPUT_DRIVER)![3]).toBe(0)
    expect(db.writes).toHaveLength(2)
  })

  it('returns ok=false without writing when the usage fetch fails', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const db = new FakeDb()
    const result = await runIngest(
      db,
      new FakeAnthropic([], new Error('HTTP 503')),
      'admin-key',
      '2026-07-02'
    )
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('503')
    expect(db.writes).toHaveLength(0)
    err.mockRestore()
  })
})

describe('run (worker shell)', () => {
  it('logs one error and exits cleanly when ANTHROPIC_ADMIN_KEY is missing', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const env = { DB: new FakeDb() as unknown as D1Database } as Env
    const result = await run(env, '2026-07-02')
    expect(result.ok).toBe(false)
    expect(result.reason).toContain('ANTHROPIC_ADMIN_KEY')
    expect(result.rowsWritten).toBe(0)
    expect(err).toHaveBeenCalledTimes(1)
    err.mockRestore()
  })
})
