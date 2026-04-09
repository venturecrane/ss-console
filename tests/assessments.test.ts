import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('assessments: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/assessments.ts'), 'utf-8')

  it('assessments.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/assessments.ts'))).toBe(true)
  })

  it('exports listAssessments function', () => {
    expect(source()).toContain('export async function listAssessments')
  })

  it('exports getAssessment function', () => {
    expect(source()).toContain('export async function getAssessment')
  })

  it('exports createAssessment function', () => {
    expect(source()).toContain('export async function createAssessment')
  })

  it('exports updateAssessment function', () => {
    expect(source()).toContain('export async function updateAssessment')
  })

  it('exports updateAssessmentStatus function', () => {
    expect(source()).toContain('export async function updateAssessmentStatus')
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

  it('scopes all queries to org_id', () => {
    const code = source()
    expect(code).toContain("'org_id = ?'")
    expect(code).toContain('org_id = ?')
  })

  it('supports optional entity_id filter in listAssessments', () => {
    const code = source()
    expect(code).toContain("'entity_id = ?'")
  })

  it('defines all valid assessment statuses', () => {
    const code = source()
    expect(code).toContain("'scheduled'")
    expect(code).toContain("'completed'")
    expect(code).toContain("'disqualified'")
    expect(code).toContain("'converted'")
  })

  it('exports ASSESSMENT_STATUSES constant', () => {
    expect(source()).toContain('export const ASSESSMENT_STATUSES')
  })

  it('exports VALID_TRANSITIONS for status state machine', () => {
    expect(source()).toContain('export const VALID_TRANSITIONS')
  })

  it('enforces valid status transitions', () => {
    const code = source()
    // Should check current status against valid transitions
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('Invalid status transition')
  })

  it('auto-sets completed_at when transitioning to completed', () => {
    const code = source()
    expect(code).toContain("newStatus === 'completed'")
    expect(code).toContain('completed_at')
  })

  it('defines valid transitions: scheduled -> completed | disqualified | cancelled', () => {
    const code = source()
    // The VALID_TRANSITIONS object should allow these transitions.
    // `cancelled` was added in migration 0011 (booking system) to support
    // guest-initiated cancellation through the /book manage flow.
    expect(code).toContain("scheduled: ['completed', 'disqualified', 'cancelled']")
  })

  it('defines valid transitions: completed -> disqualified | converted', () => {
    const code = source()
    expect(code).toContain("completed: ['disqualified', 'converted']")
  })

  it('disqualified, converted, and cancelled are terminal states', () => {
    const code = source()
    expect(code).toContain('disqualified: []')
    expect(code).toContain('converted: []')
    expect(code).toContain('cancelled: []')
  })
})

describe('assessments: R2 storage helper', () => {
  const source = () => readFileSync(resolve('src/lib/storage/r2.ts'), 'utf-8')

  it('r2.ts exists', () => {
    expect(existsSync(resolve('src/lib/storage/r2.ts'))).toBe(true)
  })

  it('exports uploadTranscript function', () => {
    expect(source()).toContain('export async function uploadTranscript')
  })

  it('exports getTranscriptUrl function', () => {
    expect(source()).toContain('export function getTranscriptUrl')
  })

  it('exports getTranscript function', () => {
    expect(source()).toContain('export async function getTranscript')
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

  it('stores content type metadata', () => {
    expect(source()).toContain('contentType')
  })

  it('stores original filename in custom metadata', () => {
    expect(source()).toContain('originalName')
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

  it('create endpoint validates required client_id field', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/index.ts'), 'utf-8')
    expect(code).toContain('client_id')
    expect(code).toContain('createAssessment')
  })

  it('create endpoint reads form data', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/index.ts'), 'utf-8')
    expect(code).toContain('request.formData()')
  })

  it('update endpoint handles status transitions', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('transition_status')
    expect(code).toContain('updateAssessmentStatus')
  })

  it('update endpoint handles transcript upload', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('uploadTranscript')
    expect(code).toContain('transcript')
  })

  it('update endpoint handles extraction JSON', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('extraction')
  })

  it('update endpoint stores extraction result directly', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('extraction')
    expect(code).toContain('updateAssessment')
  })

  it('endpoints verify admin session', () => {
    const createCode = readFileSync(resolve('src/pages/api/admin/assessments/index.ts'), 'utf-8')
    const updateCode = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(createCode).toContain("session.role !== 'admin'")
    expect(updateCode).toContain("session.role !== 'admin'")
  })

  it('transcript endpoint verifies admin session', () => {
    const code = readFileSync(
      resolve('src/pages/api/admin/assessments/[id]/transcript.ts'),
      'utf-8'
    )
    expect(code).toContain("session.role !== 'admin'")
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
