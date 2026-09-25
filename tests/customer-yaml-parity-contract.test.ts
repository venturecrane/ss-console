/**
 * Cross-repo validator parity contract (ADR 0044).
 *
 * The console TS validator and the on-box Python validator
 * (hermes-smd-overlay/bootstrap/validate.py) gate the SAME customer.yaml at two
 * points in the apply path: the console blesses an edit at authoring time, the
 * broker re-validates the pulled file on-box before writing it to the volume. If
 * they disagree, a config the console accepted could be rejected on apply, or a
 * danger the console would catch could land on the Machine if its path is
 * bypassed.
 *
 * This test pins the agreement from the TS side. The two repos hold the manifest
 * with DATA-identical fixtures (each repo formats the file per its own tooling —
 * this repo runs prettier on commit — so raw bytes differ, but the fixture DATA
 * does not). Each repo's test asserts ITS validator classifies every fixture as
 * the manifest's `expect`, and pins a canonical-content hash of the `fixtures`
 * array (formatting-independent) that must equal the constant in the overlay's
 * test_validator_parity_contract.py. A one-sided change to the fixture data
 * trips it.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createHash } from 'node:crypto'
import { parse as parseYaml } from 'yaml'
import { validate } from '../src/lib/operator/customer-yaml'

const MANIFEST_PATH = fileURLToPath(
  new URL(
    '../src/lib/operator/customer-yaml/__contract__/validator_parity_fixtures.json',
    import.meta.url
  )
)

// Canonical-content hash of the `fixtures` array (sorted keys, compact
// separators) — independent of file formatting, so prettier here cannot break
// it. MUST equal _PINNED_CONTENT_SHA256 in the overlay's
// test_validator_parity_contract.py. Update in BOTH repos when fixture data changes.
const PINNED_CONTENT_SHA256 = '0c1cabfb48475b0c3463d99773bc968068dd5e658975ca9e272ce07a971aa9d2'

interface Fixture {
  name: string
  expect: 'accept' | 'reject'
  note: string
  yaml: string
}

const rawManifest = readFileSync(MANIFEST_PATH)
const manifest = JSON.parse(new TextDecoder().decode(rawManifest)) as {
  fixtures: Fixture[]
}

/** Stable, formatting-independent serialization: sort object keys recursively,
 * compact separators. Matches Python's json.dumps(sort_keys, separators=(',',':')). */
function stableStringify(v: unknown): string {
  if (Array.isArray(v)) return '[' + v.map(stableStringify).join(',') + ']'
  if (v !== null && typeof v === 'object') {
    return (
      '{' +
      Object.keys(v as Record<string, unknown>)
        .sort()
        .map((k) => JSON.stringify(k) + ':' + stableStringify((v as Record<string, unknown>)[k]))
        .join(',') +
      '}'
    )
  }
  return JSON.stringify(v)
}

describe('validator parity contract (ADR 0044)', () => {
  it('fixture content hash matches the digest shared with the overlay test', () => {
    const actual = createHash('sha256').update(stableStringify(manifest.fixtures)).digest('hex')
    expect(actual).toBe(PINNED_CONTENT_SHA256)
  })

  for (const fixture of manifest.fixtures) {
    it(`${fixture.name} → ${fixture.expect} (${fixture.note})`, () => {
      const parsed = parseYaml(fixture.yaml)
      const result = validate(parsed, { rawText: fixture.yaml })
      expect(result.ok).toBe(fixture.expect === 'accept')
    })
  }
})
