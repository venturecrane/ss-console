import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, writeFileSync, statSync } from 'node:fs'
import { resolve, join, relative } from 'node:path'

/**
 * Module-size ratchet for the Python operator tree.
 *
 * WHY THIS LIVES IN THE TYPESCRIPT SUITE AND NOT IN RUFF. Two reasons, both
 * probed rather than assumed:
 *
 *   1. Ruff has no file-length rule. Every size setting it exposes is
 *      `line-length` — the per-line character width behind E501. There is no
 *      equivalent of eslint's `max-lines`, so the ceiling cannot be expressed
 *      as a ruff rule at all.
 *   2. `.github/workflows/operator-substrate.yml` is NOT a required status
 *      check. The only contexts gating a merge to main are
 *      "Typecheck, Lint, Format, Test" (this suite, via `npm run verify`) and
 *      "Security Summary". On 2026-08-21 PR #2526 merged with the substrate
 *      suite red, which is what a non-required gate buys you. A ceiling placed
 *      there would not gate.
 *
 * WHAT IT COUNTS. Logical lines: blanks, comment-only lines, and docstring
 * bodies excluded. That matches what `eslint.config.js:20` already does for
 * TypeScript (`max-lines` with `skipBlankLines: true, skipComments: true`), so
 * the two halves of the codebase are held to the same yardstick. Counting raw
 * `wc -l` would hold Python to a stricter standard than TypeScript and would
 * charge the tree for its comments — `establishment.py` carried 489 comment
 * lines and 80 docstring lines out of 3,509. Documentation is not debt.
 *
 * ONE COUNTER, ONE ARTIFACT. This test both ENFORCES and REGENERATES the
 * baseline, following the `tests/gate-coverage-snapshot.test.ts` idiom. That is
 * deliberate: if the baseline were produced by one counter (say a Python
 * tokenizer) and enforced by another (this file), the two could disagree and
 * the gate would fire on the disagreement rather than on real growth. Sharing
 * the counter makes that class of failure structurally impossible instead of
 * merely unlikely.
 *
 * REGENERATE:
 *
 *   UPDATE_OPERATOR_MODULE_SIZE_BASELINE=1 npx vitest run tests/operator-module-size.test.ts
 *
 * THE RATCHET ONLY TIGHTENS. A baselined file that grows fails. A baselined
 * file that shrinks also fails, with a message telling you to regenerate — so
 * the recorded number tracks reality downward and never drifts up. A file not
 * in the baseline fails the moment it crosses the ceiling.
 *
 * WHY IT EXISTS. The 2026-08-23 code review found the count of oversized
 * operator modules had gone 15 -> 20 in nine days, with
 * `workspace_broker/establishment.py` going 692 -> 2,752 logical lines over the
 * same window. Nothing in CI could see that happening. This is the thing that
 * can.
 */

const REPO_ROOT = resolve(__dirname, '..')
const OPERATOR_DIR = join(REPO_ROOT, 'operator')
const BASELINE_FILE = join(OPERATOR_DIR, 'contracts', 'operator-module-size.json')

/** Logical-line ceiling for a non-test operator module. Mirrors eslint.config.js:20. */
const CEILING = 500

/** Directories never walked: vendored venvs, caches, build output. */
const SKIP_DIRS = new Set(['.venv', '__pycache__', 'node_modules', '.pytest_cache', '.ruff_cache'])

interface Baseline {
  _comment: string
  ceiling: number
  modules: Record<string, number>
}

/**
 * A file is a test file if pytest would collect it, or if it sits in a tests/
 * directory. Test modules are exempt from the ceiling for the same reason
 * eslint.config.js:83-92 exempts them on the TypeScript side: a long table of
 * cases is not the complexity the ceiling is aimed at.
 */
function isTestFile(relPath: string): boolean {
  const base = relPath.split('/').pop() ?? ''
  if (base.startsWith('test_') || base.endsWith('_test.py')) return true
  return relPath.split('/').includes('tests')
}

function walkPythonFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) {
      walkPythonFiles(full, acc)
    } else if (entry.endsWith('.py')) {
      acc.push(full)
    }
  }
  return acc
}

/**
 * Count logical lines: exclude blanks, comment-only lines, and the contents of
 * triple-quoted strings.
 *
 * THE RULE. A line counts when it carries code OUTSIDE any triple-quoted string.
 * So a bare docstring contributes nothing (opener, body, and closer all sit
 * inside the string), while `SQL = """` counts once for the assignment and its
 * body counts for nothing. That treats a long embedded SQL or prose literal as
 * one statement rather than as N lines of logic, which is the honest reading —
 * the ceiling is aimed at complexity, and a 40-line string constant is not
 * complexity.
 *
 * WHY THE CHARACTER WALK RATHER THAN startsWith. The first version of this
 * function tested whether a trimmed line STARTED with a triple quote. Its own
 * unit test below caught the flaw: the CLOSING `"""` of a `SQL = """` block also
 * starts with a triple quote, so it was read as OPENING a new docstring and
 * silently swallowed every following line until the next quote. Tracking
 * delimiter state across the line is what makes the counter correct rather than
 * plausible.
 *
 * Escaped quotes and `#` inside string bodies are not modelled. They do not need
 * to be: the same function produces and checks the baseline, so only
 * self-consistency matters, and no real module in this tree depends on the
 * difference.
 */
export function countLogicalLines(source: string): number {
  let count = 0
  let inTriple = false
  let delim = ''

  for (const line of source.split('\n')) {
    let outside = ''
    let i = 0

    while (i < line.length) {
      if (inTriple) {
        if (line.startsWith(delim, i)) {
          inTriple = false
          i += 3
        } else {
          i += 1
        }
        continue
      }
      if (line.startsWith('"""', i) || line.startsWith("'''", i)) {
        delim = line.slice(i, i + 3)
        inTriple = true
        i += 3
        continue
      }
      outside += line[i]
      i += 1
    }

    const trimmed = outside.trim()
    if (trimmed === '') continue
    if (trimmed.startsWith('#')) continue
    count += 1
  }

  return count
}

function loadBaseline(): Baseline {
  return JSON.parse(readFileSync(BASELINE_FILE, 'utf8')) as Baseline
}

function measureTree(): Record<string, number> {
  const measured: Record<string, number> = {}
  for (const abs of walkPythonFiles(OPERATOR_DIR)) {
    const rel = relative(REPO_ROOT, abs)
    if (isTestFile(rel)) continue
    const lines = countLogicalLines(readFileSync(abs, 'utf8'))
    if (lines > CEILING) measured[rel] = lines
  }
  return measured
}

const REGENERATE = process.env.UPDATE_OPERATOR_MODULE_SIZE_BASELINE === '1'

