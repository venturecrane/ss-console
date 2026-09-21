/**
 * `firm_identity:` (the firm's letterhead) — console-side validation.
 *
 * The block is read by the Smokeball connector's renderer and printed as the
 * first-page letterhead of letter-class documents on the starter base. These
 * pin that every shipped customer.yaml carrying it validates (with the parsed
 * secret scan, since the values are digits-heavy), and that a malformed block is
 * refused rather than silently dropping a line from every letter.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml'

const CUSTOMERS = join(__dirname, '..', 'operator', 'customers')

function load(slug: string): { raw: string; parsed: Record<string, unknown> } {
  const raw = readFileSync(join(CUSTOMERS, slug, 'customer.yaml'), 'utf8')
  return { raw, parsed: parseYaml(raw) as Record<string, unknown> }
}

const authoredSlugs = readdirSync(CUSTOMERS).filter((slug) => {
  const p = join(CUSTOMERS, slug, 'customer.yaml')
  return existsSync(p) && /^firm_identity:/m.test(readFileSync(p, 'utf8'))
})

describe('firm_identity', () => {
  it('is authored on at least one shipped seat', () => {
    expect(authoredSlugs.length).toBeGreaterThan(0)
  })

  // validate() always runs the parsed-value secret scan; the raw-text pass is
  // not used here, matching scripts/validate-customer-yaml.ts (parsed pass
  // only); the raw pass flags unrelated long identifiers elsewhere in the file.
  it.each(authoredSlugs)(
    '%s validates with its firm_identity (parsed secret scan included)',
    (slug) => {
      const { parsed } = load(slug)
      const result = validate(parsed)
      if (!result.ok) throw new Error(JSON.stringify(result.errors, null, 2))
      const fi = parsed['firm_identity'] as Record<string, unknown>
      expect(typeof fi['name']).toBe('string')
    }
  )

  function withIdentity(value: unknown) {
    const { parsed } = load(authoredSlugs[0])
    return validate({ ...parsed, firm_identity: value })
  }

  function paths(value: unknown): string[] {
    const result = withIdentity(value)
    return result.ok ? [] : result.errors.map((e) => e.path)
  }

  it('is optional', () => {
    const { parsed } = load(authoredSlugs[0])
    const rest = { ...parsed }
    delete rest['firm_identity']
    expect(validate(rest).ok).toBe(true)
  })

  it('requires a name', () => {
    expect(paths({ street: '100 Example Way' })).toContain('firm_identity.name')
  })

  it('accepts a name alone', () => {
    expect(withIdentity({ name: 'ACME LAW, LLP' }).ok).toBe(true)
  })

  it('refuses an unknown key, a non-string, an empty string, and a non-mapping', () => {
    expect(paths({ name: 'ACME', city_state: 'Springfield' })).toContain('firm_identity.city_state')
    expect(paths({ name: 'ACME', phone: 5550100 })).toContain('firm_identity.phone')
    expect(paths({ name: 'ACME', fax: '  ' })).toContain('firm_identity.fax')
    expect(paths('ACME LAW')).toContain('firm_identity')
  })
})
