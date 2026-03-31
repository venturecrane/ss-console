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

  it('supports optional client_id filter in listAssessments', () => {
    const code = source()
    expect(code).toContain("'client_id = ?'")
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

  it('defines valid transitions: scheduled -> completed | disqualified', () => {
    const code = source()
    // The VALID_TRANSITIONS object should allow these transitions
    expect(code).toContain("scheduled: ['completed', 'disqualified']")
  })

  it('defines valid transitions: completed -> disqualified | converted', () => {
    const code = source()
    expect(code).toContain("completed: ['disqualified', 'converted']")
  })

  it('disqualified and converted are terminal states', () => {
    const code = source()
    expect(code).toContain('disqualified: []')
    expect(code).toContain('converted: []')
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

  it('update endpoint handles problem mapping via checkboxes', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('PROBLEM_IDS')
    expect(code).toContain('selectedProblems')
  })

  it('update endpoint handles disqualification flags', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('dq_not_decision_maker')
    expect(code).toContain('dq_scope_exceeds_sprint')
    expect(code).toContain('dq_no_tech_baseline')
    expect(code).toContain('dq_no_champion')
    expect(code).toContain('dq_books_behind')
    expect(code).toContain('dq_no_willingness_to_change')
  })

  it('update endpoint implements financial prerequisite gate (OQ-004)', () => {
    const code = readFileSync(resolve('src/pages/api/admin/assessments/[id].ts'), 'utf-8')
    expect(code).toContain('financial_confirmed')
    expect(code).toContain('financial_blindness')
    expect(code).toContain('financial_prerequisite')
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

describe('assessments: create form page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/assessments/new.astro'), 'utf-8')

  it('create page exists', () => {
    expect(existsSync(resolve('src/pages/admin/clients/[id]/assessments/new.astro'))).toBe(true)
  })

  it('form posts to /api/admin/assessments', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('action="/api/admin/assessments"')
  })

  it('includes hidden client_id field', () => {
    expect(source()).toContain('name="client_id"')
  })

  it('includes scheduled_at field', () => {
    expect(source()).toContain('name="scheduled_at"')
  })

  it('includes notes field', () => {
    expect(source()).toContain('name="notes"')
  })

  it('loads client data for display', () => {
    expect(source()).toContain('getClient')
  })

  it('has breadcrumb navigation', () => {
    const code = source()
    expect(code).toContain('/admin/clients')
    expect(code).toContain('client.business_name')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })
})

describe('assessments: detail/edit page', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/assessments/[assessmentId].astro'), 'utf-8')

  it('detail page exists', () => {
    expect(
      existsSync(resolve('src/pages/admin/clients/[id]/assessments/[assessmentId].astro'))
    ).toBe(true)
  })

  it('loads assessment via getAssessment', () => {
    expect(source()).toContain('getAssessment')
  })

  it('loads client via getClient for breadcrumb', () => {
    expect(source()).toContain('getClient')
  })

  it('form posts to /api/admin/assessments/:id', () => {
    const code = source()
    expect(code).toContain('method="POST"')
    expect(code).toContain('/api/admin/assessments/${assessment.id}')
  })

  it('uses multipart form data for file upload', () => {
    expect(source()).toContain('enctype="multipart/form-data"')
  })

  it('includes transcript file upload input', () => {
    const code = source()
    expect(code).toContain('type="file"')
    expect(code).toContain('name="transcript"')
    expect(code).toContain('.txt')
  })

  it('shows transcript download link when uploaded', () => {
    const code = source()
    expect(code).toContain('Transcript uploaded')
    expect(code).toContain('Download')
    expect(code).toContain('/transcript')
  })

  it('includes extraction JSON textarea', () => {
    const code = source()
    expect(code).toContain('name="extraction"')
    expect(code).toContain('Extraction JSON')
  })

  it('includes problem mapping checkboxes for all 6 problems', () => {
    const code = source()
    expect(code).toContain('PROBLEM_IDS')
    expect(code).toContain('PROBLEM_LABELS')
    expect(code).toContain('problem_${problemId}')
  })

  it('includes champion name and role fields', () => {
    const code = source()
    expect(code).toContain('name="champion_name"')
    expect(code).toContain('name="champion_role"')
  })

  it('includes hard disqualification flag checkboxes', () => {
    const code = source()
    expect(code).toContain('name="dq_not_decision_maker"')
    expect(code).toContain('name="dq_scope_exceeds_sprint"')
    expect(code).toContain('name="dq_no_tech_baseline"')
  })

  it('includes soft disqualification flag checkboxes', () => {
    const code = source()
    expect(code).toContain('name="dq_no_champion"')
    expect(code).toContain('name="dq_books_behind"')
    expect(code).toContain('name="dq_no_willingness_to_change"')
  })

  it('shows status transition buttons', () => {
    const code = source()
    expect(code).toContain('Status Transition')
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('transition_status')
    expect(code).toContain('new_status')
  })

  it('displays current status badge', () => {
    const code = source()
    expect(code).toContain('ASSESSMENT_STATUSES')
    expect(code).toContain('statusColorMap')
  })

  it('shows success message after save', () => {
    const code = source()
    expect(code).toContain("get('saved')")
    expect(code).toContain('Assessment updated successfully')
  })

  it('implements financial prerequisite soft warning (OQ-004)', () => {
    const code = source()
    expect(code).toContain('financial_prerequisite')
    expect(code).toContain('Financial Prerequisite Warning')
    expect(code).toContain('OQ-004')
    expect(code).toContain('financial_confirmed')
    expect(code).toContain('Confirm and Convert')
  })

  it('shows OQ-004 notice when books_behind is flagged', () => {
    const code = source()
    expect(code).toContain('booksBehind')
    expect(code).toContain('OQ-004 Notice')
  })

  it('is not indexed by search engines', () => {
    expect(source()).toContain('noindex')
  })

  it('has breadcrumb back to client', () => {
    const code = source()
    expect(code).toContain('/admin/clients/${client.id}')
    expect(code).toContain('client.business_name')
  })
})

describe('assessments: client detail page integration', () => {
  const source = () => readFileSync(resolve('src/pages/admin/clients/[id].astro'), 'utf-8')

  it('client detail page imports listAssessments', () => {
    expect(source()).toContain('listAssessments')
  })

  it('client detail page loads assessments for this client', () => {
    const code = source()
    expect(code).toContain('listAssessments')
    expect(code).toContain('assessments')
  })

  it('client detail page has assessments section', () => {
    const code = source()
    expect(code).toContain('Assessments')
    expect(code).toContain('New Assessment')
  })

  it('client detail page links to new assessment form', () => {
    const code = source()
    expect(code).toContain('/assessments/new')
  })

  it('client detail page links to assessment detail', () => {
    const code = source()
    expect(code).toContain('/assessments/${a.id}')
  })

  it('shows assessment status in the list', () => {
    const code = source()
    expect(code).toContain('ASSESSMENT_STATUSES')
    expect(code).toContain('a.status')
  })

  it('shows empty state when no assessments', () => {
    const code = source()
    expect(code).toContain('No assessments yet')
    expect(code).toContain('Schedule the first assessment')
  })
})
