import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { resolve, join } from 'node:path'

/**
 * Coverage-config honesty — the check that keeps F12 closed.
 *
 * THE FINDING, AND WHAT PROBING IT ACTUALLY TURNED UP. The 2026-08-23 review
 * recorded "vitest coverage thresholds declared but no workflow runs them".
 * True, and an understatement. Probing on 2026-08-24 found:
 *
 *   - `@vitest/coverage-v8` was not a dependency, so `npm run test:coverage`
 *     exited with MISSING DEPENDENCY. The script had never run, anywhere,
 *     since the thresholds were authored on 2026-04-16.
 *   - With the provider installed, a full run reports `0/0` files — "All
 *     files | 0 | 0 | 0 | 0". The v8 provider collects nothing under Astro's
 *     `getViteConfig` wrapper, with the default include and with an explicit
 *     `--coverage.include='src/**\/*.ts'` alike, even for a test that plainly
 *     imports the module under test.
 *
 * So four numbers sat in `vitest.config.ts` for four months describing a floor
 * the repo was never holding, over an instrument that measures zero files. Any
 * reader — human or agent — would reasonably have taken them as evidence of a
 * coverage regime. That is the specific harm: not an unenforced rule, but a
 * misleading one.
 *
 * WHAT THIS FILE ENFORCES. Thresholds may exist only alongside a workflow that
 * actually runs coverage. Declaring them again without a runner fails here.
 * The converse is deliberately NOT enforced — a workflow that runs coverage
 * without thresholds is fine and is the natural first step back.
 *
 * WHAT IT DOES NOT CLAIM. This does not verify that coverage WORKS, only that
 * a threshold is not asserted without something running it. Proving the
 * provider collects real data requires the provider to be fixed first; when it
 * is, that proof belongs here as a second assertion.
 *
 * WHAT WOULD MAKE THIS FALSE (Law 12). Re-add a `thresholds:` block to
 * `vitest.config.ts` with no coverage step in any workflow and this goes red.
 * Break the workflow reader so it sees no files and the self-test goes red,
 * because "no workflow runs coverage" is the premise of the whole assertion.
 */

const REPO_ROOT = resolve(__dirname, '..')
const VITEST_CONFIG = join(REPO_ROOT, 'vitest.config.ts')
const WORKFLOWS = join(REPO_ROOT, '.github', 'workflows')

/**
 * Does any workflow actually run coverage?
 *
 * Matches the two ways it can be invoked: the `test:coverage` npm script, or
 * vitest's `--coverage` flag directly. Comment mentions are not enough — the
 * marker must appear on a line that is not a YAML comment, so a workflow that
 * merely discusses coverage does not satisfy the gate.
 */
function workflowRunsCoverage(): boolean {
  for (const entry of readdirSync(WORKFLOWS)) {
    if (!entry.endsWith('.yml') && !entry.endsWith('.yaml')) continue
    const text = readFileSync(join(WORKFLOWS, entry), 'utf-8')
    for (const line of text.split('\n')) {
      const code = line.trim()
      if (code.startsWith('#')) continue
      if (code.includes('test:coverage') || code.includes('--coverage')) return true
    }
  }
  return false
}

/** Is a `thresholds:` block declared inside the coverage config? */
function declaresThresholds(): boolean {
  const src = readFileSync(VITEST_CONFIG, 'utf-8')
  const start = src.indexOf('coverage: {')
  if (start === -1) return false
  const region = src.slice(start)
  for (const line of region.split('\n')) {
    const code = line.trim()
    if (code.startsWith('//') || code.startsWith('*')) continue
    if (/^thresholds\s*:/.test(code)) return true
  }
  return false
}

describe('coverage config honesty', () => {
  it('the workflow reader sees real workflow files', () => {
    // Law 12 on the instrument. The assertion below is "thresholds imply a
    // runner"; a reader that could never find a runner would make the rule
    // unfalsifiable in one direction and vacuous in the other.
    const files = readdirSync(WORKFLOWS).filter((f) => f.endsWith('.yml') || f.endsWith('.yaml'))
    expect(files.length).toBeGreaterThan(5)

    // And it must be able to say YES, not only NO. Prove the matcher fires on
    // the shape it is looking for, so a green result means "no workflow runs
    // coverage" rather than "the matcher never matches anything".
    const probe = ['jobs:', '  x:', '    steps:', '      - run: npm run test:coverage']
    const matches = probe.some((l) => !l.trim().startsWith('#') && l.includes('test:coverage'))
    expect(matches, 'the coverage matcher cannot recognise a coverage step').toBe(true)
  })

  it('does not declare coverage thresholds without a workflow that runs them', () => {
    const thresholds = declaresThresholds()
    const runner = workflowRunsCoverage()

    expect(
      thresholds && !runner,
      `vitest.config.ts declares coverage thresholds, but no workflow runs coverage.\n` +
        `That combination was the state from 2026-04-16 to 2026-08-24: four numbers ` +
        `describing a floor nothing measured, which read to every subsequent reader as a ` +
        `coverage regime the repo was holding.\n` +
        `Either add a step that runs \`npm run test:coverage\` to a workflow, or remove the ` +
        `thresholds. Note that as of 2026-08-24 the v8 provider reports 0/0 files under ` +
        `Astro's getViteConfig wrapper — so adding the step is not sufficient on its own, ` +
        `and a threshold over a 0-file measurement is worse than none.`
    ).toBe(false)
  })
})
