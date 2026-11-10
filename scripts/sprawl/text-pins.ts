/**
 * Source-text pins: the static detector behind the text-pin census in
 * scripts/sprawl/analyze.ts. Pure: text in, a count out.
 *
 * A source-text pin is a change-detector test (Google Testing Blog,
 * "Change-Detector Tests Considered Harmful", 2015): it reads a repo source or
 * doc file as TEXT and asserts on its wording (toContain, toMatch, a regex,
 * Python `in`) instead of running the code it covers. It fails when a sentence
 * is reworded and stays green when the behaviour breaks. PR #2773 removed
 * fourteen such suites; tests/text-pin-ratchet.test.ts keeps them from growing back.
 *
 * Counted per assertion site, by taint:
 *
 * - ANCHORED names hold a repo path: their value mentions import.meta.dirname,
 *   __dirname, __file__, process.cwd(), a 'src/...'-style literal, or another
 *   anchored name. A fixture, golden, snapshot or temp path never anchors.
 * - SOURCE-TEXT names are bound to a read (readFileSync, readFile, read_text,
 *   read_bytes, open) of an anchored path, or derived from a source-text name.
 *   A value that passes through a spawn, a parser (JSON, YAML, TOML, AST) or an
 *   eval stops being source text: that test runs code or checks structure.
 * - VERDICT names are source text that already passed a wording check
 *   (`missing = [p for p in PHRASES if p not in TEXT]`); any assertion on one counts.
 * - A PIN is an expect(...) whose subject uses source text and whose matcher is
 *   a wording matcher (toContain, toMatch, a snapshot) or whose subject applies a
 *   wording method (.includes, .test, .indexOf ...); or a Python assert (or
 *   self.assertIn/Regex) that uses source text with `in`, re.*, .count, .find ...
 *
 * Scope is by name across the file, not by block: a name rebound to a fixture
 * elsewhere in the same file is still counted. tests/sprawl-analyze.test.ts holds
 * the known answers in both directions.
 */

export type PinLang = 'ts' | 'py'

