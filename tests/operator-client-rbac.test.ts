import { describe, it, expect } from 'vitest'
import {
  clientRolePermits,
  isClientRole,
  CLIENT_ROLES,
} from '../src/lib/portal/operator/client-rbac'
import {
  SWITCHABLE_AUTHORITY_DOMAINS,
  SMD_ONLY_AUTHORITY_DOMAINS,
} from '../src/lib/operator/authority'

describe('client-rbac: role vocabulary', () => {
  it('exposes exactly principal | staff | compliance (no legacy operator)', () => {
    expect([...CLIENT_ROLES]).toEqual(['principal', 'staff', 'compliance'])
    expect(isClientRole('operator')).toBe(false)
    expect(isClientRole('staff')).toBe(true)
    expect(isClientRole('principal')).toBe(true)
    expect(isClientRole('compliance')).toBe(true)
    expect(isClientRole('captain')).toBe(false)
  })
})

describe('client-rbac: principal operates every switchable domain', () => {
  for (const domain of SWITCHABLE_AUTHORITY_DOMAINS) {
    it(`principal permits ${domain}`, () => {
      expect(clientRolePermits(['principal'], domain)).toBe(true)
    })
  }
})

describe('client-rbac: staff operates only runtime + observability', () => {
  it('permits runtime and observability', () => {
    expect(clientRolePermits(['staff'], 'runtime')).toBe(true)
    expect(clientRolePermits(['staff'], 'observability')).toBe(true)
  })
  it('does NOT permit configuration / trust / connectors / memory / people_access / compliance', () => {
    for (const domain of [
      'configuration',
      'trust',
      'connectors',
      'memory',
      'people_access',
      'compliance',
    ] as const) {
      expect(clientRolePermits(['staff'], domain)).toBe(false)
    }
  })
})

describe('client-rbac: compliance operates only the compliance domain (evidence export)', () => {
  it('permits compliance', () => {
    expect(clientRolePermits(['compliance'], 'compliance')).toBe(true)
  })
  it('does NOT permit any other switchable domain (read-only by definition)', () => {
    for (const domain of SWITCHABLE_AUTHORITY_DOMAINS) {
      if (domain === 'compliance') continue
      expect(clientRolePermits(['compliance'], domain)).toBe(false)
    }
  })
})

describe('client-rbac: composition and fail-closed behavior', () => {
  it('SMD-only domains (provisioning, cost) are never client-operable for any role', () => {
    for (const role of CLIENT_ROLES) {
      for (const domain of SMD_ONLY_AUTHORITY_DOMAINS) {
        expect(clientRolePermits([role], domain)).toBe(false)
      }
    }
  })

  it('multiple roles union their operability', () => {
    // staff cannot operate compliance; compliance cannot operate runtime;
    // holding both grants both.
    expect(clientRolePermits(['staff', 'compliance'], 'compliance')).toBe(true)
    expect(clientRolePermits(['staff', 'compliance'], 'runtime')).toBe(true)
  })

  it('unknown role strings contribute nothing', () => {
    expect(clientRolePermits(['operator'], 'runtime')).toBe(false)
    expect(clientRolePermits([''], 'runtime')).toBe(false)
    expect(clientRolePermits([], 'runtime')).toBe(false)
  })

  it('unknown / non-switchable domains are never operable', () => {
    expect(clientRolePermits(['principal'], 'not_a_domain')).toBe(false)
    expect(clientRolePermits(['principal'], 'cost')).toBe(false)
  })
})