describe('operator module-size ratchet', () => {
  it('the counter can distinguish code from comments and docstrings', () => {
    // Law 12: a counter that returned the raw line count would pass every
    // assertion below about growth while measuring nothing about code. Prove
    // the instrument discriminates before trusting what it reports.
    const sample = [
      'import os', //      counts (1)
      '', //               blank
      '# a comment', //    comment only
      '"""', //            opens a bare docstring
      'docstring body', // inside the string
      '"""', //            closes it
      'X = 1', //          counts (2)
      'SQL = """', //      counts (3) — code outside the quote
      'SELECT 1', //       inside the string
      '"""', //            closes it
      'Y = 2', //          counts (4)
    ].join('\n')
    expect(countLogicalLines(sample)).toBe(4)
    expect(countLogicalLines('')).toBe(0)
    expect(countLogicalLines('# only a comment')).toBe(0)

    // The regression that the first draft of this counter actually had: the
    // closing quote of an assignment block was read as opening a docstring, so
    // everything after it vanished. `Y = 2` above is the line that caught it —
    // if this counter breaks that way again, the sample drops to 3.
    expect(countLogicalLines(['A = """', 'body', '"""', 'B = 1'].join('\n'))).toBe(2)

    // A single-line triple-quoted docstring must not leave the scanner stuck
    // inside a string for the rest of the file.
    expect(countLogicalLines(['"""one liner."""', 'C = 1'].join('\n'))).toBe(1)
  })

  it('baseline is well-formed and matches the enforced ceiling', () => {
    const baseline = loadBaseline()
    expect(baseline.ceiling).toBe(CEILING)
    for (const [path, lines] of Object.entries(baseline.modules)) {
      expect(lines, `${path} baselined below the ceiling`).toBeGreaterThan(CEILING)
    }
  })

  it('no operator module has grown past its baseline, and no new module is over the ceiling', () => {
    const measured = measureTree()

    if (REGENERATE) {
      const modules: Record<string, number> = {}
      for (const key of Object.keys(measured).sort()) modules[key] = measured[key]
      const next: Baseline = {
        _comment:
          'Logical-line census of non-test operator/**/*.py modules over the ceiling. ' +
          'Generated and enforced by tests/operator-module-size.test.ts — do not hand-edit. ' +
          'Regenerate with UPDATE_OPERATOR_MODULE_SIZE_BASELINE=1 npx vitest run tests/operator-module-size.test.ts. ' +
          'A module leaves this file by getting smaller; nothing may enter it by getting bigger.',
        ceiling: CEILING,
        modules,
      }
      writeFileSync(BASELINE_FILE, JSON.stringify(next, null, 2) + '\n', 'utf8')
      return
    }

    const baseline = loadBaseline()
    const problems: string[] = []

    for (const [path, lines] of Object.entries(measured)) {
      const recorded = baseline.modules[path]
      if (recorded === undefined) {
        problems.push(
          `${path}: ${lines} logical lines, over the ${CEILING} ceiling and not in the baseline. ` +
            `Split it, or if this is a deliberate carry-over, regenerate the baseline and say why in the PR.`
        )
      } else if (lines > recorded) {
        // A naive split BREAKS THE RUNTIME for skills/<name>/pre_run.py. The
        // Hermes scheduler stages that one file to <profile>/scripts/<skill>/
        // pre_run.py and runs it there (operator/templates/pre_run_gate.py:35);
        // a sibling module extracted next to it is simply not staged, so the
        // import fails on a live client seat. Say so where the advice is given,
        // rather than leaving a correct-sounding instruction that is wrong for
        // this one shape.
        const stagedAlone = /skills\/[^/]+\/pre_run\.py$/.test(path)
        problems.push(
          `${path}: grew ${recorded} -> ${lines} logical lines. The ratchet only tightens — ` +
            `split the module rather than raising its baseline.` +
            (stagedAlone
              ? ` NOTE: the scheduler stages this file ALONE (see operator/templates/pre_run_gate.py:35), ` +
                `so a naive split breaks the runtime — keep pre_run.py self-contained, or extend the ` +
                `staging to carry its siblings first.`
              : '')
        )
      }
    }

    for (const [path, recorded] of Object.entries(baseline.modules)) {
      const now = measured[path]
      if (now === undefined) {
        problems.push(
          `${path}: no longer over the ceiling (was ${recorded}). Regenerate the baseline with ` +
            `UPDATE_OPERATOR_MODULE_SIZE_BASELINE=1 so the ratchet keeps its new, tighter position.`
        )
      } else if (now < recorded) {
        problems.push(
          `${path}: shrank ${recorded} -> ${now} logical lines. Regenerate the baseline with ` +
            `UPDATE_OPERATOR_MODULE_SIZE_BASELINE=1 so the ratchet keeps its new, tighter position.`
        )
      }
    }

    expect(problems, problems.join('\n')).toEqual([])
  })
})
