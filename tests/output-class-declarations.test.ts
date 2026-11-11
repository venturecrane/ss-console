/**
 * A declared spec expectation must be one somebody can actually satisfy.
 *
 * WHY THIS GATE EXISTS. `output_classes.<class>.voice_spec: expected` is not a
 * preference — it BINDS the overlay's spec gate. A bound class whose spec is
 * absent or whose hash does not match the root manifest fails closed: that
 * class's autonomous sends downgrade to draft, and they stay downgraded until
 * someone installs a spec. That is correct, and it is the whole reason the
 * declaration exists (see sections-output-classes.ts).
 *
 * It also means a declaration nobody can satisfy is not a harmless typo. It is
 * a silent, permanent downgrade of a class of output, with no error anywhere
 * and nothing on the seat to explain it. A class slug misspelled here, or one
 * the portal writer cannot address, produces exactly that.
 *
 * So this asserts the loop closes: every class declared `expected` on any seat
 * exists in the class registry, and the console → vault writer can produce a
 * key and a document body for it. The producer and the expectation are checked
 * against each other rather than each against its own idea of the vocabulary.
 *
 * This is the repo half of #2094. The runtime half — the spec installing
 * root-owned, the gate passing on a read turn and downgrading on a turn that
 * did not read — is observed on a seat and cannot be closed here.
 */

import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { parse as parseYaml } from 'yaml'
import { skipReason } from '../scripts/lib/seat-inventory'

import {
  buildSpecDocument,
  specObjectKey,
  SPEC_PROPERTIES,
  type SpecProperty,
} from '../src/lib/operator/output-class-specs'

const CUSTOMERS_DIR = join(process.cwd(), 'operator', 'customers')
const REGISTRY_PATH = join(process.cwd(), 'operator', 'contracts', 'output-classes.yaml')

interface Declaration {
  slug: string
  outputClass: string
  property: SpecProperty
}

function registryClasses(): Set<string> {
  const parsed = parseYaml(readFileSync(REGISTRY_PATH, 'utf8')) as {
    classes?: Record<string, unknown>
  }
  const classes = parsed?.classes
  if (!classes || typeof classes !== 'object') {
    throw new Error(`${REGISTRY_PATH} has no classes: block`)
  }
  return new Set(Object.keys(classes))
}

/** Every (seat, class, property) declared `expected` anywhere in the tree. */
/** Customer dirs that boot or scaffold one: never a decommissioned seat's tombstone. */
function customerDirs(): string[] {
  return readdirSync(CUSTOMERS_DIR)
    .filter((name) => {
      const why = skipReason(name)
      return why !== 'tombstone' && why !== 'hidden'
    })
    .sort()
}

function expectedDeclarations(): Declaration[] {
  const found: Declaration[] = []
  for (const slug of customerDirs()) {
    const path = join(CUSTOMERS_DIR, slug, 'customer.yaml')
    let raw: string
    try {
      raw = readFileSync(path, 'utf8')
    } catch {
      continue
    }
    const doc = (parseYaml(raw) ?? {}) as { output_classes?: Record<string, unknown> }
    const declared = doc.output_classes
    if (!declared || typeof declared !== 'object') continue
    for (const [outputClass, entry] of Object.entries(declared)) {
      if (!entry || typeof entry !== 'object') continue
      for (const property of SPEC_PROPERTIES) {
        if ((entry as Record<string, unknown>)[`${property}_spec`] === 'expected') {
          found.push({ slug, outputClass, property })
        }
      }
    }
  }
  return found
}

describe('declared spec expectations are satisfiable', () => {
  it('at least one seat declares output_classes', () => {
    // Without this, every assertion below passes vacuously and the spec loader
    // stays shipped-but-bound-to-nothing — which is the state #2094 exists to
    // end, and precisely the failure class this programme indicts.
    const anyDeclared = customerDirs().some((slug) => {
      try {
        const doc = parseYaml(readFileSync(join(CUSTOMERS_DIR, slug, 'customer.yaml'), 'utf8')) as {
          output_classes?: unknown
        }
        return doc?.output_classes != null
      } catch {
        return false
      }
    })
    expect(
      anyDeclared,
      'no customer.yaml declares output_classes, so the overlay spec gate binds on nothing ' +
        'fleet-wide and the shipped spec loader governs no output at all'
    ).toBe(true)
  })

  it('every class declared expected exists in the class registry', () => {
    const known = registryClasses()
    for (const { slug, outputClass, property } of expectedDeclarations()) {
      expect(
        known.has(outputClass),
        `${slug} declares ${property}_spec: expected for output class '${outputClass}', which is ` +
          `not in operator/contracts/output-classes.yaml. A class the registry does not know is a ` +
          `class the gate resolves to nothing — the expectation silently governs no send.`
      ).toBe(true)
    }
  })

  it('the console writer can produce a key and a body for every expected class', async () => {
    for (const { slug, outputClass, property } of expectedDeclarations()) {
      expect(() => specObjectKey(slug)).not.toThrow()

      const built = await buildSpecDocument([
        { outputClass, property, body: 'probe body for the declaration gate' },
      ])
      expect(
        built.ok,
        `${slug} declares ${property}_spec: expected for '${outputClass}', but the console → vault ` +
          `writer refuses to build a document for it: ` +
          `${built.ok ? '' : built.errors.join('; ')}. A declaration the producer cannot satisfy is ` +
          `a permanent fail-closed downgrade of that class with nothing anywhere to explain it.`
      ).toBe(true)
      if (built.ok) {
        expect(built.doc.classes[outputClass]?.[property]?.sha256).toMatch(/^[0-9a-f]{64}$/)
      }
    }
  })
})
