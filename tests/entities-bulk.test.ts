import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('entities-bulk: DAL', () => {
  const source = () => readFileSync(resolve('src/lib/db/entities-bulk.ts'), 'utf-8')

  it('file exists', () => {
    expect(existsSync(resolve('src/lib/db/entities-bulk.ts'))).toBe(true)
  })

  it('exports bulkDismissEntities', () => {
    expect(source()).toContain('export async function bulkDismissEntities')
  })

  it('exports listEntitiesForExport', () => {
    expect(source()).toContain('export async function listEntitiesForExport')
  })

  it('returns a partial-success shape with ok + failed arrays', () => {
    const code = source()
    expect(code).toContain('ok: BulkActionOk[]')
    expect(code).toContain('failed: BulkActionFailure[]')
  })

  it('delegates to transitionStage for each entity (per-entity context row)', () => {
    // Reusing `transitionStage` guarantees each entity gets its own
    // `stage_change` context entry — one of the ACs.
    const code = source()
    expect(code).toContain('transitionStage')
    expect(code).toContain('import { transitionStage')
  })

  it('writes a supplemental structured note per entity (lost_reason metadata)', () => {
    // The second per-entity context write is how the Lost tab filter in
    // #477 finds these rows by enum code.
    const code = source()
    expect(code).toContain('appendContext')
    expect(code).toContain('lost_reason:')
  })

  it('does not emit a single rollup context entry for N entities', () => {
    // Negative check — a for-loop guarantees per-entity writes; catch
    // accidental batch INSERTs with multiple ids in one row.
    const code = source()
    expect(code).not.toMatch(/INSERT INTO context[^;]+VALUES[^;]+,[^;]*,[^;]*,[^;]*\)/s)
    expect(code).toContain('for (const id of ids)')
  })

  it('continues past per-entity errors (try/catch inside the loop)', () => {
    const code = source()
    const loopStart = code.indexOf('for (const id of ids)')
    const tryIdx = code.indexOf('try {', loopStart)
    const catchIdx = code.indexOf('} catch', tryIdx)
    expect(loopStart).toBeGreaterThan(-1)
    expect(tryIdx).toBeGreaterThan(loopStart)
    expect(catchIdx).toBeGreaterThan(tryIdx)
  })

  it('validates the reason enum before doing any work', () => {
    const code = source()
    expect(code).toContain('isLostReason(options.reason)')
  })

  it('listEntitiesForExport uses parameterized IN query with per-id placeholders', () => {
    const code = source()
    expect(code).toContain("ids.map(() => '?').join(', ')")
    expect(code).toContain('e.org_id = ?')
    // No template-literal injection of raw id values. `${placeholders}` is
    // fine because `placeholders` is a pre-built `?, ?, ?` string — catch
    // anything that looks like `IN (${ids` / `IN (${...ids` etc.
    expect(code).not.toMatch(/IN \(\$\{\s*\.?\.?\.?\s*ids/)
  })

  it('listEntitiesForExport scopes to org_id and pulls primary contact email', () => {
    const code = source()
    expect(code).toContain('e.org_id = ?')
    expect(code).toContain('contacts c')
    expect(code).toContain('c.email IS NOT NULL')
  })
})

describe('api/admin/entities/bulk: endpoint', () => {
  const source = () => readFileSync(resolve('src/pages/api/admin/entities/bulk.ts'), 'utf-8')

  it('file exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/entities/bulk.ts'))).toBe(true)
  })

  it('exports POST handler', () => {
    expect(source()).toContain('export const POST: APIRoute')
  })

  it('requires admin role', () => {
    expect(source()).toContain("session.role !== 'admin'")
  })

  it('rejects empty id arrays', () => {
    expect(source()).toContain('ids must be a non-empty array')
  })

  it('caps batch size', () => {
    expect(source()).toContain('batch size capped')
  })

  it('validates reason via canonical isLostReason', () => {
    expect(source()).toContain('isLostReason(reason)')
  })

  it('returns 207 when some entities fail (partial success)', () => {
    expect(source()).toContain('result.failed.length === 0 ? 200 : 207')
  })

  it('CSV response sets attachment disposition', () => {
    const code = source()
    expect(code).toContain("'Content-Type': 'text/csv; charset=utf-8'")
    expect(code).toContain("'Content-Disposition': `attachment;")
  })

  it('CSV escapes quotes / commas / newlines', () => {
    const code = source()
    expect(code).toContain('/[",\\n\\r]/')
    expect(code).toContain('replace(/"/g, \'""\')')
  })

  it('CSV header lists id + contact fields but no fabricated template copy', () => {
    const code = source()
    expect(code).toContain("'id', 'name', 'contact_name', 'email', 'phone', 'website', 'stage'")
    // Sanity: no marketing / timeline / scope strings leaked into the CSV.
    expect(code).not.toMatch(/we'?ll reach out|kickoff|stabilization|business day/i)
  })
})