/** The language a test file's pins are read in, or null when it is not a test file. */
export function pinLangOf(path: string): PinLang | null {
  if (/(^|\/)(fixtures?|__fixtures__|__snapshots__)\//.test(path)) return null
  if (/\.(test|spec)\.(ts|tsx|mts|js|mjs)$/.test(path)) return 'ts'
  if (/(^|\/)(test_[^/]*|[^/]*_test)\.py$/.test(path)) return 'py'
  return null
}

// ---------------------------------------------------------------- masking

/** Blank `[a, b)` in `out`, keeping newlines so offsets and line numbers hold. */
function blank(out: string[], a: number, b: number): void {
  for (let k = a; k < b; k++) if (out[k] !== '\n') out[k] = ' '
}

function skipTsString(src: string, i: number, q: string): number {
  let j = i + 1
  while (j < src.length) {
    const c = src[j]
    if (c === '\\') j += 2
    else if (c === q) return j + 1
    else if (q !== '`' && c === '\n') return j
    else if (q === '`' && c === '$' && src[j + 1] === '{') j = skipTemplateExpr(src, j + 2)
    else j++
  }
  return j
}

function skipTemplateExpr(src: string, i: number): number {
  let depth = 1
  let j = i
  while (j < src.length && depth > 0) {
    const c = src[j]
    if (c === "'" || c === '"' || c === '`') {
      j = skipTsString(src, j, c)
      continue
    }
    if (c === '{') depth++
    else if (c === '}') depth--
    j++
  }
  return j
}

/** End of a regex literal starting at `i`, or -1 when the slash is a division. */
function skipRegex(src: string, i: number): number {
  let inClass = false
  for (let j = i + 1; j < src.length; j++) {
    const c = src[j]
    if (c === '\\') j++
    else if (c === '\n') return -1
    else if (c === '[') inClass = true
    else if (c === ']') inClass = false
    else if (c === '/' && !inClass) {
      let k = j + 1
      while (/[a-z]/.test(src[k] ?? '')) k++
      return k
    }
  }
  return -1
}

const REGEX_MAY_FOLLOW = /^$|[(,=:[!&|?{};+\-*%<>~^]/

function commentEnd(src: string, i: number): number {
  if (src[i] !== '/') return -1
  if (src[i + 1] === '/') {
    const e = src.indexOf('\n', i)
    return e < 0 ? src.length : e
  }
  if (src[i + 1] === '*') {
    const e = src.indexOf('*/', i + 2)
    return e < 0 ? src.length : e + 2
  }
  return -1
}

function literalEnd(src: string, i: number, prev: string): number {
  const c = src[i]
  if (c === "'" || c === '"' || c === '`') return skipTsString(src, i, c)
  if (c === '/' && REGEX_MAY_FOLLOW.test(prev)) return skipRegex(src, i)
  return -1
}

/** A template literal's text is blanked; its `${...}` expressions stay, masked in turn. */
function maskTemplate(src: string, out: string[], i: number, end: number): void {
  let j = i + 1
  while (j < end - 1) {
    if (src[j] === '\\') {
      blank(out, j, j + 2)
      j += 2
    } else if (src[j] === '$' && src[j + 1] === '{') {
      const close = skipTemplateExpr(src, j + 2)
      const inner = maskTs(src.slice(j + 2, close - 1))
      for (let k = 0; k < inner.length; k++) out[j + 2 + k] = inner[k]
      j = close
    } else {
      blank(out, j, j + 1)
      j++
    }
  }
}

function maskTs(src: string): string {
  const out = src.split('')
  let prev = ''
  let i = 0
  while (i < src.length) {
    const ce = commentEnd(src, i)
    if (ce >= 0) {
      blank(out, i, ce)
      i = ce
      continue
    }
    const le = literalEnd(src, i, prev)
    if (le > i) {
      if (src[i] === '`') maskTemplate(src, out, i, le)
      else blank(out, i + 1, le - 1)
      prev = src[i]
      i = le
      continue
    }
    if (!/\s/.test(src[i])) prev = src[i]
    i++
  }
  return out.join('')
}

function maskPy(src: string): string {
  const out = src.split('')
  let i = 0
  while (i < src.length) {
    const c = src[i]
    if (c === '#') {
      const e = src.indexOf('\n', i)
      const end = e < 0 ? src.length : e
      blank(out, i, end)
      i = end
    } else if (c === "'" || c === '"') {
      const q = src.startsWith(c.repeat(3), i) ? c.repeat(3) : c
      let j = i + q.length
      while (j < src.length && !src.startsWith(q, j) && !(q.length === 1 && src[j] === '\n'))
        j += src[j] === '\\' ? 2 : 1
      blank(out, i + q.length, j)
      i = Math.min(src.length, j + q.length)
    } else i++
  }
  return out.join('')
}

/** Comments, string and regex interiors blanked (newlines kept), so structure can be scanned. */
export function maskCode(src: string, lang: PinLang): string {
  return lang === 'ts' ? maskTs(src) : maskPy(src)
}

// ---------------------------------------------------------------- statements

/**
 * A binding (names = the value at [start, end)) or an assertion, as offsets
 * into the file. `at` is where the names are declared, which fixes their scope.
 */
type Binding = { names: string[]; at: number; start: number; end: number }
type Assertion = { start: number; subjectEnd: number; matcher: string }
type Block = { start: number; end: number }

const OPEN = '([{'
const CLOSE = ')]}'

/** Offset just past the bracket that closes the one at `i`. */
function closeOf(m: string, i: number): number {
  let depth = 0
  for (let j = i; j < m.length; j++) {
    if (OPEN.includes(m[j])) depth++
    else if (CLOSE.includes(m[j]) && --depth === 0) return j + 1
  }
  return m.length
}

const CONTINUES_NEXT = /^\s*(\.|\?|:|\+|-|\*|\/|&&|\|\||\?\?|=>)/
const CONTINUES_PREV = /(=|\(|,|\+|-|\*|&&|\|\||\?|:|=>|\[|\{)\s*$/

/** End of a TypeScript expression starting at `i`: the newline or `;` at depth 0 that ends it. */
function tsExprEnd(m: string, i: number): number {
  let depth = 0
  for (let j = i; j < m.length; j++) {
    const c = m[j]
    if (OPEN.includes(c)) depth++
    else if (CLOSE.includes(c)) {
      if (--depth < 0) return j
    } else if (depth === 0 && c === ';') return j
    else if (depth === 0 && c === '\n') {
      const more =
        CONTINUES_PREV.test(m.slice(i, j)) || CONTINUES_NEXT.test(m.slice(j + 1, j + 200))
      if (!more) return j
    }
  }
  return m.length
}

const IDENT = /[A-Za-z_$][\w$]*/g

function namesOf(lhs: string): string[] {
  const plain = /^\s*([A-Za-z_$][\w$]*)\s*(?::.*)?$/s.exec(lhs)
  if (plain) return [plain[1]]
  return [...lhs.replace(/:\s*[A-Za-z_$][\w$]*(?=\s*[,}\]])/g, '').matchAll(IDENT)].map((x) => x[0])
}

function tsBindings(m: string): Binding[] {
  const out: Binding[] = []
  for (const x of m.matchAll(/\b(?:const|let|var)\s+([^=;\n]+?)\s*=(?![=>])/g)) {
    const start = x.index + x[0].length
    out.push({ names: namesOf(x[1]), at: x.index, start, end: tsExprEnd(m, start) })
  }
  for (const x of m.matchAll(/\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(/g)) {
    const params = closeOf(m, x.index + x[0].length - 1)
    const brace = m.indexOf('{', params)
    if (brace >= 0) out.push({ names: [x[1]], at: x.index, start: params, end: closeOf(m, brace) })
  }
  for (const x of m.matchAll(
    /\bfor\s*(?:await\s*)?\(\s*(?:const|let|var)\s+(.+?)\s+(?:of|in)\s/g
  )) {
    const end = closeOf(m, m.indexOf('(', x.index))
    out.push({ names: namesOf(x[1]), at: x.index, start: x.index + x[0].length, end })
  }
  // A callback parameter takes its value from the receiver: files.map((f) => ...), list.forEach(text => ...).
  const CALLBACK =
    /([\w$.]+)\.(?:map|forEach|filter|some|every|flatMap|find)\(\s*\(?\s*([A-Za-z_$][\w$]*)/g
  for (const x of m.matchAll(CALLBACK))
    out.push({ names: [x[2]], at: x.index, start: x.index, end: x.index + x[1].length })
  return out
}

function tsBlocks(m: string): Block[] {
  const out: Block[] = []
  for (let i = 0; i < m.length; i++) if (m[i] === '{') out.push({ start: i, end: closeOf(m, i) })
  return out
}

const TS_MATCHER = /^((?:\s*\.\s*(?:not|resolves|rejects))*)\s*\.\s*([A-Za-z]+)/

function tsAssertions(m: string): Assertion[] {
  const out: Assertion[] = []
  for (const x of m.matchAll(/\bexpect(?:\.soft)?\s*\(/g)) {
    const open = x.index + x[0].length - 1
    const close = closeOf(m, open)
    const tail = TS_MATCHER.exec(m.slice(close, close + 200))
    out.push({ start: open + 1, subjectEnd: close - 1, matcher: tail ? tail[2] : '' })
  }
  return out
}

type PyLine = { start: number; end: number; indent: number; text: string }

/** Python logical lines: physical lines joined while a bracket is open or a line ends in `\`. */
function pyLines(m: string): PyLine[] {
  const out: PyLine[] = []
  let start = 0
  let depth = 0
  for (let j = 0; j <= m.length; j++) {
    const c = m[j] ?? '\n'
    if (OPEN.includes(c)) depth++
    else if (CLOSE.includes(c)) depth = Math.max(0, depth - 1)
    else if (c === '\n') {
      if (j < m.length && (depth > 0 || /\\\s*$/.test(m.slice(start, j)))) continue
      const text = m.slice(start, j)
      if (text.trim())
        out.push({ start, end: j, indent: text.length - text.trimStart().length, text })
      start = j + 1
    }
  }
  return out
}

/** Assignment targets: `a, b` gives a and b; `self.text` gives text; `d[k]` gives d. */
function pyTargets(lhs: string): string[] {
  return lhs
    .split(',')
    .map(
      (part) =>
        part
          .replace(/\[.*$/s, '')
          .replace(/^\s*\*/, '')
          .trim()
          .split('.')
          .pop() ?? ''
    )
    .filter((n) => /^[A-Za-z_]\w*$/.test(n))
}

/** The def bodies, each a scope from the end of its `def` line to its last indented line. */
function pyBlocks(lines: PyLine[]): Block[] {
  const out: Block[] = []
  lines.forEach((l, k) => {
    if (!/^\s*(?:async\s+)?def\s/.test(l.text)) return
    let e = k + 1
    while (e < lines.length && lines[e].indent > l.indent) e++
    out.push({ start: l.end, end: lines[e - 1].end })
  })
  return out
}

const PY_ASSIGN = /^([\w\s,.[\]*]+?)\s*(?::[^=]+)?(?<![=!<>+\-*/%|&^])=(?!=)/
const PY_FOR = /^(?:async\s+)?for\s+(.+?)\s+in\s/

function pyBindings(lines: PyLine[], blocks: Block[]): Binding[] {
  const out: Binding[] = []
  for (const l of lines) {
    const t = l.text.trimStart()
    const lead = l.start + l.text.length - t.length
    const def = /^(?:async\s+)?def\s+(\w+)/.exec(t)
    if (def) {
      const body = blocks.find((b) => b.start === l.end)
      out.push({ names: [def[1]], at: l.start, start: l.start, end: body ? body.end : l.end })
      continue
    }
    const hit = PY_ASSIGN.exec(t) ?? PY_FOR.exec(t)
    if (hit)
      out.push({ names: pyTargets(hit[1]), at: l.start, start: lead + hit[0].length, end: l.end })
    for (const w of t.matchAll(/\bopen\(.*?\bas\s+(\w+)/g))
      out.push({ names: [w[1]], at: l.start, start: l.start, end: l.end })
  }
  return out
}

function pyAssertions(lines: PyLine[]): Assertion[] {
  return lines
    .filter((l) => /^\s*assert\b|\bself\.assert\w+\(/.test(l.text))
    .map((l) => {
      const named = /self\.(assert\w+)\(/.exec(l.text)
      return { start: l.start, subjectEnd: l.end, matcher: named ? named[1] : 'assert' }
    })
}

// ---------------------------------------------------------------- scope

/**
 * Lexical scope, approximated: TypeScript braces and Python def bodies. A name
 * resolves to the innermost enclosing scope that binds it, else the module, so
 * a local `out` in one test is not the `out` another test read from a file.
 */
class Scopes {
  private readonly at: Int32Array
  private readonly parent: number[]
  private readonly declared = new Map<string, Set<number>>()

  constructor(length: number, blocks: Block[], bindings: Binding[]) {
    const sorted = [...blocks].sort((a, b) => a.start - b.start || b.end - a.end)
    this.at = new Int32Array(length + 1).fill(-1)
    // Outer blocks start first, so each block's parent is whatever covers its start when it is reached.
    this.parent = sorted.map((b, k) => {
      const outer = this.at[b.start]
      this.at.fill(k, b.start, Math.max(b.start, b.end))
      return outer
    })
    for (const b of bindings) {
      const s = this.at[b.at]
      for (const n of b.names) this.declared.set(n, (this.declared.get(n) ?? new Set()).add(s))
    }
  }

  /** The key a declaration of `name` at offset `pos` lives under. */
  keyAt(name: string, pos: number): string {
    return `${name}@${this.at[pos]}`
  }

  /** The key the use of `name` at offset `pos` resolves to. */
  resolve(name: string, pos: number): string {
    const own = this.declared.get(name)
    let k = this.at[pos]
    while (k !== -1 && !own?.has(k)) k = this.parent[k]
    return `${name}@${k}`
  }
}

// ---------------------------------------------------------------- taint

/**
 * Shared helpers in tests/_support that read a repo file and hand back its text
 * (readSeatScript: a seat script with the layout expanded; provisionScriptSource
 * and scriptDerivation: operator/bin/provision-customer.sh). A call to one is a
 * read of an anchored path, though the readFileSync lives in another file.
 */
const SUPPORT_READERS = String.raw`\b(?:readSeatScript|provisionScriptSource|scriptDerivation)\s*\(`
const ANCHOR_TS = new RegExp(
  SUPPORT_READERS +
    String.raw`|import\.meta\.(?:dirname|url|filename)|__dirname|process\.cwd\(\)|` +
    /['"`](?:\.\.?\/)*(?:src|docs|operator|scripts|workers|migrations|\.github|\.claude|tests|public|e2e|bin)\/|['"`](?:\.\.?\/)*(?:CLAUDE\.md|AGENTS\.md|README\.md|package\.json|wrangler\.toml|tsconfig\.json|astro\.config\.\w+|vitest\.config\.\w+|knip\.\w+|eslint\.config\.\w+)['"`]/
      .source
)
const ANCHOR_PY =
  /__file__|\brootpath\b|\brootdir\b|show-toplevel|['"](?:\.\.?\/)*(?:src|docs|operator|scripts|workers|migrations|\.github|\.claude|tests)\/|['"](?:CLAUDE\.md|AGENTS\.md|package\.json|wrangler\.toml)['"]/
/** Test data, never source: a fixture, golden or snapshot file, or a temp directory. */
const NOT_SOURCE =
  /fixture|golden|snapshot|testdata|mkdtemp|tmpdir|tmp_path|TemporaryDirectory|\btmp\b|gettempdir/i
const READ_TS = new RegExp(String.raw`\b(?:readFileSync|readFile)\s*\(|` + SUPPORT_READERS)
const READ_PY = /\.read_text\(|\.read_bytes\(|\bopen\(|\.read\(\)|\.readlines\(\)/
/** A value through one of these is run or parsed, not read as wording. */
const BREAK_TS =
  /\b(?:execFileSync|execSync|spawnSync|spawn|execFile|exec|fork)\s*\(|\b(?:JSON|YAML|yaml|TOML|toml)\.\w+\(|\bparse(?:Yaml|YAML|Toml|TOML|Json|Document)?\s*\(|\bnew Function\b|\bvm\.\w+\(|\bimport\s*\(/
const BREAK_PY =
  /\bsubprocess\.\w+\(|\bPopen\(|\bcheck_output\(|\b(?:yaml|json|toml|tomllib|tomli|ast)\.\w+\(|\bsafe_load\(|\bexec\(|\bcompile\(|\bimportlib\.|\brunpy\.|\.execute(?:script|many)?\(|\bsqlite3\.connect\(/
const VERDICT_TS =
  /\.(?:includes|test|match|matchAll|indexOf|lastIndexOf|search|startsWith|endsWith|split)\s*\(/
const VERDICT_PY =
  /\bin\b|\bre\.\w+\(|\.(?:count|startswith|endswith|find|rfind|index|splitlines|split)\(/
const WORDING_MATCHER =
  /^(?:toContain|toMatch|toMatchInlineSnapshot|toMatchSnapshot|toMatchFileSnapshot)$/
const WORDING_ASSERT_PY = /^assert(?:In|NotIn|Regex|NotRegex)$/

type Rules = { anchor: RegExp; read: RegExp; brk: RegExp; verdict: RegExp }

const RULES: Record<PinLang, Rules> = {
  ts: { anchor: ANCHOR_TS, read: READ_TS, brk: BREAK_TS, verdict: VERDICT_TS },
  py: { anchor: ANCHOR_PY, read: READ_PY, brk: BREAK_PY, verdict: VERDICT_PY },
}

type Taint = {
  anchored: Set<string>
  readers: Set<string>
  text: Set<string>
  verdict: Set<string>
  breakers: Set<string>
}

/** One file under analysis: its text, masked text, rules, scopes and taint. */
type Ctx = { src: string; m: string; r: Rules; scopes: Scopes; t: Taint; lang: PinLang }

/** Variable uses in m[start, end): identifiers not reached through `.` (Python `self.` excepted). */
function* uses(c: Ctx, start: number, end: number): Generator<[string, number]> {
  const re = /[A-Za-z_$][\w$]*/g
  re.lastIndex = start
  for (let x = re.exec(c.m); x && x.index < end; x = re.exec(c.m)) {
    const before = c.m.slice(Math.max(0, x.index - 6), x.index)
    if (/\.\s*$/.test(before) && !(c.lang === 'py' && /self\.$/.test(before))) continue
    yield [x[0], x.index]
  }
}

function mentions(c: Ctx, start: number, end: number, set: Set<string>): boolean {
  for (const [name, pos] of uses(c, start, end))
    if (set.has(c.scopes.resolve(name, pos))) return true
  return false
}

function callsAny(c: Ctx, start: number, end: number, set: Set<string>): boolean {
  for (const [name, pos] of uses(c, start, end))
    if (/^\s*\(/.test(c.m.slice(pos + name.length, pos + name.length + 4)))
      if (set.has(c.scopes.resolve(name, pos))) return true
  return false
}

/** The taint sets one binding's names belong to, given what is known so far. */
function classify(c: Ctx, b: Binding): Array<keyof Taint> {
  const raw = c.src.slice(b.start, b.end)
  const code = c.m.slice(b.start, b.end)
  // A runner first: a helper that makes a temp dir and spawns a script is still a runner.
  if (c.r.brk.test(code) || callsAny(c, b.start, b.end, c.t.breakers)) return ['breakers']
  if (NOT_SOURCE.test(raw)) return []
  const out: Array<keyof Taint> = []
  const anchored = c.r.anchor.test(raw) || mentions(c, b.start, b.end, c.t.anchored)
  if (anchored) out.push('anchored')
  const reads = c.r.read.test(code) || callsAny(c, b.start, b.end, c.t.readers)
  // A helper that reads whatever path it is handed: `const read = (p) => readFileSync(p, 'utf8')`.
  if (reads && !anchored) out.push('readers')
  const fromText = mentions(c, b.start, b.end, c.t.text)
  if ((reads && anchored) || fromText) out.push('text')
  if (fromText && (c.r.verdict.test(code) || mentions(c, b.start, b.end, c.t.verdict)))
    out.push('verdict')
  return out
}

/** One pass over the bindings; returns true when any set grew. */
function taintPass(c: Ctx, bs: Binding[]): boolean {
  let grew = false
  for (const b of bs) {
    for (const set of classify(c, b)) {
      for (const n of b.names) {
        const key = c.scopes.keyAt(n, b.at)
        if (c.t[set].has(key)) continue
        c.t[set].add(key)
        grew = true
      }
    }
  }
  return grew
}

function isPin(c: Ctx, a: Assertion): boolean {
  const subject = c.m.slice(a.start, a.subjectEnd)
  const raw = c.src.slice(a.start, a.subjectEnd)
  const reads = c.r.read.test(subject) || callsAny(c, a.start, a.subjectEnd, c.t.readers)
  const anchored = c.r.anchor.test(raw) || mentions(c, a.start, a.subjectEnd, c.t.anchored)
  const direct = reads && anchored && !NOT_SOURCE.test(raw)
  if (!direct && !mentions(c, a.start, a.subjectEnd, c.t.text)) return false
  if (c.r.brk.test(subject)) return false
  if (mentions(c, a.start, a.subjectEnd, c.t.verdict)) return true
  if (c.lang === 'ts') return WORDING_MATCHER.test(a.matcher) || c.r.verdict.test(subject)
  return WORDING_ASSERT_PY.test(a.matcher) || c.r.verdict.test(subject.replace(/^\s*assert\b/, ''))
}

/** The 1-based line of each source-text pin assertion in one test file (none for a non-test path). */
export function sourceTextPinLines(path: string, src: string): number[] {
  const lang = pinLangOf(path)
  if (!lang) return []
  const m = maskCode(src, lang)
  const lines = lang === 'py' ? pyLines(m) : []
  const blocks = lang === 'ts' ? tsBlocks(m) : pyBlocks(lines)
  const bindings = lang === 'ts' ? tsBindings(m) : pyBindings(lines, blocks)
  const asserts = lang === 'ts' ? tsAssertions(m) : pyAssertions(lines)
  const t: Taint = {
    anchored: new Set(),
    readers: new Set(),
    text: new Set(),
    verdict: new Set(),
    breakers: new Set(),
  }
  const c: Ctx = { src, m, r: RULES[lang], scopes: new Scopes(m.length, blocks, bindings), t, lang }
  for (let k = 0; k < 20 && taintPass(c, bindings); k++);
  return asserts.filter((a) => isPin(c, a)).map((a) => src.slice(0, a.start).split('\n').length)
}

/** The number of source-text pin assertions in one test file (0 for a non-test path). */
export function sourceTextPins(path: string, src: string): number {
  return sourceTextPinLines(path, src).length
}
