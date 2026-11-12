/**
 * Known-answer tests for the sprawl audit's pure half (scripts/sprawl/analyze.ts).
 *
 * Each rule is asserted on a case that must count and on the same case
 * perturbed so it must not. The first baseline run (2026-11-10) counted the
 * TypeScript template `${WHY}` as an environment variable in 510 files; the
 * template-literal case below is that defect, held shut.
 */
import { describe, expect, it } from 'vitest'
import {
  type Clone,
  type FileFacts,
  areaOf,
  changeCoupling,
  envNames,
  isGenerated,
  isTestPath,
  literalCensus,
  literalRatchet,
  regressions,
  summarizeClones,
  summarizeVulture,
  textPinCensus,
  textPinRatchet,
  unexplainedPinFiles,
} from '../scripts/sprawl/analyze'
import { pinLangOf, sourceTextPins } from '../scripts/sprawl/text-pins'
import { declaredCopies } from '../scripts/sprawl/sources'

const files = (n: number, dir: string, ext: string, text: string) =>
  Array.from({ length: n }, (_, i) => ({ path: `${dir}${i}/x.${ext}`, text }))

describe('literalCensus', () => {
  it('counts a seat path restated across five non-test files', () => {
    const rows = literalCensus(files(5, 'operator/a', 'py', 'P = "/run/smd-jobs/queue"'))
    expect(rows).toEqual([
      expect.objectContaining({ literal: '/run/smd-jobs/queue', kind: 'path', files: 5 }),
    ])
  })

  it('does not count it in four files, or when the fifth is a test', () => {
    expect(literalCensus(files(4, 'operator/a', 'py', 'P = "/run/smd-jobs"'))).toEqual([])
    const withTest = [
      ...files(4, 'operator/a', 'py', '"/run/smd-jobs"'),
      { path: 'operator/tests/test_x.py', text: '"/run/smd-jobs"' },
    ]
    expect(literalCensus(withTest)).toEqual([])
  })

  it('treats a path ending a sentence as the same path', () => {
    const rows = literalCensus([
      ...files(4, 'src/a', 'ts', '"/opt/data"'),
      { path: 'src/z.ts', text: "const why = 'kept under /opt/data.'" },
    ])
    expect(rows.map((r) => r.literal)).toEqual(['/opt/data'])
  })

  it('never counts prose under docs', () => {
    expect(literalCensus(files(9, 'docs/a', 'md', 'see /opt/data/jobs'))).toEqual([])
  })

  it('does not count a seat_layout constant that shares an env name, but does count the name', () => {
    const reader = { path: 'operator/r.sh', text: 'echo "${CUSTOMER_YAML}"' }
    const viaLayout = files(6, 'operator/a', 'py', 'P = seat_layout.CUSTOMER_YAML')
    expect(literalCensus([reader, ...viaLayout])).toEqual([])
    const restated = files(6, 'operator/a', 'py', 'P = os.environ.get("CUSTOMER_YAML")')
    expect(literalCensus([reader, ...restated]).map((r) => r.literal)).toEqual(['CUSTOMER_YAML'])
  })

  it('does not match a path-like fragment that is not anchored at a seat root', () => {
    expect(literalCensus(files(6, 'src/a', 'ts', 'const u = "a/optional/run"'))).toEqual([])
  })
})

describe('envNames', () => {
  it('reads names through Python, Node and shell accessors', () => {
    const names = envNames([
      { path: 'a.py', text: 'os.environ.get("SMD_A")\nos.environ["SMD_B"]\nos.getenv("SMD_C")' },
      { path: 'b.ts', text: 'process.env.SMD_D; process.env["SMD_E"]' },
      { path: 'c.sh', text: 'echo "${SMD_F:-x}"' },
    ])
    expect([...names].sort()).toEqual(['SMD_A', 'SMD_B', 'SMD_C', 'SMD_D', 'SMD_E', 'SMD_F'])
  })

  it('does not read a TypeScript template literal or an underscore-free word as an env var', () => {
    expect([...envNames([{ path: 'b.ts', text: 'const s = `${WHY_NOT} ${URL}`' }])]).toEqual([])
    expect([...envNames([{ path: 'c.sh', text: 'echo "${URL}"' }])]).toEqual([])
  })

  it('does not count a name the CI platform defines, but does count our own', () => {
    const sh = 'echo x >> "${GITHUB_OUTPUT}"; echo "${RUNNER_TEMP}"; echo "${SMD_OWN}"'
    expect([...envNames([{ path: 'c.sh', text: sh }])]).toEqual(['SMD_OWN'])
  })

  it('does not count a read of the generated seat layout as an env var', () => {
    const sh = 'test -d ${SEAT_ROOT}${SMD_LAYOUT_JOBS_RUN}/queue; echo "${SMD_LAYOUT_DATA}"'
    expect([...envNames([{ path: 'c.sh', text: sh }])]).toEqual(['SEAT_ROOT'])
  })
})

