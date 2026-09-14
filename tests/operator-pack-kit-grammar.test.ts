import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'fs'
import { resolve, join } from 'path'

// Truthfulness P0: the pack config is largely aspirational (only one connector is
// runtime-live, and most packs have no customer yet), so a pack must never read as a
// delivered, running capability. Two pack generations carry this truth differently,
// and during the one-at-a-time migration to the lifecycle standard both must stay
// enforced:
//
//   - Lifecycle standard (docs/marketing/pack-standard.md): the pack walks the
//     vertical's core process via <PackLifecycle>. The walk uses the established
//     selling voice, so the truth is carried by the SECTION FRAMING instead: the
//     walk is declared illustrative ("the shape of the work, not a fixed script")
//     and paired with the fail-closed honesty ("surfaces what it found and asks").
//     Both phrases are required.
//
//   - Legacy "What The Pack Starts You With" (the 11 not-yet-migrated): a
//     `packTemplates` array framed as "starting templates we configure with you,"
//     with no finished-capability verbs bound to a template.
//
// A pack is detected as lifecycle-standard by its use of <PackLifecycle>.

const packsDir = resolve('src/pages/packs')

// Verbs that assert a finished, running capability. Banned inside packTemplates,
// where they would turn "a starting template for X" into "it does X today."
// ("does" is intentionally excluded: it matches benign "does not ..." phrasing.)
const CAPABILITY_VERBS = /\b(handles|manages|automates)\b/i

function packFiles(): string[] {
  return readdirSync(packsDir)
    .filter((n) => n.endsWith('.astro'))
    .map((n) => join(packsDir, n))
}

function packTemplatesLiteral(src: string): string | null {
  const m = src.match(/const packTemplates = \[([\s\S]*?)\n\]/)
  return m ? m[1] : null
}

describe('Operator pack kit grammar (truthfulness)', () => {
  for (const file of packFiles()) {
    const rel = file.slice(file.indexOf('src/'))
    const src = readFileSync(file, 'utf-8')
    const flat = src.replace(/\s+/g, ' ')
    const isLifecyclePack = src.includes('<PackLifecycle')

    if (isLifecyclePack) {
      it(`${rel} frames the lifecycle walk as illustrative, not a delivered capability`, () => {
        expect(
          flat,
          `lifecycle pack ${rel} must declare the walk illustrative ("the shape of the work, not a fixed script")`
        ).toContain('the shape of the work, not a fixed script')
      })

      it(`${rel} carries the fail-closed test-and-tune honesty`, () => {
        expect(
          flat,
          `lifecycle pack ${rel} must carry the fail-closed honesty ("surfaces what it found and asks")`
        ).toContain('surfaces what it found and asks')
      })
    } else {
      it(`${rel} frames the kit as starting templates, not a capability`, () => {
        // The truthful framing must be present on every legacy pack page.
        expect(src).toContain('starting templates we configure with you')
      })

      it(`${rel} packTemplates use no finished-capability verbs`, () => {
        const literal = packTemplatesLiteral(src)
        expect(literal, 'packTemplates array not found').not.toBeNull()
        expect(
          CAPABILITY_VERBS.test(literal as string),
          `packTemplates binds a finished-capability verb (handles/manages/automates/does) to a template in ${rel}`
        ).toBe(false)
      })
    }
  }
})
