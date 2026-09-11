import { describe, it, expect } from 'vitest'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve, join } from 'node:path'
import { execFileSync } from 'node:child_process'

/**
 * Vendored-symbol sync gate for `operator/skills/*\/pre_run.py` — the check
 * that keeps F7 closed.
 *
 * THE FINDING, AND HOW IT WAS UNDERSTATED. The 2026-08-23 review recorded
 * "`BrokerSuppressedWakeWriter` duplicated across 4 skills, no sync test".
 * Parsing the tree found 38 duplicated top-level symbols across the 14
 * `pre_run.py` files, not one: an entire heartbeat library
 * (`decide_and_emit`, `write_suppressed_wake_heartbeat`, `_append_wake_row`,
 * `probe_open_matter_count`, `_skill_name`) copy-pasted into eight skills.
 *
 * WHY THE DUPLICATION IS NOT THE DEFECT. The Hermes scheduler stages
 * `pre_run.py` ALONE into `<profile>/scripts/<skill>/pre_run.py` and runs it
 * there (`operator/templates/pre_run_gate.py:35`). A sibling module extracted
 * next to it is not staged, so importing it fails on a live client seat. These
 * files CANNOT import a shared module today. Vendoring is forced by the
 * staging mechanism, and a gate demanding deduplication would be a gate nobody
 * can satisfy — which is a gate that gets deleted.
 *
 * WHAT IS THE DEFECT: the copies can diverge silently. Eight copies of
 * `decide_and_emit` agree today. Nothing said so, and nothing would say if one
 * stopped agreeing — the drift would surface as one skill's heartbeat behaving
 * differently on a live seat, with no signal in CI.
 *
 * WHY AN ALLOWLIST RATHER THAN "ALL DUPLICATES MUST AGREE". Of the 38
 * duplicated symbols, 17 differ, and most of those SHOULD: `decide` differs
 * six ways across six skills because each skill decides differently, and
 * `main` differs seven ways for the same reason. A blanket gate would fail on
 * correct code on day one. The contract names the symbols that are shared
 * LIBRARY code; everything else is per-skill and unconstrained.
 *
 * WHY PYTHON PARSES THE PYTHON. The first draft of this file extracted and
 * normalized definitions with TypeScript string handling, and it was wrong
 * twice in ways that both pointed the same direction — toward false agreement:
 *
 *   1. It kept NESTED docstrings, so the four copies of
 *      `BrokerSuppressedWakeWriter` (which differ only in prose) read as
 *      divergent and were dropped from the generated contract. The symbol the
 *      finding was about would have gone ungated.
 *   2. It terminated a definition at the first column-0 character, and a
 *      wrapped signature closes with `) -> T:` at column 0 — so
 *      `_seat_sentinel_decision` was captured as its PARAMETER LIST, and two
 *      copies with different bodies hashed identically.
 *
 * Both were found by cross-checking against `ast`, not by review. A text
 * comparator also cannot see that two copies of `_writer_factory` are the same
 * code differently wrapped. The lesson is not "be more careful with the
 * regex" — it is that the correct instrument for Python source is a Python
 * parser, so this file shells out to one rather than approximating it.
 *
 * PYTHON AVAILABILITY. `python3` is present on `ubuntu-latest`, where the
 * required "Typecheck, Lint, Format, Test" job runs. When it is missing the
 * test SKIPS on a developer machine and FAILS under `CI` — a gate that
 * silently no-ops in CI when its interpreter goes missing would be a gate that
 * measured nothing, which is the exact failure this whole file is about.
 *
 * ONE CENSUS, ONE ARTIFACT. The same census generates and enforces the
 * contract, following `tests/operator-module-size.test.ts`. Sharing it makes
 * "the generator and the checker disagree" structurally impossible.
 *
 * THE CONTRACT STORES NAMES AND COUNTS, NEVER HASHES, and this is the third
 * bug this file had. The first version recorded each symbol's digest and
 * compared every copy against the RECORDED value. It passed on the authoring
 * machine and failed in CI on all 23 symbols at once, in all 14 skills —
 * because `ast.dump` output is interpreter-version dependent, so a digest
 * taken under one Python is not comparable under another. A stored digest is a
 * measurement frozen with its instrument's calibration attached.
 *
 * The invariant was never "these copies equal a historical value". It is
 * "these copies equal EACH OTHER", so the check computes every copy in one run
 * and compares them among themselves. Version-independence falls out for free:
 * both sides of every comparison come from the same interpreter. It also means
 * a shared symbol changed consistently across all copies passes without a
 * regeneration dance, which is the correct behaviour — reviewing that change is
 * code review's job, and the diff shows it plainly.
 *
 * REGENERATE (after deliberately changing a shared symbol in EVERY copy):
 *
 *   UPDATE_PRE_RUN_SHARED_SYMBOLS=1 npx vitest run tests/operator-vendored-symbols.test.ts
 */

