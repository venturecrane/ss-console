/**
 * The console's copy of "what a seat is for" (ADR 0100) agrees with the one
 * the seat runs: src/lib/operator/customer-yaml/sections-capabilities.ts
 * mirrors operator/hermes-smd-overlay/shared/capabilities.py.
 *
 * 1. TABLES. JOB_LANES and CONNECTOR_BACKENDS equal the Python's, read by the
 *    Python itself, and BLOCK_OWNERS equals the registry's `owner:` fields.
 * 2. PREDICATE. On every repo seat, the authored lanes and the Smokeball
 *    connector answer the same in both languages (the Python runs on the parsed
 *    document, so no PyYAML is needed).
 * 3. ENFORCEMENT. A capability's block on a seat that does not author the
 *    capability is refused by validate(); every repo seat still validates
 *    clean of that refusal.
 * 4. REQUEST CARDS. The lanes a card may name are exactly the authorable lanes.
 */

import { describe, expect, it } from 'vitest'
import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import {
  BLOCK_OWNERS,
  CONNECTOR_BACKENDS,
  JOB_LANES,
  authoredLanes,
  authorsConnector,
} from '../src/lib/operator/customer-yaml/sections-capabilities'
import { validate } from '../src/lib/operator/customer-yaml/validator'
import { JOB_LANES as CARD_LANES } from '../src/lib/operator/request-card'

const ROOT = resolve(__dirname, '..')
const SHARED = join(ROOT, 'operator/hermes-smd-overlay/shared')
const CUSTOMERS = join(ROOT, 'operator/customers')

const SEATS = readdirSync(CUSTOMERS)
  .filter((d) => existsSync(join(CUSTOMERS, d, 'customer.yaml')))
  .sort()

function python(program: string, input: string): string {
  return execFileSync('python3', ['-I', '-c', program, SHARED], { input, encoding: 'utf8' })
}

const PY_PRELUDE = 'import json, sys\nsys.path.insert(0, sys.argv[1])\nimport capabilities as c\n'

describe('capability tables', () => {
  it('JOB_LANES and CONNECTOR_BACKENDS equal shared/capabilities.py', () => {
    const out = JSON.parse(
      python(
        PY_PRELUDE +
          'print(json.dumps({"lanes": c.JOB_LANES, "connectors": {k: sorted(v) for k, v in c.CONNECTOR_BACKENDS.items()}}))',
        ''
      )
    )
    expect(out.lanes).toEqual(JOB_LANES)
    expect(Object.keys(out.lanes)).toEqual(Object.keys(JOB_LANES)) // order too
    expect(out.connectors).toEqual(
      Object.fromEntries(Object.entries(CONNECTOR_BACKENDS).map(([k, v]) => [k, [...v].sort()]))
    )
  })

  it("BLOCK_OWNERS equals the registry's owner fields", () => {
    const reg = parseYaml(
      readFileSync(join(ROOT, 'operator/contracts/customer-yaml-blocks.yaml'), 'utf8')
    ) as { top_level: Record<string, { owner?: string }> }
    const owned = Object.fromEntries(
      Object.entries(reg.top_level)
        .filter(([, spec]) => (spec.owner ?? 'core') !== 'core')
        .map(([name, spec]) => [name, spec.owner])
    )
    expect(BLOCK_OWNERS).toEqual(owned)
  })
})

describe.each(SEATS)('seat %s', (slug) => {
  const doc = parseYaml(readFileSync(join(CUSTOMERS, slug, 'customer.yaml'), 'utf8')) as Record<
    string,
    unknown
  >

  it('authors the same capabilities in TypeScript and Python', () => {
    const py = JSON.parse(
      python(
        PY_PRELUDE +
          'd = json.load(sys.stdin)\nprint(json.dumps({"lanes": c.authored_lanes(d), "smokeball": c.authors_connector(d, "smokeball")}))',
        JSON.stringify(doc)
      )
    )
    expect(authoredLanes(doc)).toEqual(py.lanes)
    expect(authorsConnector(doc, 'smokeball')).toBe(py.smokeball)
  })

  it('authors the owner of every capability block it authors', () => {
    const res = validate(doc)
    const refused = res.ok ? [] : res.errors.filter((e) => e.code === 'CapabilityNotAuthored')
    expect(refused).toEqual([])
  })
})

describe('enforcement', () => {
  const pilot = parseYaml(
    readFileSync(join(CUSTOMERS, 'pilot-smokeball', 'customer.yaml'), 'utf8')
  ) as Record<string, unknown>

  it('refuses a Smokeball block on a seat that binds no Smokeball connector', () => {
    // FALSIFIER: drop checkCapabilityOwnedBlocks from validateSections and this passes clean.
    const connectors = Object.fromEntries(
      Object.entries(pilot['connectors'] as Record<string, Record<string, unknown>>).map(
        ([k, v]) => [k, v['backend'] === 'mcp:smokeball' ? { ...v, enabled: false } : v]
      )
    )
    const doc = { ...pilot, connectors }
    expect(pilot['smokeball_layouts']).toBeDefined()
    const res = validate(doc)
    expect(res.ok).toBe(false)
    const refused = res.ok ? [] : res.errors.filter((e) => e.code === 'CapabilityNotAuthored')
    expect(refused.map((e) => e.path)).toEqual(['smokeball_layouts'])
  })
})

describe('request card lanes', () => {
  it('are exactly the lanes a seat can author', () => {
    expect([...CARD_LANES].sort()).toEqual(Object.keys(JOB_LANES).sort())
  })
})
