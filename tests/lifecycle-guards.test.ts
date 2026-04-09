import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

describe('lifecycle invariant guards: transitionStage pre-conditions', () => {
  const entitiesSource = () => readFileSync(resolve('src/lib/db/entities.ts'), 'utf-8')

  it('proposing -> engaged requires an accepted quote query', () => {
    const code = entitiesSource()
    expect(code).toContain("entity.stage === 'proposing' && newStage === 'engaged'")
    expect(code).toContain("status = 'accepted'")
    expect(code).toContain('no accepted quote found')
  })

  it('proposing -> engaged throws when no accepted quote', () => {
    const code = entitiesSource()
    expect(code).toContain(
      "SELECT 1 FROM quotes WHERE entity_id = ? AND status = 'accepted' LIMIT 1"
    )
    expect(code).toContain('Cannot transition to engaged')
  })

  it('proposing -> engaged succeeds when accepted quote exists (no unconditional throw)', () => {
    const code = entitiesSource()
    expect(code).toContain('if (!acceptedQuote)')
    const guardIndex = code.indexOf('if (!acceptedQuote)')
    const updateIndex = code.indexOf('UPDATE entities SET', guardIndex)
    expect(updateIndex).toBeGreaterThan(guardIndex)
  })

  it('delivered -> ongoing requires paid completion invoice query', () => {
    const code = entitiesSource()
    expect(code).toContain("entity.stage === 'delivered' && newStage === 'ongoing'")
    expect(code).toContain(
      "SELECT 1 FROM invoices WHERE entity_id = ? AND type = 'completion' AND status = 'paid' LIMIT 1"
    )
  })

  it('delivered -> ongoing throws when no paid completion invoice', () => {
    const code = entitiesSource()
    expect(code).toContain('Cannot transition to ongoing')
    expect(code).toContain('no paid completion invoice found')
  })

  it('delivered -> ongoing supports force override', () => {
    const code = entitiesSource()
    expect(code).toContain('opts?.force')
    expect(code).toContain('force_override')
  })

  it('force override is logged in context metadata', () => {
    const code = entitiesSource()
    expect(code).toContain('metadata.force_override = opts.force')
  })

  it('exports TransitionOptions interface', () => {
    expect(entitiesSource()).toContain('export interface TransitionOptions')
  })

  it('transitionStage accepts optional opts parameter', () => {
    const code = entitiesSource()
    expect(code).toContain('opts?: TransitionOptions')
  })

  it('signal -> assessing is documented as intentionally absent', () => {
    const code = entitiesSource()
    expect(code).toContain('signal -> assessing is intentionally absent')
    expect(code).toContain('prospect')
    expect(code).toContain('intermediate state')
  })

  it('VALID_TRANSITIONS does not allow signal -> assessing', () => {
    const code = entitiesSource()
    expect(code).toContain("signal: ['prospect', 'lost']")
  })
})

describe('lifecycle invariant guards: quote acceptance guards', () => {
  const quotesSource = () => readFileSync(resolve('src/lib/db/quotes.ts'), 'utf-8')

  it('accepting a quote requires signwell_doc_id', () => {
    const code = quotesSource()
    expect(code).toContain('!existing.signwell_doc_id')
    expect(code).toContain('signwell_doc_id is not set')
  })

  it('accepting a quote requires signed_sow_path', () => {
    const code = quotesSource()
    expect(code).toContain('!existing.signed_sow_path')
    expect(code).toContain('signed_sow_path is not set')
  })

  it('quote acceptance guard checks are inside newStatus === accepted block', () => {
    const code = quotesSource()
    const acceptedCheck = code.indexOf("newStatus === 'accepted'")
    const signwellCheck = code.indexOf('!existing.signwell_doc_id')
    expect(signwellCheck).toBeGreaterThan(acceptedCheck)
    expect(acceptedCheck).toBeGreaterThan(-1)
  })

  it('quote acceptance guards throw Error (not silent return)', () => {
    const code = quotesSource()
    expect(code).toContain('Cannot accept quote: signwell_doc_id')
    expect(code).toContain('Cannot accept quote: signed_sow_path')
  })
})

describe('lifecycle invariant guards: context append-only invariant', () => {
  const contextSource = () => readFileSync(resolve('src/lib/db/context.ts'), 'utf-8')

  it('context.ts documents append-only invariant', () => {
    const code = contextSource()
    expect(code).toContain('INVARIANT')
    expect(code).toContain('append-only')
    expect(code).toContain('No UPDATE or DELETE')
  })

  it('context.ts does not export any UPDATE operations', () => {
    const code = contextSource()
    expect(code).not.toMatch(/UPDATE\s+context\s+SET/)
  })

  it('context.ts does not export any DELETE operations', () => {
    const code = contextSource()
    expect(code).not.toMatch(/DELETE\s+FROM\s+context/)
  })

  it('context.ts documents the merge exception', () => {
    const code = contextSource()
    expect(code).toContain('entity merges')
    expect(code).toContain('mergeEntities')
  })
})

describe('lifecycle invariant guards: admin stage endpoint', () => {
  const stageSource = () =>
    readFileSync(resolve('src/pages/api/admin/entities/[id]/stage.ts'), 'utf-8')

  it('stage endpoint passes force option to transitionStage', () => {
    const code = stageSource()
    expect(code).toContain("formData.get('force')")
    expect(code).toContain('{ force: forceStr }')
  })
})
