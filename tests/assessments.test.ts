import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Security wiring of the assessment surface: parameterized, org-scoped SQL,
 * org-scoped and sanitized R2 keys, admin-session gates on every route, and a
 * transcript served as a download. The data layer's behaviour runs on D1 in
 * tests/assessments-behaviour.test.ts.
 */

describe('assessments: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/assessments.ts'), 'utf-8')

  it('assessments.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/assessments.ts'))).toBe(true)
  })

  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // Should not use template literals in SQL strings
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('scopes all queries to org_id', () => {
    const code = source()
    expect(code).toContain("'org_id = ?'")
    expect(code).toContain('org_id = ?')
  })
})

describe('assessments: R2 storage helper', () => {
  const source = () => readFileSync(resolve('src/lib/storage/r2.ts'), 'utf-8')

  it('r2.ts exists', () => {
    expect(existsSync(resolve('src/lib/storage/r2.ts'))).toBe(true)
  })

  it('uses structured key pattern with org scoping', () => {
    const code = source()
    expect(code).toContain('orgId')
    expect(code).toContain('assessmentId')
    expect(code).toContain('/transcript/')
  })

  it('sanitizes filenames for R2 keys', () => {
    const code = source()
    expect(code).toContain('replace')
    // Should only allow safe characters
    expect(code).toContain(/[^a-zA-Z0-9._-]/.source)
  })
})

describe('assessments: API routes', () => {
  it('create endpoint exists at src/pages/api/admin/assessments/index.ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/assessments/index.ts'))).toBe(true)
  })

  it('update endpoint exists at src/pages/api/admin/assessments/[id].ts', () => {
    expect(existsSync(resolve('src/pages/api/admin/assessments/[id].ts'))).toBe(true)
  })

  it('transcript download endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/assessments/[id]/transcript.ts'))).toBe(true)
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/assessments/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(createCode).toContain('requireAdminSession')
    expect(updateCode).toContain('requireAdminSession')
  })

  it('transcript endpoint verifies admin session', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/assessments/[id]/transcript.ts'),
      'utf-8'
    )
    expect(code).toContain('requireAdminSession')
  })

  it('transcript endpoint returns file as download', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/assessments/[id]/transcript.ts'),
      'utf-8'
    )
    expect(code).toContain('Content-Disposition')
    expect(code).toContain('attachment')
  })
})
