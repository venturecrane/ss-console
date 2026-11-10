/**
 * Sprawl audit: the CLI half. Gathers inputs (git, jscpd, vulture), hands them
 * to scripts/sprawl/analyze.ts, prints a summary and writes a JSON snapshot.
 * Every count is computed in the pure half and tested there
 * (tests/sprawl-analyze.test.ts). The skill that runs this is
 * .claude/skills/sprawl-audit/SKILL.md.
 *
 * Usage:
 *   npx tsx scripts/sprawl-audit.ts                       # measure, print
 *   npx tsx scripts/sprawl-audit.ts --out snap.json       # also write the snapshot
 *   npx tsx scripts/sprawl-audit.ts --compare prev.json   # print the change against a snapshot
 *   npx tsx scripts/sprawl-audit.ts --since 6.months      # change-coupling window (default 6.months)
 *   npx tsx scripts/sprawl-audit.ts --compare scripts/sprawl/snapshot.json --strict
 *       # exit 1 when any headline number rose since the accepted snapshot (the weekly workflow)
 *   npx tsx scripts/sprawl-audit.ts --out scripts/sprawl/snapshot.json
 *       # accept the current state as the new snapshot (only after it went DOWN, in a PR)
 *   npx tsx scripts/sprawl-audit.ts --write-literal-baseline
 *       # rewrite scripts/sprawl/literal-baseline.json from the tree (literal census only, fast);
 *       # run after removing restated literals, so the CI ratchet locks the lower count in
 *   npx tsx scripts/sprawl-audit.ts --text-pins
 *       # list every source-text pin outside the allowlisted policy scans, file:line
 *   npx tsx scripts/sprawl-audit.ts --write-text-pin-baseline
 *       # rewrite scripts/sprawl/text-pin-baseline.json from the tree; run after removing pins
 *   npx tsx scripts/sprawl-audit.ts --concepts
 *       # the "built N times" census: each concept's count, target, rule and every instance
 *   npx tsx scripts/sprawl-audit.ts --write-concept-baseline
 *       # rewrite scripts/sprawl/concept-baseline.json from the tree; run after a count fell
 *
 * Tool versions are pinned so two runs measure with the same instrument.
 */

import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  type Clone,
  type FileFacts,
  type Headline,
  type SourceFile,
  changeCoupling,
  regressions,
  literalBaselineOf,
  literalCensus,
  summarizeClones,
  summarizeVulture,
  textPinCensus,
} from './sprawl/analyze'
import {
  LITERAL_BASELINE,
  TEXT_PIN_BASELINE,
  generatedPaths,
  readSources,
  readTestFiles,
  trackedFiles,
} from './sprawl/sources'
import { TEXT_PIN_FALSE_POSITIVES, TEXT_PIN_POLICY_SCANS } from './sprawl/text-pin-allowlist'
import { CONCEPT_BASELINE, conceptBaselineDoc, conceptCensus } from './sprawl/concepts'
import { sourceTextPinLines } from './sprawl/text-pins'

const JSCPD = 'jscpd@4.3.0'
const VULTURE = 'vulture==2.14'
const CLONE_DIRS = ['src', 'operator', 'scripts', 'tests', '.claude/hooks', '.claude/bin']

type Args = {
  out?: string
  compare?: string
  since: string
  writeBaseline: boolean
  strict: boolean
  textPins: boolean
  writePinBaseline: boolean
  concepts: boolean
  writeConceptBaseline: boolean
}

const FLAGS = new Set([
  '--write-literal-baseline',
  '--strict',
  '--text-pins',
  '--write-text-pin-baseline',
  '--concepts',
  '--write-concept-baseline',
])

