/**
 * Sprawl audit: the pure half. Every number the audit reports is computed here,
 * from inputs the CLI half (scripts/sprawl-audit.ts) gathers, so each rule is
 * gated by tests/sprawl-analyze.test.ts on a case with a known answer.
 *
 * Four measures, each the industry-standard instrument for one kind of sprawl:
 *
 * 1. Clones (jscpd). Copied blocks of code. Split into WHOLE-FILE COPIES (a
 *    file vendored into several places, usually kept identical by a sync test
 *    such as operator/tests/test_escalation_ledger_sync.py) and PARTIAL clones,
 *    a block copied inside otherwise different files, which nothing keeps in
 *    step. Whole-file copies are a cost on every edit, not a defect.
 * 2. Restated literals. A boundary-crossing value (an absolute path, an
 *    environment-variable name) written out in many files instead of read from
 *    one definition: connascence of meaning spread across distance
 *    (connascence.io/meaning.html). This is what made the ADR 0103 rename touch
 *    59 files for 26 env names. Clone detectors cannot see it: one string is
 *    never a clone.
 * 3. Change coupling (Tornhill, "Your Code as a Crime Scene"; code-maat).
 *    Files in different areas that keep changing in the same commit: the
 *    observed signature of Shotgun Surgery (Fowler, Refactoring ch. 3).
 * 4. Dead-code candidates (vulture for Python; knip already gates TypeScript in
 *    verify.yml). Reported as candidates, because vulture's 60% tier includes
 *    code only tests call.
 * 5. Source-text pins (change-detector tests; Google Testing Blog, "Change-
 *    Detector Tests Considered Harmful"). A test that reads a repo source or doc
 *    file as text and asserts on its wording instead of running the code. The
 *    detector is scripts/sprawl/text-pins.ts; textPinCensus counts it per file.
 */

import { sourceTextPins } from './text-pins'

export type Clone = {
  lines: number
  a: { name: string; start: number; end: number }
  b: { name: string; start: number; end: number }
}

export type FileFacts = { lines: number; sha: string }

export type CloneSummary = {
  copiedFiles: number
  copiedLines: number
  partialClones: number
  partialLines: number
  byArea: Array<{ area: string; lines: number }>
  largest: Clone[]
}

const TEST_PATH = /(^|\/)tests?\/|(^|\/)test_[^/]*$|\.test\.[a-z]+$|_test\.py$|(^|\/)conftest\.py$/

export function isTestPath(path: string): boolean {
  return TEST_PATH.test(path)
}

/** The area a file belongs to: operator/<pkg>, operator/skills/<skill>, else top two segments. */
export function areaOf(path: string): string {
  const parts = path.split('/')
  if (parts[0] === 'operator' && parts[1] === 'skills') return parts.slice(0, 3).join('/')
  return parts.slice(0, 2).join('/')
}

/**
 * A whole-file copy: the clone covers at least 90% of BOTH files. Byte identity
 * is not required, because a vendored copy often carries a provenance header
 * (operator/hermes-smd-overlay/shared/sticky_stop.py: five comment lines, then
 * the canonical byte for byte). Whether a sync test guards a copy is a separate
 * question; this counts the copies themselves.
 */
function isWholeFileCopy(c: Clone, facts: Map<string, FileFacts>): boolean {
  const fa = facts.get(c.a.name)
  const fb = facts.get(c.b.name)
  if (!fa || !fb || fa.lines === 0 || fb.lines === 0) return false
  return c.lines >= Math.floor(fa.lines * 0.9) && c.lines >= Math.floor(fb.lines * 0.9)
}

export function summarizeClones(clones: Clone[], facts: Map<string, FileFacts>): CloneSummary {
  const copied = new Set<string>()
  const partial: Clone[] = []
  for (const c of clones) {
    if (isTestPath(c.a.name) || isTestPath(c.b.name)) continue
    if (isWholeFileCopy(c, facts)) {
      copied.add(c.a.name)
      copied.add(c.b.name)
    } else {
      partial.push(c)
    }
  }
  const area = new Map<string, number>()
  for (const c of partial) area.set(areaOf(c.a.name), (area.get(areaOf(c.a.name)) ?? 0) + c.lines)
  let copiedLines = 0
  for (const f of copied) copiedLines += facts.get(f)?.lines ?? 0
  return {
    copiedFiles: copied.size,
    copiedLines,
    partialClones: partial.length,
    partialLines: partial.reduce((n, c) => n + c.lines, 0),
    byArea: [...area].map(([a, lines]) => ({ area: a, lines })).sort((x, y) => y.lines - x.lines),
    largest: [...partial].sort((x, y) => y.lines - x.lines).slice(0, 15),
  }
}

