/**
 * `logic_in_code:` (the per-seat switches of ADR 0104), console side. The on-box
 * twin is `_validate_logic_in_code`; the parity fixtures pin the two together.
 */
import { describe, expect, it } from 'vitest'
import { checkLogicInCode } from '../src/lib/operator/customer-yaml/sections-logic-in-code'
import type { ValidationError } from '../src/lib/operator/customer-yaml/types'

function check(value: unknown): { parsed: unknown; errors: ValidationError[] } {
  const errors: ValidationError[] = []
  const parsed = checkLogicInCode({ logic_in_code: value }, errors)
  return { parsed, errors }
}

describe('logic_in_code', () => {
  it('is absent when unauthored, which every reader takes as observe', () => {
    const errors: ValidationError[] = []
    expect(checkLogicInCode({}, errors)).toBeNull()
    expect(check(null).parsed).toBeNull()
    expect(errors).toEqual([])
  })

  it('carries each authored switch', () => {
    const { parsed, errors } = check({
      printed_source_check: 'enforce',
      post_record_derivation: 'observe',
      conflict_fence: 'enforce',
      fixed_wording: 'observe',
      status_from_events: 'enforce',
      library_readback: 'observe',
    })
    expect(errors).toEqual([])
    expect(parsed).toEqual({
      printed_source_check: 'enforce',
      post_record_derivation: 'observe',
      conflict_fence: 'enforce',
      fixed_wording: 'observe',
      status_from_events: 'enforce',
      library_readback: 'observe',
    })
  })

  it('treats a key with no value as absent, as the readers do', () => {
    const { parsed, errors } = check({ conflict_fence: null })
    expect(errors).toEqual([])
    expect(parsed).toEqual({})
  })

  it.each(['off', 'Observe', true, 1, ['observe']])('refuses the value %j', (value) => {
    const { errors } = check({ conflict_fence: value })
    expect(errors.map((e) => e.path)).toEqual(['logic_in_code.conflict_fence'])
  })

  it('refuses a misspelled switch, which would otherwise read as observe', () => {
    const { errors } = check({ printed_sauce_check: 'enforce' })
    expect(errors.map((e) => e.path)).toEqual(['logic_in_code.printed_sauce_check'])
  })

  it('refuses a block that is not a mapping', () => {
    const { parsed, errors } = check('enforce')
    expect(parsed).toBeNull()
    expect(errors.map((e) => e.code)).toEqual(['TypeMismatch'])
  })
})
