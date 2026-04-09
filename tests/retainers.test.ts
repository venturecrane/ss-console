import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('retainers: data access layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/retainers.ts'), 'utf-8')

  it('retainers.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/retainers.ts'))).toBe(true)
  })

  it('exports getRetainer function', () => {
    expect(source()).toContain('export async function getRetainer')
  })

  it('exports createRetainer function', () => {
    expect(source()).toContain('export async function createRetainer')
  })

  it('exports listActiveRetainers function', () => {
    expect(source()).toContain('export async function listActiveRetainers')
  })

  it('exports listRetainersForEntity function', () => {
    expect(source()).toContain('export async function listRetainersForEntity')
  })

  it('exports updateRetainerStatus function', () => {
    expect(source()).toContain('export async function updateRetainerStatus')
  })

  it('exports recordRetainerBilling function', () => {
    expect(source()).toContain('export async function recordRetainerBilling')
  })

  it('uses parameterized queries', () => {
    const code = source()
    expect(code).toContain('.bind(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('generates UUIDs for primary keys', () => {
    expect(source()).toContain('crypto.randomUUID()')
  })

  it('exports VALID_TRANSITIONS with correct state machine', () => {
    const code = source()
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toMatch(/cancelled:\s*\[\]/)
  })

  it('createRetainer sets next_billing_date = start_date', () => {
    expect(source()).toContain('data.start_date, // next_billing_date = start_date')
  })

  it('all queries are org-scoped', () => {
    const code = source()
    expect(code).toContain('WHERE id = ? AND org_id = ?')
    expect(code).toContain("WHERE org_id = ? AND status = 'active'")
    expect(code).toContain('WHERE org_id = ? AND entity_id = ?')
  })
})

describe('retainers: API endpoint', () => {
  const apiSource = () => readFileSync(resolve('src/pages/api/admin/retainers/index.ts'), 'utf-8')

  it('API index.ts exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/retainers/index.ts'))).toBe(true)
  })

  it('imports createRetainer from DAL', () => {
    expect(apiSource()).toContain('createRetainer')
  })

  it('imports transitionStage from entities', () => {
    expect(apiSource()).toContain('transitionStage')
  })

  it('handles transition_to_ongoing for delivered entities', () => {
    const code = apiSource()
    expect(code).toContain("formData.get('transition_to_ongoing')")
    expect(code).toContain("'ongoing'")
  })

  it('enforces admin role', () => {
    expect(apiSource()).toContain("session.role !== 'admin'")
  })
})

describe('retainers: status transition API', () => {
  it('status API exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/retainers/[id].ts'))).toBe(true)
  })

  it('handles transition_status action', () => {
    const code = readFileSync(resolve('src/pages/api/admin/retainers/[id].ts'), 'utf-8')
    expect(code).toContain("action === 'transition_status'")
  })
})

describe('retainers: admin form page', () => {
  const formSource = () =>
    readFileSync(resolve('src/pages/admin/entities/[id]/retainer.astro'), 'utf-8')

  it('retainer form page exists', () => {
    expect(existsSync(resolve('src/pages/admin/entities/[id]/retainer.astro'))).toBe(true)
  })

  it('restricts to delivered/ongoing entities', () => {
    expect(formSource()).toContain("['delivered', 'ongoing'].includes(entity.stage)")
  })

  it('form posts to /api/admin/retainers', () => {
    expect(formSource()).toContain('action="/api/admin/retainers"')
  })

  it('includes transition_to_ongoing checkbox', () => {
    expect(formSource()).toContain('name="transition_to_ongoing"')
  })

  it('hides create form when active retainer exists', () => {
    expect(formSource()).toContain('!activeRetainer')
  })
})

describe('retainers: entity detail page integration', () => {
  const detailSource = () => readFileSync(resolve('src/pages/admin/entities/[id].astro'), 'utf-8')

  it('imports listRetainersForEntity', () => {
    expect(detailSource()).toContain('listRetainersForEntity')
  })

  it('fetches retainers in Promise.all', () => {
    expect(detailSource()).toContain('listRetainersForEntity(env.DB, session.orgId, entityId)')
  })

  it('shows retainer summary card', () => {
    expect(detailSource()).toContain('Retainers ({retainers.length})')
  })

  it('shows create retainer button', () => {
    expect(detailSource()).toContain('showRetainerButton')
    expect(detailSource()).toContain('Create Retainer')
  })

  it('links to retainer management page', () => {
    expect(detailSource()).toContain('/admin/entities/${entity.id}/retainer')
  })
})