const facts = (entries: Array<[string, number, string]>) =>
  new Map<string, FileFacts>(entries.map(([p, lines, sha]) => [p, { lines, sha }]))
const clone = (a: string, b: string, lines: number): Clone => ({
  lines,
  a: { name: a, start: 1, end: lines },
  b: { name: b, start: 1, end: lines },
})

describe('summarizeClones', () => {
  it('counts a whole-file copy, including one with a provenance header', () => {
    const s = summarizeClones(
      [clone('operator/skills/a/l.py', 'operator/w/l.py', 100)],
      facts([
        ['operator/skills/a/l.py', 100, 'h'],
        ['operator/w/l.py', 105, 'g'],
      ])
    )
    expect(s).toMatchObject({ copiedFiles: 2, copiedLines: 205, partialClones: 0 })
  })

  it('counts the same block as a partial clone when the second file is much larger', () => {
    const s = summarizeClones(
      [clone('operator/skills/a/l.py', 'operator/w/l.py', 100)],
      facts([
        ['operator/skills/a/l.py', 100, 'h'],
        ['operator/w/l.py', 400, 'g'],
      ])
    )
    expect(s).toMatchObject({ copiedFiles: 0, partialClones: 1, partialLines: 100 })
  })

  it('counts a small block in large files as partial, and ignores tests', () => {
    const f = facts([
      ['src/a.ts', 400, 'h'],
      ['src/b.ts', 400, 'h'],
    ])
    expect(summarizeClones([clone('src/a.ts', 'src/b.ts', 20)], f).partialClones).toBe(1)
    expect(summarizeClones([clone('tests/a.test.ts', 'src/b.ts', 20)], f).partialClones).toBe(0)
  })
})

describe('changeCoupling', () => {
  const pair = ['operator/contracts/v.json', 'src/lib/audit.ts']

  it('reports two files in different areas that change together', () => {
    const rows = changeCoupling(Array.from({ length: 6 }, () => pair))
    expect(rows).toEqual([{ a: pair[0], b: pair[1], shared: 6, degree: 100 }])
  })

  it('does not report files in one area, a large changeset, or a weak pair', () => {
    expect(
      changeCoupling(Array.from({ length: 6 }, () => ['src/lib/a.ts', 'src/lib/b.ts']))
    ).toEqual([])
    const big = [...pair, ...Array.from({ length: 40 }, (_, i) => `x/${i}.ts`)]
    expect(changeCoupling(Array.from({ length: 6 }, () => big))).toEqual([])
    const weak = [
      ...Array.from({ length: 5 }, () => pair),
      ...Array.from({ length: 20 }, () => [pair[0]]),
    ]
    expect(changeCoupling(weak)).toEqual([])
  })
})

describe('helpers', () => {
  it('classifies tests and areas', () => {
    expect(isTestPath('operator/skills/x/tests/test_a.py')).toBe(true)
    expect(isTestPath('operator/skills/x/pre_run.py')).toBe(false)
    expect(areaOf('operator/skills/x/pre_run.py')).toBe('operator/skills/x')
    expect(areaOf('operator/runners/jobs/jobs/daemon.py')).toBe('operator/runners')
  })

  it('parses vulture output and ignores other lines', () => {
    const out =
      "a.py:1: unused function 'f' (60% confidence)\nnoise\nb.py:2: unused variable 'x' (100% confidence)\n"
    expect(summarizeVulture(out)).toEqual({ candidates: 2, byKind: { function: 1, variable: 1 } })
  })
})

describe('regressions', () => {
  const base = { copiedLines: 100, partialLines: 50, literals: 10, coupling: 3, dead: 40 }

  it('is empty when nothing rose, including when something fell', () => {
    expect(regressions(base, base)).toEqual([])
    expect(regressions({ ...base, dead: 30 }, base)).toEqual([])
  })

  it('names each number that rose', () => {
    expect(regressions({ ...base, literals: 11, coupling: 4 }, base)).toEqual([
      'literals restated in 5+ files: 10 -> 11',
      'cross-area change-coupled pairs: 3 -> 4',
    ])
  })
})