const REPO_ROOT = resolve(__dirname, '..')
const CONTRACT = join(REPO_ROOT, 'operator', 'contracts', 'pre-run-shared-symbols.json')

interface Contract {
  _comment: string
  symbols: Record<string, { copies: number }>
}

/**
 * Census script. Emits `{ symbolName: { skillName: hash } }` over every
 * top-level def/class in every `operator/skills/*\/pre_run.py`.
 *
 * Docstrings are stripped at EVERY nesting level before hashing: prose is not
 * behaviour, and a reworded comment must not read as drift. Everything else —
 * including a triple-quoted SQL or prose CONSTANT in a body — is retained,
 * because a constant IS behaviour.
 *
 * `ast.dump` is deliberately the digest input rather than the source text: it
 * is insensitive to formatting (two copies of `_writer_factory` differ only in
 * where the call wraps) and sensitive to structure, which is the distinction
 * this gate needs.
 */
const CENSUS_PY = `
import ast, json, pathlib, hashlib, sys

def strip_docstrings(node):
    for n in ast.walk(node):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = n.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                n.body = body[1:] or [ast.Pass()]
    return node

out = {}
for path in sorted(pathlib.Path('operator/skills').glob('*/pre_run.py')):
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            dumped = ast.dump(strip_docstrings(node), annotate_fields=True)
            digest = hashlib.sha256(dumped.encode()).hexdigest()
            out.setdefault(node.name, {})[path.parent.name] = digest

json.dump(out, sys.stdout)
`

type Census = Record<string, Record<string, string>>