describe('admin/entities/index.astro: bulk UI wiring', () => {
  const source = () => readFileSync(resolve('src/pages/admin/entities/index.astro'), 'utf-8')

  it('imports LOST_REASONS from the canonical module', () => {
    expect(source()).toContain("import { LOST_REASONS } from '../../../lib/db/lost-reasons'")
  })

  it('defines BULK_ENABLED_STAGES = signal/prospect/assessing only', () => {
    const code = source()
    // Extract just the BULK_ENABLED_STAGES declaration line(s).
    const match = code.match(/BULK_ENABLED_STAGES:\s*EntityStage\[\]\s*=\s*\[([^\]]*)\]/)
    expect(match).toBeTruthy()
    const inside = match![1]
    expect(inside).toContain("'signal'")
    expect(inside).toContain("'prospect'")
    expect(inside).toContain("'assessing'")
    // Negative: bulk must NOT be enabled on these tabs.
    expect(inside).not.toContain("'proposing'")
    expect(inside).not.toContain("'engaged'")
    expect(inside).not.toContain("'ongoing'")
    expect(inside).not.toContain("'lost'")
    expect(inside).not.toContain("'delivered'")
  })

  it('renders a row checkbox class `bulk-select-row` only when bulkEnabled', () => {
    const code = source()
    expect(code).toContain('class="bulk-select-row')
    expect(code).toContain('{bulkEnabled && (')
  })

  it('row checkbox lives inside a `relative z-10` layer (safe with #462 stretched-link)', () => {
    const code = source()
    // Checkbox wrapper must elevate above row-level anchor.
    expect(code).toMatch(/relative z-10[^<]*<[^>]*>\s*<input[^>]+bulk-select-row/s)
  })

  it('renders header "Select all on page" checkbox', () => {
    const code = source()
    expect(code).toContain('id="bulk-select-all"')
    expect(code).toContain('aria-label="Select all on page"')
  })

  it('renders sticky bulk bar with the three actions', () => {
    const code = source()
    expect(code).toContain('id="bulk-bar"')
    expect(code).toContain('id="bulk-outreach"')
    expect(code).toContain('id="bulk-dismiss"')
    expect(code).toContain('id="bulk-export"')
    // Sticky positioning.
    expect(code).toContain('fixed bottom-6')
  })

  it('dismiss modal includes reason selector populated from LOST_REASONS', () => {
    const code = source()
    expect(code).toContain('id="bulk-dismiss-reason"')
    expect(code).toContain('{LOST_REASONS.map((r) => (')
  })

  it('dismiss modal has an aria-modal role=dialog with labelling', () => {
    const code = source()
    expect(code).toContain('role="dialog"')
    expect(code).toContain('aria-modal="true"')
    expect(code).toContain('aria-labelledby="bulk-dismiss-title"')
  })

  it('POSTs JSON to /api/admin/entities/bulk', () => {
    const code = source()
    expect(code).toContain("fetch('/api/admin/entities/bulk'")
    expect(code).toContain("'Content-Type': 'application/json'")
    expect(code).toContain("action: 'dismiss'")
    expect(code).toContain("action: 'export'")
  })

  it('"Send outreach" uses mailto with BCC and blank body (Pattern A compliance)', () => {
    const code = source()
    expect(code).toContain('mailto:?bcc=')
    // Explicitly no hardcoded subject/body template.
    expect(code).not.toMatch(/mailto:[^`]*subject=/i)
    expect(code).not.toMatch(/mailto:[^`]*body=/i)
  })

  it('does not contain fabricated client-facing template copy (CLAUDE.md Pattern A)', () => {
    const code = source()
    // Sample the forbidden phrases from the CLAUDE.md audit.
    expect(code).not.toMatch(/we'll reach out to schedule/i)
    expect(code).not.toMatch(/stabilization period/i)
    expect(code).not.toMatch(/work begins within/i)
    expect(code).not.toMatch(/1 business day/i)
  })
})