export type SourceFile = { path: string; text: string }

export type LiteralRow = {
  literal: string
  kind: 'path' | 'env'
  /** Distinct non-test files restating the literal: the cost of changing it. */
  files: number
  /** Test files restating it, reported apart: a test that sets an env var by name is expected. */
  testFiles: number
  languages: string[]
  areas: number
  sample: string[]
}

/** Absolute paths on a seat. Anchored at a known root so prose like "and/or" never matches. */
const PATH_LITERAL =
  /(?<![\w.])\/(?:opt|run|var\/lib|etc\/smd)\/[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*/g

/**
 * An env-var name is an UPPER_SNAKE token (with at least one underscore, so a
 * word like URL or WHY is never one) read through an accessor somewhere in the
 * tree. The `${NAME}` form is read from shell files only: in TypeScript it is a
 * template literal, and the first baseline run counted `${WHY}` as an env var.
 */
const NAME = '([A-Z][A-Z0-9]*_[A-Z0-9_]+)'
const ENV_ACCESS = [
  new RegExp(`os\\.environ(?:\\.get)?\\s*[[(]\\s*["']${NAME}["']`, 'g'),
  new RegExp(`os\\.getenv\\(\\s*["']${NAME}["']`, 'g'),
  new RegExp(`process\\.env\\.${NAME}`, 'g'),
  new RegExp(`process\\.env\\[\\s*["']${NAME}["']`, 'g'),
]
const SHELL_ENV_ACCESS = new RegExp(`\\$\\{${NAME}(?::?[-=?+][^}]*)?\\}`, 'g')

/**
 * Names a platform defines (GitHub Actions' GITHUB_* and RUNNER_*). Every
 * workflow step that writes GITHUB_OUTPUT must name it; there is no home of
 * ours to give it, so restating it is the platform's interface, not sprawl.
 */
const PLATFORM_ENV = /^(GITHUB|RUNNER)_/

/**
 * The seat layout's generated shell constants (operator/templates/_seat-layout.generated.sh,
 * from operator/contracts/seat-layout.yaml). `${SMD_LAYOUT_JOBS_RUN}` is a read of the one
 * declaration, as `seat_layout.JOBS_RUN` is in Python: the cure for a restated path, not
 * an environment variable restated (the file assigns them and exports nothing).
 */
const LAYOUT_CONSTANT = /^SMD_LAYOUT_/

export function languageOf(path: string): string {
  const m = /\.([a-z]+)$/.exec(path)
  const ext = m ? m[1] : ''
  if (['ts', 'tsx', 'mjs', 'js', 'astro', 'cjs'].includes(ext)) return 'ts/js'
  if (ext === 'py') return 'python'
  if (ext === 'sh' || ext === '' || ext === 'bash') return 'shell'
  if (['yml', 'yaml', 'toml', 'json'].includes(ext)) return 'config'
  if (ext === 'md') return 'docs'
  return ext
}

export function envNames(files: SourceFile[]): Set<string> {
  const names = new Set<string>()
  for (const f of files) {
    const patterns = languageOf(f.path) === 'shell' ? [...ENV_ACCESS, SHELL_ENV_ACCESS] : ENV_ACCESS
    for (const re of patterns) {
      for (const m of f.text.matchAll(re)) {
        if (!PLATFORM_ENV.test(m[1]) && !LAYOUT_CONSTANT.test(m[1])) names.add(m[1])
      }
    }
  }
  return names
}

function addHit(map: Map<string, Set<string>>, key: string, path: string): void {
  const set = map.get(key) ?? new Set<string>()
  set.add(path)
  map.set(key, set)
}

/** Count, per literal, the distinct files that restate it. Docs are excluded: prose naming a path is not code. */
/**
 * A file another program wrote: a `GENERATED by <path>` marker in its first
 * ten lines naming a tracked file. Its restatements are the generator's single
 * projection of one source (operator/bin/gen-seat-layout.py writes one module
 * per install tree), so they are not sprawl. The generator must exist: a marker
 * naming nothing is a hand-written file wearing a label.
 */
export function isGenerated(text: string, tracked: ReadonlySet<string>): boolean {
  const head = text.split('\n', 10).join('\n')
  const m = /GENERATED by (\S+?)(?=[\s,;:]|\.\s|\.$|$)/.exec(head)
  return m !== null && tracked.has(m[1])
}

/** Comment lines (`#`, `//`, `*`, `/*` first on the line): prose naming a path, kept greppable, not counted. */
const COMMENT_LINE = /^\s*(#|\/\/|\/\*|\*)/

function codeLines(text: string): string {
  return text
    .split('\n')
    .filter((line) => !COMMENT_LINE.test(line))
    .join('\n')
}

export function literalCensus(files: SourceFile[], minFiles = LITERAL_MIN_FILES): LiteralRow[] {
  const code = files.filter((f) => languageOf(f.path) !== 'docs')
  const env = envNames(code)
  const paths = new Map<string, Set<string>>()
  const envHits = new Map<string, Set<string>>()
  // `seat_layout.CUSTOMER_YAML` reads the generated layout module (operator/bin/
  // gen-seat-layout.py): the cure for a restated path, not a restated env name,
  // even when a constant shares its spelling with one.
  const word = /(?<!\bseat_layout\.)\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b/g
  for (const f of code) {
    const text = codeLines(f.text)
    // A path that ends a sentence, trailing period and all, is the same path.
    for (const m of text.matchAll(PATH_LITERAL)) addHit(paths, m[0].replace(/[/.]+$/, ''), f.path)
    for (const m of text.matchAll(word)) if (env.has(m[0])) addHit(envHits, m[0], f.path)
  }
  const rows: LiteralRow[] = []
  const emit = (map: Map<string, Set<string>>, kind: LiteralRow['kind']) => {
    for (const [literal, set] of map) {
      const list = [...set].filter((p) => !isTestPath(p)).sort()
      if (list.length < minFiles) continue
      rows.push({
        literal,
        kind,
        files: list.length,
        testFiles: set.size - list.length,
        languages: [...new Set(list.map(languageOf))].sort(),
        areas: new Set(list.map(areaOf)).size,
        sample: list.slice(0, 5),
      })
    }
  }
  emit(paths, 'path')
  emit(envHits, 'env')
  return rows.sort((x, y) => y.files - x.files || x.literal.localeCompare(y.literal))
}

export const LITERAL_MIN_FILES = 5

export type RatchetResult = { rose: string[]; fell: string[] }

/**
 * The fitness function for restated literals. A literal may not appear in more
 * non-test files than the baseline allows; a literal not in the baseline may
 * not reach LITERAL_MIN_FILES. A count that FELL also fails, until the baseline
 * is lowered to match, so an improvement is locked in rather than spent by the
 * next PR (the same strict shape as tests/jobs-volume-old-path-allowlist.test.ts).
 *
 * @public Merge-gate check. tests/sprawl-ratchet.test.ts imports it and fails a PR that restates a literal past its baseline.
 */
export function literalRatchet(
  rows: LiteralRow[],
  baseline: Record<string, number>
): RatchetResult {
  const now = new Map(rows.map((r) => [r.literal, r.files]))
  return countRatchet(now, baseline, LITERAL_MIN_FILES - 1, 'files')
}

/**
 * The strict ratchet shared by both gates: a key may not exceed its baseline (a
 * key absent from the baseline may not exceed `unlisted`), and a key below its
 * baseline also fails until the baseline is lowered to match.
 */
function countRatchet(
  now: Map<string, number>,
  baseline: Record<string, number>,
  unlisted: number,
  unit: string
): RatchetResult {
  const rose: string[] = []
  const fell: string[] = []
  for (const [key, n] of now) {
    const allowed = baseline[key] ?? unlisted
    if (n > allowed) rose.push(`${key}: ${n} ${unit} (allowed ${allowed})`)
  }
  for (const [key, allowed] of Object.entries(baseline)) {
    const n = now.get(key) ?? 0
    if (n < allowed) fell.push(`${key}: ${n} ${unit} (baseline ${allowed})`)
  }
  return { rose: rose.sort(), fell: fell.sort() }
}

/**
 * Source-text pin assertions per test file, every test file (allowlisted policy
 * scans included, so each is capped too). Files with none are omitted.
 */
export function textPinCensus(files: SourceFile[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const f of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    const n = sourceTextPins(f.path, f.text)
    if (n > 0) out[f.path] = n
  }
  return out
}

/**
 * Files that hold pins with no stated reason: in the census but in neither the
 * allowlist of guards nor the list of known detector false positives
 * (scripts/sprawl/text-pin-allowlist.ts). A pin needs a reason AND a cap.
 *
 * @public Merge-gate check. tests/text-pin-ratchet.test.ts imports it.
 */
export function unexplainedPinFiles(
  census: Record<string, number>,
  reasons: ReadonlySet<string>
): string[] {
  return Object.keys(census)
    .filter((path) => !reasons.has(path))
    .sort()
}

/**
 * The fitness function for source-text pins: every file is capped at its count
 * in scripts/sprawl/text-pin-baseline.json, allowlisted guards included, so a
 * new pin in a guard file fails like a new pin anywhere; a file not in it may
 * have none; and a count that fell fails until the baseline is lowered.
 *
 * @public Merge-gate check. tests/text-pin-ratchet.test.ts imports it and fails a PR that adds a source-text pin.
 */
export function textPinRatchet(
  census: Record<string, number>,
  baseline: Record<string, number>
): RatchetResult {
  return countRatchet(new Map(Object.entries(census)), baseline, 0, 'pins')
}

export function literalBaselineOf(rows: LiteralRow[]): Record<string, number> {
  const out: Record<string, number> = {}
  for (const r of [...rows].sort((a, b) => a.literal.localeCompare(b.literal)))
    out[r.literal] = r.files
  return out
}

export type CouplingRow = { a: string; b: string; shared: number; degree: number }

export type CouplingOptions = {
  maxChangeset: number
  minRevs: number
  minShared: number
  minDegree: number
}

export const DEFAULT_COUPLING: CouplingOptions = {
  maxChangeset: 30,
  minRevs: 5,
  minShared: 5,
  minDegree: 50,
}

/**
 * code-maat's coupling measure: shared revisions over the average of the two
 * files' revisions, as a percentage. Only pairs in different areas, neither a
 * test: a source file and its test, or two files in one package, are expected
 * to change together.
 */
function countCrossAreaPairs(files: string[], shared: Map<string, number>): void {
  for (let i = 0; i < files.length; i++) {
    for (let j = i + 1; j < files.length; j++) {
      if (areaOf(files[i]) === areaOf(files[j])) continue
      const key = `${files[i]}\u0000${files[j]}`
      shared.set(key, (shared.get(key) ?? 0) + 1)
    }
  }
}

export function changeCoupling(
  commits: string[][],
  opts: CouplingOptions = DEFAULT_COUPLING
): CouplingRow[] {
  const revs = new Map<string, number>()
  const shared = new Map<string, number>()
  for (const raw of commits) {
    const files = [...new Set(raw)].filter((f) => !isTestPath(f)).sort()
    if (files.length === 0 || files.length > opts.maxChangeset) continue
    for (const f of files) revs.set(f, (revs.get(f) ?? 0) + 1)
    countCrossAreaPairs(files, shared)
  }
  const rows: CouplingRow[] = []
  for (const [key, n] of shared) {
    const [a, b] = key.split('\u0000')
    const ra = revs.get(a) ?? 0
    const rb = revs.get(b) ?? 0
    if (n < opts.minShared || ra < opts.minRevs || rb < opts.minRevs) continue
    const degree = Math.round((100 * n) / ((ra + rb) / 2))
    if (degree >= opts.minDegree) rows.push({ a, b, shared: n, degree })
  }
  return rows.sort((x, y) => y.shared - x.shared || y.degree - x.degree)
}

/** The headline numbers of a snapshot, the only part two runs are compared on. */
export type Headline = {
  copiedLines: number
  partialLines: number
  literals: number
  coupling: number
  dead: number
}

/** Every headline number that ROSE since the accepted snapshot. Empty means no new sprawl. */
export function regressions(cur: Headline, prev: Headline): string[] {
  const keys: Array<[keyof Headline, string]> = [
    ['copiedLines', 'whole-file copy lines'],
    ['partialLines', 'partial clone lines'],
    ['literals', 'literals restated in 5+ files'],
    ['coupling', 'cross-area change-coupled pairs'],
    ['dead', 'dead-code candidates'],
  ]
  return keys
    .filter(([k]) => cur[k] > prev[k])
    .map(([k, label]) => `${label}: ${prev[k]} -> ${cur[k]}`)
}

export type DeadSummary = { candidates: number; byKind: Record<string, number> }

/** Parse vulture's `path:line: unused <kind> 'name' (N% confidence)` lines. */
export function summarizeVulture(output: string): DeadSummary {
  const byKind: Record<string, number> = {}
  let candidates = 0
  for (const line of output.split('\n')) {
    const m = /: unused ([a-z]+) /.exec(line)
    if (!m) continue
    candidates++
    byKind[m[1]] = (byKind[m[1]] ?? 0) + 1
  }
  return { candidates, byKind }
}
