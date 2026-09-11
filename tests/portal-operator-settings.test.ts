/**
 * Tests for the Operator settings contracts
 * (src/lib/portal/operator/settings.ts): the pure customer.yaml
 * projections the facet resolvers consume (trust ceilings, skill
 * toggles, connector rows). The voice-sample family was removed
 * 2026-07-15 (inert chrome close-out).
 */

import { describe, it, expect } from 'vitest'
import { connectorRowsFromCustomerYaml } from '../src/lib/portal/operator/settings'

describe('connectorRowsFromCustomerYaml', () => {
  it('returns an empty list for null / non-object input', () => {
    expect(connectorRowsFromCustomerYaml(null)).toEqual([])
    expect(connectorRowsFromCustomerYaml(undefined)).toEqual([])
    expect(connectorRowsFromCustomerYaml('string')).toEqual([])
    expect(connectorRowsFromCustomerYaml(42)).toEqual([])
  })

  it('returns an empty list for an empty connectors map', () => {
    expect(connectorRowsFromCustomerYaml({})).toEqual([])
  })

  it('projects each capability into a row sorted by name', () => {
    const rows = connectorRowsFromCustomerYaml({
      PracticeManagement: { adapter: 'filevine' },
      Email: { adapter: 'microsoft-graph' },
      Calendar: { adapter: 'microsoft-graph' },
    })
    expect(rows.map((r) => r.capabilityName)).toEqual(['Calendar', 'Email', 'PracticeManagement'])
    expect(rows.every((r) => r.health === 'unconfigured')).toBe(true)
    expect(rows.every((r) => r.reconsentRequired === false)).toBe(true)
  })

  it('tolerates entries without an adapter string', () => {
    const rows = connectorRowsFromCustomerYaml({
      Email: {},
      Calendar: { adapter: 42 },
    })
    expect(rows).toHaveLength(2)
    expect(rows.every((r) => r.adapter === '')).toBe(true)
  })

  it('skips non-object entries', () => {
    const rows = connectorRowsFromCustomerYaml({
      Email: null,
      Calendar: 'string',
      PracticeManagement: { adapter: 'filevine' },
    })
    expect(rows).toHaveLength(1)
    expect(rows[0].capabilityName).toBe('PracticeManagement')
  })
})