describe('literalRatchet', () => {
  const row = (literal: string, files: number) => ({
    literal,
    kind: 'path' as const,
    files,
    testFiles: 0,
    languages: [],
    areas: 1,
    sample: [],
  })

  it('passes at the baseline and fails one file above it', () => {
    expect(literalRatchet([row('/opt/data', 87)], { '/opt/data': 87 })).toEqual({
      rose: [],
      fell: [],
    })
    expect(literalRatchet([row('/opt/data', 88)], { '/opt/data': 87 }).rose).toEqual([
      '/opt/data: 88 files (allowed 87)',
    ])
  })

  it('fails a new literal at five files, and a count that fell below its baseline', () => {
    expect(literalRatchet([row('/run/new', 5)], {}).rose).toHaveLength(1)
    expect(literalRatchet([row('/run/new', 4)], {}).rose).toEqual([])
    expect(literalRatchet([], { '/opt/data': 87 }).fell).toEqual([
      '/opt/data: 0 files (baseline 87)',
    ])
  })
})

describe('isGenerated', () => {
  const tracked = new Set(['operator/bin/gen-seat-layout.py'])

  it('recognizes a header naming a tracked generator', () => {
    const text =
      '# GENERATED by operator/bin/gen-seat-layout.py from operator/contracts/x.yaml.\nX = 1\n'
    expect(isGenerated(text, tracked)).toBe(true)
  })

  it('does not trust a header naming a generator that does not exist, or one past line ten', () => {
    expect(isGenerated('# GENERATED by operator/bin/nope.py\n', tracked)).toBe(false)
    const late = `${'x = 1\n'.repeat(10)}# GENERATED by operator/bin/gen-seat-layout.py\n`
    expect(isGenerated(late, tracked)).toBe(false)
  })
})

describe('declaredCopies', () => {
  it('names every stamped copy: skill folders by copy_as, other trees by path, never a source', () => {
    const yaml = [
      'modules:',
      '  gate:',
      '    source: operator/templates/gate.py',
      '    copy_as: pre_run.py',
      '    skills: [alpha, beta]',
      '  rule:',
      '    source: operator/overlay/shared/rule.py',
      '    copies: [operator/connector/rule.py]',
    ].join('\n')
    expect([...declaredCopies(yaml)].sort()).toEqual([
      'operator/connector/rule.py',
      'operator/skills/alpha/pre_run.py',
      'operator/skills/beta/pre_run.py',
    ])
  })

  it('declares nothing for a manifest that does not parse or has no modules', () => {
    expect(declaredCopies('modules: [').size).toBe(0)
    expect(declaredCopies('other: 1\n').size).toBe(0)
  })
})

