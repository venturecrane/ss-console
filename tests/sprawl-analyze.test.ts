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
} from '../scripts/sprawl/analyze'

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

describe('comment lines', () => {
  it('a path named only in comments is not counted; the same path in code is', () => {
    const comment = files(6, 'operator/a', 'py', '# reads /run/smd-jobs/queue\nx = 1\n')
    expect(literalCensus(comment)).toEqual([])
    const code = files(6, 'operator/a', 'py', 'Q = "/run/smd-jobs/queue"  # the queue\n')
    expect(literalCensus(code).map((r) => r.literal)).toEqual(['/run/smd-jobs/queue'])
  })
})
