import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('milestones: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/milestones.ts'), 'utf-8')

  it('milestones.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/milestones.ts'))).toBe(true)
  })

  it('exports listMilestones function', () => {
    expect(source()).toContain('export async function listMilestones')
  })

  it('exports getMilestone function', () => {
    expect(source()).toContain('export async function getMilestone')
  })

  it('exports createMilestone function', () => {
    expect(source()).toContain('export async function createMilestone')
  })

  it('exports updateMilestone function', () => {
    expect(source()).toContain('export async function updateMilestone')
  })

  it('exports updateMilestoneStatus function', () => {
    expect(source()).toContain('export async function updateMilestoneStatus')
  })

  it('exports bulkCreateMilestones function', () => {
    expect(source()).toContain('export async function bulkCreateMilestones')
  })

  it('exports deleteMilestone function', () => {
    expect(source()).toContain('export async function deleteMilestone')
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs for primary keys', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('orders milestones by sort_order ASC', () => {
    const code = source()
    expect(code).toContain('ORDER BY sort_order ASC')
  })

  it('defines all valid milestone statuses', () => {
    const code = source()
    expect(code).toContain("'pending'")
    expect(code).toContain("'in_progress'")
    expect(code).toContain("'completed'")
    expect(code).toContain("'skipped'")
  })

  it('exports MILESTONE_STATUSES constant', () => {
    expect(source()).toContain('export const MILESTONE_STATUSES')
  })

  it('exports VALID_TRANSITIONS for status state machine', () => {
    expect(source()).toContain('export const VALID_TRANSITIONS')
  })

  it('enforces valid status transitions', () => {
    const code = source()
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('Invalid status transition')
  })

  it('defines valid transitions: pending -> in_progress | skipped', () => {
    const code = source()
    expect(code).toContain("pending: ['in_progress', 'skipped']")
  })

  it('defines valid transitions: in_progress -> completed | skipped', () => {
    const code = source()
    expect(code).toContain("in_progress: ['completed', 'skipped']")
  })

  it('completed and skipped are terminal states', () => {
    const code = source()
    expect(code).toContain('completed: []')
    expect(code).toContain('skipped: []')
  })

  it('auto-sets completed_at when transitioning to completed', () => {
    const code = source()
    expect(code).toContain("newStatus === 'completed'")
    expect(code).toContain('completed_at')
  })

  it('supports payment_trigger flag', () => {
    const code = source()
    expect(code).toContain('payment_trigger')
  })

  it('bulkCreateMilestones creates multiple milestones', () => {
    const code = source()
    expect(code).toContain('bulkCreateMilestones')
    expect(code).toContain('createMilestone')
    // Should iterate over array
    expect(code).toContain('milestones.length')
  })

  it('bulkCreateMilestones assigns sort_order sequentially', () => {
    const code = source()
    // Should use index for default sort order
    expect(code).toContain('sort_order: data.sort_order ?? i')
  })

  it('createMilestone handles sort_order', () => {
    const code = source()
    expect(code).toContain('sort_order')
  })
})