describe('sourceTextPins: TypeScript', () => {
  const ts = (...lines: string[]) => sourceTextPins('tests/x.test.ts', lines.join('\n'))
  const READ = "const src = readFileSync(resolve(import.meta.dirname, '../src/lib/x.ts'), 'utf8')"

  it('counts a wording assertion on a source file read as text', () => {
    expect(ts(READ, "it('a', () => { expect(src).toContain('export function x') })")).toBe(1)
    expect(
      ts(READ, 'expect(src).not.toMatch(/legacy/)', "expect(src.includes('y')).toBe(true)")
    ).toBe(2)
  })

  it('does not count a fixture read, a call into the code, or a script run and its output', () => {
    const fixture = "const t = readFileSync(resolve(import.meta.dirname, 'fixtures/x.txt'), 'utf8')"
    expect(ts(fixture, "expect(t).toContain('a')")).toBe(0)
    expect(ts("import { x } from '../src/lib/x'", "expect(x()).toContain('a')")).toBe(0)
    const run =
      "const out = execFileSync('bash', [resolve(import.meta.dirname, '../scripts/x.sh')], { encoding: 'utf8' })"
    expect(ts(run, "expect(out).toContain('ok')")).toBe(0)
  })

  it('does not count a file the test wrote to a temp directory, or a parsed structure', () => {
    const tmp = [
      "const dir = mkdtempSync(join(tmpdir(), 'x-'))",
      "expect(readFileSync(join(dir, 'out.txt'), 'utf8')).toContain('done')",
    ]
    expect(ts(...tmp)).toBe(0)
    const parsed =
      "const cfg = JSON.parse(readFileSync(resolve(import.meta.dirname, '../package.json'), 'utf8'))"
    expect(ts(parsed, "expect(cfg.scripts.verify).toContain('lint')")).toBe(0)
  })

  it('follows a read through a helper, a template path and a derived slice', () => {
    const helper = [
      "const ROOT = resolve(import.meta.dirname, '..')",
      "const read = (p: string) => readFileSync(p, 'utf8')",
      'const body = read(`${ROOT}/operator/skills/x/SKILL.md`)',
      "const step = body.slice(body.indexOf('## RUN'))",
      "expect(step).toContain('Never ask twice')",
    ]
    expect(ts(...helper)).toBe(1)
  })

  it('does not count the output of a helper that runs a script in a temp directory', () => {
    const helper = [
      "const SCRIPT = resolve(import.meta.dirname, '../operator/bin/x.sh')",
      'function run(): string {',
      "  const dir = mkdtempSync(join(tmpdir(), 'x-'))",
      "  return execFileSync('bash', [SCRIPT, dir], { encoding: 'utf8' })",
      '}',
      'const out = run()',
      "expect(out).toContain('ok')",
    ]
    expect(ts(...helper)).toBe(0)
  })

  it('counts a read through a shared tests/_support reader, and not when its text is run', () => {
    const seat = "const boot = readSeatScript('operator/templates/entrypoint.sh')"
    expect(ts(seat, "expect(boot).toContain('exec gosu')")).toBe(1)
    const run = [
      "const step = scriptDerivation('SLUG_HASH')",
      "const out = execFileSync('bash', ['-c', step], { encoding: 'utf8' })",
      "expect(out).toContain('abc')",
    ]
    expect(ts(...run)).toBe(0)
  })

  it('counts an assertion on a verdict computed from source text, whatever the matcher', () => {
    expect(
      ts(
        READ,
        'const missing = PHRASES.filter((p) => !src.includes(p))',
        'expect(missing).toEqual([])'
      )
    ).toBe(1)
  })

  it('scopes names by block: a local `text` from a render is not the `text` another test read', () => {
    const two = [
      "it('a', () => { const text = readFileSync(join(process.cwd(), 'src/a.ts'), 'utf8'); expect(text).toContain('x') })",
      "it('b', () => { const text = render(); expect(text).toContain('x') })",
    ]
    expect(ts(...two)).toBe(1)
  })

  it('ignores comments and string contents', () => {
    expect(
      ts(READ, "// expect(src).toContain('x')", 'const note = "expect(src).toContain(\'x\')"')
    ).toBe(0)
  })
})

describe('sourceTextPins: Python', () => {
  const py = (...lines: string[]) =>
    sourceTextPins('operator/x/tests/test_x.py', lines.join('\n') + '\n')
  const REPO = 'REPO = Path(__file__).resolve().parents[2]'

  it('counts `in`, re.search and .count on a repo file read as text', () => {
    const src = [
      REPO,
      'TEXT = (REPO / "operator/skills/x/SKILL.md").read_text()',
      '',
      'def test_a():',
      '    assert "never" in TEXT',
      '    assert re.search(r"^## RUN", TEXT, re.M)',
      '    assert TEXT.count("x") == 1',
    ]
    expect(py(...src)).toBe(3)
  })

  it('counts through a pytest fixture that reads the file', () => {
    const src = [
      '@pytest.fixture',
      'def skill():',
      '    return (Path(__file__).parent.parent / "SKILL.md").read_text()',
      '',
      'def test_a(skill):',
      '    assert "x" in skill',
    ]
    expect(py(...src)).toBe(1)
  })

  it('does not count a fixture file, tmp_path output, a subprocess, the code called, or parsed YAML', () => {
    expect(
      py(
        'FIX = Path(__file__).parent / "fixtures" / "a.txt"',
        'def test_a():',
        '    assert "x" in FIX.read_text()'
      )
    ).toBe(0)
    expect(
      py(
        'def test_a(tmp_path):',
        '    run(tmp_path)',
        '    assert "done" in (tmp_path / "out.txt").read_text()'
      )
    ).toBe(0)
    const sub = [
      REPO,
      'def test_a():',
      '    out = subprocess.run([str(REPO / "bin/x")], capture_output=True, text=True).stdout',
      '    assert "ok" in out',
    ]
    expect(py(...sub)).toBe(0)
    expect(py('from jobs.x import line', 'def test_a():', '    assert "x" in line()')).toBe(0)
    const yaml = [
      REPO,
      'CFG = yaml.safe_load((REPO / "operator/c.yaml").read_text())',
      'def test_a():',
      '    assert "lane" in CFG',
    ]
    expect(py(...yaml)).toBe(0)
  })

  it('does not count a migration run in sqlite and the rows it produced', () => {
    const src = [
      REPO,
      'def test_a():',
      '    conn = sqlite3.connect(":memory:")',
      '    conn.executescript((REPO / "migrations/0009.sql").read_text())',
      '    cols = conn.execute("PRAGMA table_info(t)").fetchall()',
      '    assert "r2_key" in [c[1] for c in cols]',
    ]
    expect(py(...src)).toBe(0)
  })

  it('scopes names by def: one test reading `text` from a file does not taint another', () => {
    const src = [
      REPO,
      'def test_a():',
      '    text = (REPO / "src/a.py").read_text()',
      '    assert "x" in text',
      '',
      'def test_b():',
      '    text = render()',
      '    assert "x" in text',
    ]
    expect(py(...src)).toBe(1)
  })
})