function parseArgs(argv: string[]): Args {
  const args: Args = {
    since: '6.months',
    writeBaseline: false,
    strict: false,
    textPins: false,
    writePinBaseline: false,
    concepts: false,
    writeConceptBaseline: false,
  }
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i + 1]
    if (argv[i] === '--write-literal-baseline') args.writeBaseline = true
    else if (argv[i] === '--strict') args.strict = true
    else if (argv[i] === '--text-pins') args.textPins = true
    else if (argv[i] === '--write-text-pin-baseline') args.writePinBaseline = true
    else if (argv[i] === '--concepts') args.concepts = true
    else if (argv[i] === '--write-concept-baseline') args.writeConceptBaseline = true
    else if (argv[i] === '--out') args.out = v
    else if (argv[i] === '--compare') args.compare = v
    else if (argv[i] === '--since') args.since = v
    else throw new Error(`unknown argument ${argv[i]}`)
    if (!FLAGS.has(argv[i])) i++
  }
  if (args.strict && !args.compare) throw new Error('--strict needs --compare <snapshot>')
  return args
}

function run(cmd: string, argv: string[]): string {
  return execFileSync(cmd, argv, {
    encoding: 'utf8',
    maxBuffer: 1 << 30,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

function factsFor(sources: SourceFile[]): Map<string, FileFacts> {
  const m = new Map<string, FileFacts>()
  for (const s of sources) {
    m.set(s.path, {
      lines: s.text.split('\n').length,
      sha: createHash('sha256').update(s.text).digest('hex'),
    })
  }
  return m
}

function asLoc(v: unknown): Clone['a'] {
  const o = v as Record<string, unknown> | null
  if (
    !o ||
    typeof o.name !== 'string' ||
    typeof o.start !== 'number' ||
    typeof o.end !== 'number'
  ) {
    throw new Error('jscpd report: a duplicate has no file location')
  }
  return { name: o.name, start: o.start, end: o.end }
}

function parseJscpd(text: string): { clones: Clone[]; percentage: number } {
  const raw = JSON.parse(text) as {
    duplicates?: unknown
    statistics?: { total?: { percentage?: unknown } }
  }
  const pct = raw.statistics?.total?.percentage
  if (!Array.isArray(raw.duplicates) || typeof pct !== 'number')
    throw new Error('jscpd report: unexpected shape')
  const clones = raw.duplicates.map((d: unknown) => {
    const o = d as Record<string, unknown>
    if (typeof o.lines !== 'number') throw new Error('jscpd report: a duplicate has no line count')
    return { lines: o.lines, a: asLoc(o.firstFile), b: asLoc(o.secondFile) }
  })
  return { clones, percentage: pct }
}

function runJscpd(): { clones: Clone[]; percentage: number } {
  const dir = mkdtempSync(join(tmpdir(), 'sprawl-'))
  run('npx', [
    '-y',
    JSCPD,
    ...CLONE_DIRS,
    '--format',
    'typescript,javascript,python,bash,tsx',
    '--min-tokens',
    '70',
    '--min-lines',
    '8',
    '--ignore',
    '**/node_modules/**,**/dist/**,**/.venv/**,**/__pycache__/**,**/fixtures/**',
    '--reporters',
    'json',
    '--output',
    dir,
    '--silent',
  ])
  return parseJscpd(readFileSync(join(dir, 'jscpd-report.json'), 'utf8'))
}

const VULTURE_ARGS = [
  'operator',
  '--min-confidence',
  '60',
  '--exclude',
  '*/tests/*,*/.venv/*,*/node_modules/*,test_*.py,conftest.py',
]

/** uvx on a laptop; CI installs the pinned version with pip and sets SPRAWL_VULTURE=python. */
function runVulture(): string {
  const viaPip = process.env.SPRAWL_VULTURE === 'python'
  try {
    return viaPip
      ? run('python3', ['-m', 'vulture', ...VULTURE_ARGS])
      : run('uvx', ['--from', VULTURE, 'vulture', ...VULTURE_ARGS])
  } catch (e) {
    // vulture exits 3 when it finds candidates; its stdout is the report.
    const out = (e as { stdout?: unknown }).stdout
    if (typeof out === 'string' && out.length > 0) return out
    throw e
  }
}

function commitFiles(since: string): string[][] {
  const raw = run('git', [
    'log',
    `--since=${since}`,
    '--no-merges',
    '--no-renames',
    '--name-only',
    '--format=%x00',
  ])
  return raw
    .split('\0')
    .map((c) => c.split('\n').filter(Boolean))
    .filter((c) => c.length > 0)
}

function measure(since: string) {
  const tracked = trackedFiles()
  const sources = readSources(tracked)
  // A generated file is one source projected per tree (isGenerated), not a copy.
  const generated = generatedPaths(tracked)
  const handWritten = (c: Clone) => !generated.has(c.a.name) && !generated.has(c.b.name)
  const { clones, percentage } = runJscpd()
  const commits = commitFiles(since)
  const vulture = runVulture()
    .split('\n')
    .filter((line) => !generated.has(line.split(':', 1)[0]))
    .join('\n')
  return {
    measuredAt: new Date().toISOString(),
    commit: run('git', ['rev-parse', 'HEAD']).trim(),
    instruments: { jscpd: JSCPD, vulture: VULTURE, couplingWindow: since, commits: commits.length },
    clones: {
      duplicatedPct: percentage,
      ...summarizeClones(clones.filter(handWritten), factsFor(sources)),
    },
    literals: literalCensus(sources),
    coupling: changeCoupling(commits),
    dead: summarizeVulture(vulture),
  }
}

type Snapshot = ReturnType<typeof measure>

function headline(s: Snapshot): Headline {
  return {
    copiedLines: s.clones.copiedLines,
    partialLines: s.clones.partialLines,
    literals: s.literals.length,
    coupling: s.coupling.length,
    dead: s.dead.candidates,
  }
}

function delta(cur: number, prev: number | undefined): string {
  if (prev === undefined) return ''
  const d = cur - prev
  return d === 0 ? ' (=)' : ` (${d > 0 ? '+' : ''}${d})`
}

function print(s: Snapshot, prev?: Snapshot): void {
  const c = s.clones
  const p = prev?.clones
  console.log(`Sprawl audit at ${s.commit.slice(0, 9)} (${s.measuredAt.slice(0, 10)})`)
  console.log(`Clones: ${c.duplicatedPct}% duplicated lines overall (jscpd, tests included).`)
  console.log(
    `  Whole-file copies (vendored): ${c.copiedFiles} files, ${c.copiedLines} lines${delta(c.copiedLines, p?.copiedLines)}`
  )
  console.log(
    `  Partial clones in production code: ${c.partialClones} clones, ${c.partialLines} lines${delta(c.partialLines, p?.partialLines)}`
  )
  for (const a of c.byArea.slice(0, 8)) console.log(`    ${String(a.lines).padStart(5)}  ${a.area}`)
  const prevLit = new Map((prev?.literals ?? []).map((r) => [r.literal, r.files]))
  console.log(
    `Restated literals (5+ non-test files): ${s.literals.length}${delta(s.literals.length, prev?.literals.length)}`
  )
  for (const r of s.literals.slice(0, 15)) {
    console.log(
      `  ${String(r.files).padStart(4)} files ${r.kind.padEnd(4)} ${r.literal} [${r.languages.join(', ')}]${delta(r.files, prevLit.get(r.literal))}`
    )
  }
  console.log(
    `Change coupling across areas (${s.instruments.commits} commits, ${s.instruments.couplingWindow}): ${s.coupling.length} pairs${delta(s.coupling.length, prev?.coupling.length)}`
  )
  for (const r of s.coupling.slice(0, 12))
    console.log(`  ${r.shared} shared, ${r.degree}%  ${r.a}  <->  ${r.b}`)
  console.log(
    `Dead-code candidates (vulture 60%+, Python): ${s.dead.candidates}${delta(s.dead.candidates, prev?.dead.candidates)} ${JSON.stringify(s.dead.byKind)}`
  )
}

/** Every source-text pin outside the allowlist, one `file:line` per assertion, then the total. */
/** Every source-text pin as `file:line [kind]`: guard, false-positive, or UNEXPLAINED (fails the ratchet). */
function printTextPins(): void {
  const tally: Record<string, number> = { guard: 0, 'false-positive': 0, UNEXPLAINED: 0 }
  for (const f of readTestFiles(trackedFiles())) {
    const kind = TEXT_PIN_POLICY_SCANS[f.path]
      ? 'guard'
      : TEXT_PIN_FALSE_POSITIVES[f.path]
        ? 'false-positive'
        : 'UNEXPLAINED'
    for (const line of sourceTextPinLines(f.path, f.text)) {
      console.log(`${f.path}:${line} [${kind}]`)
      tally[kind]++
    }
  }
  console.log(
    `source-text pins: ${tally.guard} in allowlisted guards, ${tally['false-positive']} known false positives, ${tally.UNEXPLAINED} unexplained`
  )
}

/** Cap every file at its current count (guards included). Run only after a count fell. */
function writePinBaseline(): void {
  const census = textPinCensus(readTestFiles(trackedFiles()))
  const doc = {
    $comment:
      'Written by scripts/sprawl-audit.ts --write-text-pin-baseline; gated by tests/text-pin-ratchet.test.ts. Lower only.',
    ...census,
  }
  writeFileSync(TEXT_PIN_BASELINE, JSON.stringify(doc, null, 2) + '\n')
  console.log(`${TEXT_PIN_BASELINE}: ${Object.keys(census).length} files`)
}

/** Each concept's count against its target, then every instance, one per line. */
function printConcepts(): void {
  for (const r of conceptCensus(readSources(trackedFiles()))) {
    console.log(`${r.id}: ${r.count} (target ${r.target})\n  rule: ${r.rule}`)
    for (const p of r.instances) console.log(`    ${p}`)
  }
}

function writeConceptBaseline(): void {
  const rows = conceptCensus(readSources(trackedFiles()))
  writeFileSync(CONCEPT_BASELINE, JSON.stringify(conceptBaselineDoc(rows), null, 2) + '\n')
  console.log(`${CONCEPT_BASELINE}: ${rows.map((r) => `${r.id}=${r.count}`).join(' ')}`)
}

const args = parseArgs(process.argv.slice(2))
if (args.concepts) {
  printConcepts()
} else if (args.writeConceptBaseline) {
  writeConceptBaseline()
} else if (args.textPins) {
  printTextPins()
} else if (args.writePinBaseline) {
  writePinBaseline()
} else if (args.writeBaseline) {
  const baseline = literalBaselineOf(literalCensus(readSources(trackedFiles())))
  const doc = {
    $comment:
      'Written by scripts/sprawl-audit.ts --write-literal-baseline; gated by tests/sprawl-ratchet.test.ts. Lower only.',
    ...baseline,
  }
  writeFileSync(LITERAL_BASELINE, JSON.stringify(doc, null, 2) + '\n')
  console.log(`${LITERAL_BASELINE}: ${Object.keys(baseline).length} literals`)
} else {
  const snap = measure(args.since)
  const prev = args.compare
    ? (JSON.parse(readFileSync(args.compare, 'utf8')) as Snapshot)
    : undefined
  print(snap, prev)
  if (args.out) writeFileSync(args.out, JSON.stringify(snap, null, 2) + '\n')
  if (prev) {
    const rose = regressions(headline(snap), headline(prev))
    console.log(
      rose.length === 0
        ? 'No headline number rose.'
        : `ROSE:\n${rose.map((r) => `  ${r}`).join('\n')}`
    )
    if (args.strict && rose.length > 0) process.exitCode = 1
  }
}