/** Run the census, or return null when python3 is unavailable outside CI. */
function census(): Census | null {
  try {
    const raw = execFileSync('python3', ['-c', CENSUS_PY], {
      cwd: REPO_ROOT,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    return JSON.parse(raw) as Census
  } catch (err) {
    if (process.env.CI) {
      throw new Error(
        `The vendored-symbol gate could not run its census. python3 is required in CI — ` +
          `a skip here would silently un-gate every shared pre_run.py symbol.\n${String(err)}`
      )
    }
    return null
  }
}

function loadContract(): Contract {
  return JSON.parse(readFileSync(CONTRACT, 'utf-8')) as Contract
}

const REGENERATE = process.env.UPDATE_PRE_RUN_SHARED_SYMBOLS === '1'

describe('pre_run.py vendored-symbol sync', () => {
  it('the census finds real content and discriminates prose from behaviour', () => {
    // Law 12 on the instrument. Every assertion below compares hashes, so a
    // census that silently returned nothing would report perfect agreement
    // forever — two empty sets are equal.
    const found = census()
    if (!found) {
      console.warn('[vendored-symbols] SKIPPED: python3 unavailable (binding in CI)')
      return
    }

    expect(Object.keys(found).length, 'census came back empty').toBeGreaterThan(20)

    // Prove the digest ignores prose and formatting but catches behaviour, on
    // a fixture rather than on the tree — the tree could change, the contract
    // of the instrument must not.
    const probe = `
import ast, hashlib, json, sys
def strip(n):
    for x in ast.walk(n):
        if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            b = x.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \\
                    and isinstance(b[0].value.value, str):
                x.body = b[1:] or [ast.Pass()]
    return n
def h(src):
    return hashlib.sha256(ast.dump(strip(ast.parse(src).body[0])).encode()).hexdigest()
base    = 'def f(a):\\n    """One."""\\n    return g(a, 1)\\n'
reworded= 'def f(a):\\n    """Totally different words."""\\n    return g(a, 1)\\n'
rewrap  = 'def f(a):\\n    """One."""\\n    return g(\\n        a, 1\\n    )\\n'
changed = 'def f(a):\\n    """One."""\\n    return g(a, 2)\\n'
constant_a = 'def f():\\n    SQL = """SELECT 1"""\\n    return SQL\\n'
constant_b = 'def f():\\n    SQL = """SELECT 2"""\\n    return SQL\\n'
json.dump({
  "prose_ignored": h(base) == h(reworded),
  "format_ignored": h(base) == h(rewrap),
  "behaviour_caught": h(base) != h(changed),
  "constant_caught": h(constant_a) != h(constant_b),
}, sys.stdout)
`
    const verdict = JSON.parse(
      execFileSync('python3', ['-c', probe], { cwd: REPO_ROOT, encoding: 'utf-8' })
    ) as Record<string, boolean>

    expect(verdict.prose_ignored, 'a reworded docstring must not read as drift').toBe(true)
    expect(verdict.format_ignored, 'a rewrapped call must not read as drift').toBe(true)
    expect(verdict.behaviour_caught, 'a changed argument MUST read as drift').toBe(true)
    expect(verdict.constant_caught, 'a changed string CONSTANT MUST read as drift').toBe(true)
  })

  it('every contracted symbol is identical in every skill that vendors it', () => {
    const found = census()
    if (!found) return // reported by the self-test above

    if (REGENERATE) {
      const symbols: Contract['symbols'] = {}
      for (const name of Object.keys(found).sort()) {
        const copies = found[name]
        const skills = Object.keys(copies)
        const hashes = new Set(Object.values(copies))
        // Seed with symbols that are duplicated AND already agree. A symbol
        // that differs across skills is per-skill logic (`decide`, `main`) and
        // is deliberately left unconstrained.
        if (skills.length > 1 && hashes.size === 1) {
          symbols[name] = { copies: skills.length }
        }
      }
      writeFileSync(
        CONTRACT,
        JSON.stringify(
          {
            _comment:
              'Symbols vendored identically into multiple operator/skills/*/pre_run.py. ' +
              'The Hermes scheduler stages pre_run.py ALONE (operator/templates/pre_run_gate.py:35), ' +
              'so these files cannot import a shared module — the copies are forced, and this ' +
              'contract is what keeps them honest. Hashes are over a docstring-stripped ast.dump, ' +
              'so prose and formatting are ignored and structure is not. Generated and enforced by ' +
              'tests/operator-vendored-symbols.test.ts — do not hand-edit. Regenerate with ' +
              'UPDATE_PRE_RUN_SHARED_SYMBOLS=1 after changing a shared symbol in EVERY copy.',
            symbols,
          },
          null,
          2
        ) + '\n',
        'utf-8'
      )
      return
    }

    const contract = loadContract()
    const problems: string[] = []

    for (const [name, expected] of Object.entries(contract.symbols)) {
      const copies = found[name]
      if (!copies) {
        problems.push(
          `${name}: contracted as a shared symbol but no longer defined in any pre_run.py. ` +
            `If it was deliberately retired everywhere, regenerate the contract.`
        )
        continue
      }
      // Compare the copies to EACH OTHER, never to a stored hash — see the
      // header note on why a recorded digest was version-dependent poison.
      const groups = new Map<string, string[]>()
      for (const [skill, h] of Object.entries(copies)) {
        if (!groups.has(h)) groups.set(h, [])
        groups.get(h)!.push(skill)
      }
      if (groups.size > 1) {
        const shape = [...groups.values()]
          .map((skills) => skills.sort().join(' + '))
          .sort()
          .join('  vs  ')
        problems.push(
          `${name}: vendored into ${Object.keys(copies).length} skill(s) and no longer identical. ` +
            `Versions: ${shape}. ` +
            `These copies exist because the scheduler stages pre_run.py alone ` +
            `(operator/templates/pre_run_gate.py:35) — a change to one MUST be made to all, ` +
            `or the skills behave differently on a live seat. Apply the change to every copy. ` +
            `No regeneration is needed: the contract records names and copy counts, not ` +
            `content, so a change made consistently everywhere passes on its own.`
        )
      }
      const now = Object.keys(copies).length
      if (now < expected.copies) {
        problems.push(
          `${name}: was vendored into ${expected.copies} skills, now ${now}. ` +
            `If a skill deliberately dropped it, regenerate the contract and say why in the PR.`
        )
      }
    }

    expect(problems, problems.join('\n')).toEqual([])
  })

  it('the contract is non-empty and every entry is genuinely shared', () => {
    // A contract that emptied itself would make the sync test above vacuous
    // while still passing. Pin the floor.
    const entries = Object.entries(loadContract().symbols)
    expect(entries.length).toBeGreaterThanOrEqual(20)
    for (const [name, entry] of entries) {
      expect(entry.copies, `${name} contracted with fewer than 2 copies`).toBeGreaterThan(1)
    }
  })
})