describe('pinLangOf', () => {
  it('reads TypeScript and Python test files, never a fixture or a non-test file', () => {
    expect(pinLangOf('tests/a.test.ts')).toBe('ts')
    expect(pinLangOf('operator/bin/tests/test_a.py')).toBe('py')
    expect(pinLangOf('tests/fixtures/test_a.py')).toBeNull()
    expect(pinLangOf('src/lib/a.ts')).toBeNull()
  })
})

describe('textPinCensus and textPinRatchet', () => {
  const pin =
    "const s = readFileSync(join(process.cwd(), 'src/a.ts'), 'utf8')\nexpect(s).toContain('a')\n"

  it('counts every file with pins, allowlisted policy scans included, and none without', () => {
    const census = textPinCensus([
      { path: 'tests/a.test.ts', text: pin },
      { path: 'tests/policy.test.ts', text: pin + pin },
      { path: 'tests/clean.test.ts', text: 'expect(f()).toBe(1)\n' },
    ])
    expect(census).toEqual({ 'tests/a.test.ts': 1, 'tests/policy.test.ts': 2 })
  })

  it('caps an allowlisted file: a pin added to it fails, a pin removed without lowering fails', () => {
    const baseline = { 'tests/policy.test.ts': 2 }
    const added = textPinCensus([{ path: 'tests/policy.test.ts', text: pin + pin + pin }])
    expect(textPinRatchet(added, baseline).rose).toEqual([
      'tests/policy.test.ts: 3 pins (allowed 2)',
    ])
    const removed = textPinCensus([{ path: 'tests/policy.test.ts', text: pin }])
    expect(textPinRatchet(removed, baseline).fell).toEqual([
      'tests/policy.test.ts: 1 pins (baseline 2)',
    ])
    expect(
      textPinRatchet(textPinCensus([{ path: 'tests/policy.test.ts', text: pin + pin }]), baseline)
    ).toEqual({ rose: [], fell: [] })
  })

  it('names every file holding pins without a stated reason', () => {
    const census = { 'tests/a.test.ts': 1, 'tests/policy.test.ts': 2 }
    expect(unexplainedPinFiles(census, new Set(['tests/policy.test.ts']))).toEqual([
      'tests/a.test.ts',
    ])
    expect(unexplainedPinFiles(census, new Set(Object.keys(census)))).toEqual([])
  })

  it('fails a rise, a new file with pins, and a fall the baseline was not lowered for', () => {
    expect(textPinRatchet({ 'tests/a.test.ts': 2 }, { 'tests/a.test.ts': 2 })).toEqual({
      rose: [],
      fell: [],
    })
    expect(textPinRatchet({ 'tests/a.test.ts': 3 }, { 'tests/a.test.ts': 2 }).rose).toEqual([
      'tests/a.test.ts: 3 pins (allowed 2)',
    ])
    expect(textPinRatchet({ 'tests/new.test.ts': 1 }, {}).rose).toEqual([
      'tests/new.test.ts: 1 pins (allowed 0)',
    ])
    expect(textPinRatchet({}, { 'tests/a.test.ts': 2 }).fell).toEqual([
      'tests/a.test.ts: 0 pins (baseline 2)',
    ])
  })
})

describe('comment lines', () => {
  it('a path named only in comments is not counted; the same path in code is', () => {
    const comment = files(6, 'operator/a', 'py', '# reads /run/smd-jobs/queue\nx = 1\n')
    expect(literalCensus(comment)).toEqual([])
    const code = files(6, 'operator/a', 'py', 'Q = "/run/smd-jobs/queue"  # the queue\n')
    expect(literalCensus(code).map((r) => r.literal)).toEqual(['/run/smd-jobs/queue'])
  })
})
