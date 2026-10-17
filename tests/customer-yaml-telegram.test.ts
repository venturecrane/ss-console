/**
 * `telegram:` block validation, including the deprecated `allowed_users` alias.
 *
 * The defect this pins: seats authored `telegram.allowed_users`, but the
 * validator and the overlay materializer read only `enabled` + `allow_from`, so
 * the authored allowlist was silently ignored and the Fly secret was the only
 * gate. Phase 1 reads `allowed_users` as an alias of `allow_from` (union) and
 * rejects every other unknown key. The overlay half of the same contract lives in
 * hermes-smd-overlay tests/test_bootstrap_translate.py.
 *
 * Falsifiers: the "alias satisfies enabled" and "alias entries are type-checked"
 * cases both FAIL if `allowed_users` is ignored again.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import {
  checkTelegram,
  telegramDeprecationNotes,
} from '../src/lib/operator/customer-yaml/sections-telegram'
import type { ValidationError } from '../src/lib/operator/customer-yaml/types'

function run(telegram: unknown): ValidationError[] {
  const errors: ValidationError[] = []
  checkTelegram({ telegram }, errors)
  return errors
}

describe('telegram block', () => {
  it('accepts the canonical form', () => {
    expect(run({ enabled: true, allow_from: ['7367659986'], require_mention: false })).toEqual([])
  })

  it('enabled with no allowlist fails closed', () => {
    const errors = run({ enabled: true })
    expect(errors.map((e) => e.code)).toEqual(['MissingField'])
  })

  it('FALSIFIER: allowed_users alone satisfies an enabled block (alias is read)', () => {
    expect(run({ enabled: true, allowed_users: ['7367659986'] })).toEqual([])
  })

  it('FALSIFIER: allowed_users entries are type-checked like allow_from', () => {
    const errors = run({ allowed_users: ['@scott'] })
    expect(errors.map((e) => e.path)).toEqual(['telegram.allowed_users[0]'])
  })

  it('allowed_users must be a list and non-empty when it carries the block', () => {
    expect(run({ allowed_users: 'x' }).map((e) => e.code)).toEqual(['TypeMismatch'])
    expect(run({ allowed_users: [] }).map((e) => e.code)).toEqual(['MissingField'])
  })

  it('both lists may be authored together (union)', () => {
    expect(run({ enabled: true, allow_from: ['1'], allowed_users: ['1', '2'] })).toEqual([])
  })

  it('rejects any other unknown key', () => {
    const errors = run({ enabled: true, allow_from: ['1'], allowed_user: ['2'] })
    expect(errors.map((e) => [e.code, e.path])).toEqual([
      ['UnknownTelegramField', 'telegram.allowed_user'],
    ])
  })

  it('allowed_users produces a deprecation note; the canonical form does not', () => {
    expect(telegramDeprecationNotes({ telegram: { allowed_users: ['1'] } })).toHaveLength(1)
    expect(telegramDeprecationNotes({ telegram: { enabled: true, allow_from: ['1'] } })).toEqual([])
    expect(telegramDeprecationNotes({})).toEqual([])
  })

  it('the shipped scott seat telegram block validates', () => {
    const root = parseYaml(
      readFileSync(resolve('operator/customers/scott/customer.yaml'), 'utf8')
    ) as Record<string, unknown>
    expect(run(root['telegram'])).toEqual([])
  })
})
