import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { isLikelyBusinessName, resolveBusinessName } from '../workers/new-business/src/soda'

interface PhoenixRow {
  PER_NUM: string
  PERMIT_NAME: string | null
  PROFESS_NAME: string | null
  PER_TYPE_DESC: string | null
  SCOPE_DESC: string | null
}

const fixturePath = join(__dirname, 'fixtures', 'phoenix-permits.json')
const phoenixRows: PhoenixRow[] = JSON.parse(readFileSync(fixturePath, 'utf8'))

describe('isLikelyBusinessName', () => {
  describe('junk permit descriptions (should reject)', () => {
    const junk = [
      'REPIPE BUILDING SEWER',
      'WTR FILTERS REPLACEMENT & ADD SOFTNERS',
      'ITULE COOLER EXPANSION',
      'REPLACE AC UNIT',
      'REROOFING',
      'ADD PATIO COVER',
      'REMODEL KITCHEN',
      'WTR SERVICE LINE',
      'TENANT IMPROVEMENT',
      'BUILDOUT FOR OFFICE',
    ]
    for (const n of junk) {
      it(`rejects "${n}"`, () => {
        expect(isLikelyBusinessName(n)).toBe(false)
      })
    }
  })

  describe('real businesses (should accept)', () => {
    const real = [
      'FIREHOUSE SOLUTIONS LLC',
      'INTY SOLAR LLC',
      'Phoenix Plumbing Co',
      'ABC Dental LLC',
      'REMODEL CONSTRUCTION LLC', // "REMODEL" substring — survives via entity suffix
      'Metro Fire Equipment',
      'Positive Synergy LLC',
      'Valley Fire Sprinkler Inc',
      'SmithCraft & Co',
      'Acme Electric Inc',
    ]
    for (const n of real) {
      it(`accepts "${n}"`, () => {
        expect(isLikelyBusinessName(n)).toBe(true)
      })
    }
  })

  describe('edge cases', () => {
    it('rejects literal "OWNER"', () => {
      expect(isLikelyBusinessName('OWNER')).toBe(false)
    })

    it('rejects very short strings', () => {
      expect(isLikelyBusinessName('')).toBe(false)
      expect(isLikelyBusinessName('A')).toBe(false)
    })

    it('accepts a sentence-case name with no entity suffix', () => {
      // These survive because they're not all-caps trade verbs
      expect(isLikelyBusinessName('SmithCraft')).toBe(true)
    })
  })
})

describe('resolveBusinessName — Phoenix corpus (200 real rows)', () => {
  it('never returns PERMIT_NAME as the business name', () => {
    const pipelined = phoenixRows
      .map((r) => resolveBusinessName('phoenix', r as unknown as Record<string, unknown>))
      .filter((r): r is { name: string; role: 'business' | 'contractor' | 'unknown' } => r !== null)
    // None of the resolved names should match a PERMIT_NAME value from any row
    const permitNames = new Set(
      phoenixRows.map((r) => r.PERMIT_NAME).filter((n): n is string => n != null)
    )
    for (const r of pipelined) {
      expect(permitNames.has(r.name)).toBe(false)
    }
  })

  it('tags Phoenix names with role=contractor', () => {
    const sample = phoenixRows.find((r) => r.PROFESS_NAME && r.PROFESS_NAME !== 'OWNER')
    if (!sample) throw new Error('fixture has no usable row')
    const resolved = resolveBusinessName('phoenix', sample as unknown as Record<string, unknown>)
    expect(resolved?.role).toBe('contractor')
  })

  it('skips OWNER rows', () => {
    const ownerRow = phoenixRows.find((r) => r.PROFESS_NAME === 'OWNER')
    if (ownerRow) {
      expect(
        resolveBusinessName('phoenix', ownerRow as unknown as Record<string, unknown>)
      ).toBeNull()
    }
  })

  it('skips rows with empty profess_name', () => {
    const emptyRow = phoenixRows.find((r) => !r.PROFESS_NAME)
    if (emptyRow) {
      expect(
        resolveBusinessName('phoenix', emptyRow as unknown as Record<string, unknown>)
      ).toBeNull()
    }
  })

  it('acceptance rate on real profess_name values is >= 90%', () => {
    const eligible = phoenixRows.filter(
      (r) => r.PROFESS_NAME && r.PROFESS_NAME.trim() && r.PROFESS_NAME.toUpperCase() !== 'OWNER'
    )
    const accepted = eligible.filter((r) =>
      resolveBusinessName('phoenix', r as unknown as Record<string, unknown>)
    )
    const acceptanceRate = accepted.length / eligible.length
    // Expect >90% of real PROFESS_NAME values to pass — these are real
    // business names (albeit contractor businesses), so false-positive
    // rejection should be rare.
    expect(acceptanceRate).toBeGreaterThanOrEqual(0.9)
  })
})

describe('resolveBusinessName — per-source behavior', () => {
  it('scottsdale_permits always returns null (no business name field)', () => {
    expect(resolveBusinessName('scottsdale_permits', { address: '123 Main St' })).toBeNull()
  })

  it('scottsdale_licenses uses Company field', () => {
    expect(resolveBusinessName('scottsdale_licenses', { Company: 'Desert Dental LLC' })).toEqual({
      name: 'Desert Dental LLC',
      role: 'business',
    })
  })

  it('mesa prefers application_name over applicant', () => {
    expect(
      resolveBusinessName('mesa', {
        application_name: 'Real Business LLC',
        applicant: 'Contractor Co',
      })
    ).toEqual({ name: 'Real Business LLC', role: 'unknown' })
  })

  it('mesa falls back to applicant with role=contractor', () => {
    expect(
      resolveBusinessName('mesa', {
        application_name: '',
        applicant: 'Phoenix Plumbing LLC',
      })
    ).toEqual({ name: 'Phoenix Plumbing LLC', role: 'contractor' })
  })

  it('tempe uses ProjectName only, never Description', () => {
    // The bug this replaces: Description was used as fallback, producing
    // names like "REPIPE BUILDING SEWER".
    expect(
      resolveBusinessName('tempe', {
        ProjectName: '',
        Description: 'REPIPE BUILDING SEWER',
      })
    ).toBeNull()
    expect(
      resolveBusinessName('tempe', {
        ProjectName: 'Tempe Cafe LLC',
        Description: 'Tenant improvement',
      })
    ).toEqual({ name: 'Tempe Cafe LLC', role: 'unknown' })
  })
})
