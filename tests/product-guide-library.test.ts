/**
 * The product guide's library is representative-safe, and its answer key cites
 * only what the library holds (ADR 0105).
 *
 * The `guide` role answers questions about the Operator from the authored
 * library at operator/skills/product-guide/references/, citing the document and
 * the section. The asker is an outside sales representative, so the library is
 * held to rules a reviewer would otherwise have to remember:
 *
 *   1. No dollar amount, in any form.
 *   2. No em dash.
 *   3. No named client or person. The names are DERIVED from every authored
 *      seat (customer_name, users[].full_name, people[] names, client slugs),
 *      so a new client seat is covered the day it is authored, plus a short
 *      fixed list of internal names. "SMD Services" is the one name allowed.
 *   4. No internal runtime vocabulary: vendor and tool names of the runtime,
 *      decision-record numbers, file names or code paths.
 *   5. Every relative link resolves to a file in the library.
 *   6. Every file starts with a `# ` title and has stable `## ` sections, and
 *      INDEX.md lists every file (answers cite "document, section").
 *
 * The answer key (operator/skills/product-guide/rehearsal/questions.yaml) is the
 * $0 rehearsal asset: every expected citation must name a document and a `## `
 * section that exist. The paid answer-quality check runs the questions against
 * a seat; nothing here calls a model.
 *
 * Invite codes (ADR 0106). Nothing the guide reads or is graded against may
 * carry a string shaped like an invite code: the library, the skill and the
 * answer key are matched against the code pattern the seat's own router uses
 * (read from the overlay, so the two cannot drift).
 *
 * Each rule is shown able to fail on a planted string (the last describe).
 */

import { describe, it, expect } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'

const SKILL_DIR = resolve('operator/skills/product-guide')
const LIBRARY = join(SKILL_DIR, 'references')
const KEY = join(SKILL_DIR, 'rehearsal', 'questions.yaml')
const CUSTOMERS = resolve('operator/customers')
const ROLE_DIR = resolve('operator/roles/guide')
const PRACTICE_DIR = resolve('operator/skills/pitch-practice')
const SCENARIOS = join(PRACTICE_DIR, 'references', 'scenarios.yaml')
const RUBRIC = join(PRACTICE_DIR, 'references', 'rubric.md')
/** The seat's own send gate's banned markers (fabrication gate, every email reply). */
const MARKERS_JSON = resolve('operator/hermes-smd-overlay/shared/fabrication_markers.json')
/** The router's invite-code detector (ADR 0106); its CODE_PATTERN literal is read, not copied. */
const ENROLLMENT_PY = resolve('operator/hermes-smd-overlay/shared/sender_enrollment.py')

/** The invite-code pattern exactly as the seat's router compiles it (re.IGNORECASE). */
function codePattern(): RegExp {
  const m = readFileSync(ENROLLMENT_PY, 'utf8').match(/^CODE_PATTERN = r"([^"]+)"$/m)
  if (!m) throw new Error('CODE_PATTERN not found in the overlay')
  // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp - the pattern is the overlay's own checked-in literal, never user input
  return new RegExp(m[1], 'gi')
}
const CODE_RE = codePattern()

/** Every code-shaped token in a text. */
function codeShaped(text: string): string[] {
  return [...text.matchAll(CODE_RE)].map((m) => m[0])
}

/**
 * The reviewed explainer page. Its address lives in ONE place, INDEX.md, on the
 * line "Explainer page: <url>", so it can change without touching skill text.
 */
const EXPLAINER_LINE = /^Explainer page: (https:\/\/claude\.ai\/artifact\/[A-Za-z0-9]+)$/m
/** The page's anchors, one per view; the only anchors the library may name. */
const EXPLAINER_ANCHORS = [
  'idea',
  'context',
  'lifecycle',
  'limits',
  'jobs',
  'isolation',
  'trust',
  'service',
  'edges',
] as const
/** The page's one industry example; a tenth anchor the library may name, not required. */
const EXAMPLE_ANCHOR = 'example'

/** Product and competitor names from the sales source material; never in practice. */
const COMPETITOR_NAMES = [
  'EvenUp',
  'LegalClerk',
  'Smith.ai',
  'Copilot',
  'ChatGPT',
  'Clio',
  'Smokeball',
  'Caseflood',
  'CaseGen',
  'LawFirmIgnite',
  'Blueshoe',
  'Legal Soft',
  'Vendasta',
]

type MarkerEntry = { id: string; kind?: string; value?: string; pattern?: string }

/**
 * The fabrication gate's own registry, matched the way the overlay compiles it
 * (shared/fabrication_markers.py): every email reply the seat sends is scanned
 * with it, so a library sentence quoted into an answer must never carry one.
 */
function loadMarkers(): { id: string; test: (t: string) => boolean }[] {
  const doc = JSON.parse(readFileSync(MARKERS_JSON, 'utf8')) as { markers: MarkerEntry[] }
  return doc.markers.map((m) => {
    const text = m.value ?? m.pattern ?? ''
    const kind = (m.kind ?? 'literal_ci').toLowerCase()
    if (kind === 'regex') {
      // nosemgrep: javascript.lang.security.audit.detect-non-literal-regexp.detect-non-literal-regexp - the pattern is the overlay's own checked-in marker registry, never user input
      const re = new RegExp(text, 'i')
      return { id: m.id, test: (t: string) => re.test(t) }
    }
    if (kind === 'literal') return { id: m.id, test: (t: string) => t.includes(text) }
    const lower = text.toLowerCase()
    return { id: m.id, test: (t: string) => t.toLowerCase().includes(lower) }
  })
}
const MARKERS = loadMarkers()

function markerHits(text: string): string[] {
  return MARKERS.filter((m) => m.test(text)).map((m) => m.id)
}

/** Internal names that no seat file lists but must never reach a representative. */
const FIXED_NAMES = ['Captain', 'Crane', 'Venture Crane', 'SMDurgan']

/** Tokens of an authored name that are ordinary words, not identifying. */
const GENERIC_NAME_TOKENS = new Set(['The', 'LLP', 'Agent', 'Operator', 'Marketing'])

/** Seats that are SMD's own rigs, not clients or people (their names are labels). */
const RIG_SEAT = /^(smd-|pilot-|_)/

/**
 * Runtime vocabulary: vendor and tool names of the runtime, decision-record
 * references, code and file paths. Case-insensitive unless noted.
 */
const RUNTIME_TERMS: { label: string; re: RegExp }[] = [
  {
    label: 'runtime vendor or tool name',
    re: /\b(hermes|agentmail|cloudflare|infisical|sentry|svix|groq|anthropic|claude|sonnet|opus|fly\.io|tailscale|wrangler|vectorize|sqlite|clerk|signwell|stripe|brave)\b/i,
  },
  { label: 'runtime storage or host name', re: /\b(R2|D1|Fly)\b/ },
  { label: 'decision-record reference', re: /\bADR[- ]?\d*/ },
  {
    label: 'code or config file name',
    re: /\b[\w.-]+\.(py|ts|tsx|mjs|js|sh|ya?ml|json|toml|sql)\b/i,
  },
  {
    label: 'repository path',
    re: /(^|[\s(`'"])(operator|src|docs|scripts|tests|workers)\/[\w-]/m,
  },
  { label: 'source line reference', re: /:\d+(-\d+)?\b(?![:.]\d)/ },
]

const DOLLAR = [
  { label: 'dollar sign', re: /\$/ },
  { label: 'currency word', re: /\b\d[\d,.]*\s*(dollars?|usd|cents?)\b/i },
  { label: 'currency word', re: /\b(usd|dollars?)\b/i },
]
const EM_DASH = /—/

function walk(dir: string): string[] {
  if (!existsSync(dir)) return []
  const out: string[] = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else out.push(p)
  }
  return out.sort()
}

function nameStrings(value: unknown): string[] {
  return typeof value === 'string' && value.trim() !== '' && !value.includes('[') ? [value] : []
}

/** Every client or person name an authored seat carries, plus the fixed list. */
export function authoredNames(): string[] {
  const names = new Set<string>(FIXED_NAMES)
  for (const e of readdirSync(CUSTOMERS, { withFileTypes: true })) {
    if (!e.isDirectory() || RIG_SEAT.test(e.name)) continue
    const path = join(CUSTOMERS, e.name, 'customer.yaml')
    if (!existsSync(path)) continue
    const doc = (parseYaml(readFileSync(path, 'utf8')) ?? {}) as Record<string, unknown>
    if (e.name.includes('-')) names.add(e.name)
    const raw: string[] = [...nameStrings(doc.customer_name)]
    for (const u of (doc.users as Record<string, unknown>[] | undefined) ?? []) {
      raw.push(...nameStrings(u?.full_name))
    }
    for (const p of (doc.people as Record<string, unknown>[] | undefined) ?? []) {
      raw.push(...nameStrings(p?.name), ...nameStrings(p?.full_name))
    }
    for (const full of raw) {
      const clean = full.replace(/\([^)]*\)/g, '').trim()
      names.add(clean)
      for (const token of clean.split(/[\s&,]+/)) {
        if (token.length >= 4 && /^[A-Z]/.test(token) && !GENERIC_NAME_TOKENS.has(token)) {
          names.add(token)
        }
      }
    }
  }
  names.delete('SMD Services')
  return [...names].sort()
}

const WORD_CHAR = /[A-Za-z0-9_]/

/** True when `name` occurs in `text` as a whole word (case-insensitive for a slug). */
function containsWord(text: string, name: string): boolean {
  const fold = name.includes('-')
  const hay = fold ? text.toLowerCase() : text
  const needle = fold ? name.toLowerCase() : name
  for (let i = hay.indexOf(needle); i !== -1; i = hay.indexOf(needle, i + 1)) {
    const before = i === 0 ? '' : hay[i - 1]
    const after = hay[i + needle.length] ?? ''
    if (!WORD_CHAR.test(before) && !WORD_CHAR.test(after)) return true
  }
  return false
}

/** Every rule violation in one text, as readable strings. */
export function hygieneErrors(text: string, names: string[], opts: { runtime: boolean }): string[] {
  const errors: string[] = []
  for (const d of DOLLAR) if (d.re.test(text)) errors.push(`${d.label}: ${text.match(d.re)?.[0]}`)
  if (EM_DASH.test(text)) errors.push('em dash')
  for (const name of names) {
    if (containsWord(text, name)) errors.push(`names "${name}"`)
  }
  if (opts.runtime) {
    // Fenced code blocks are illustrations of authored settings; checked too.
    for (const t of RUNTIME_TERMS) {
      const m = text.match(t.re)
      if (m) errors.push(`${t.label}: "${m[0].trim()}"`)
    }
  }
  return errors
}

/** Relative markdown links in one file that do not resolve to a library file. */
function brokenLinks(file: string, text: string): string[] {
  const broken: string[] = []
  for (const m of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    const target = m[1]
    if (/^(https?:|mailto:|#)/.test(target)) continue
    const path = resolve(dirname(file), target.split('#')[0])
    if (!path.startsWith(LIBRARY) || !existsSync(path)) broken.push(target)
  }
  return broken
}

/** The `## ` section headings of a library document, in order. */
export function sections(text: string): string[] {
  return [...text.matchAll(/^## (.+?)\s*$/gm)].map((m) => m[1])
}

const libraryFiles = walk(LIBRARY).filter((f) => f.endsWith('.md'))
const NAMES = authoredNames()

describe('product guide library (ADR 0105)', () => {
  it('exists, holds only markdown, and has an index', () => {
    expect(libraryFiles.length).toBeGreaterThan(5)
    expect(walk(LIBRARY).filter((f) => !f.endsWith('.md'))).toEqual([])
    expect(existsSync(join(LIBRARY, 'INDEX.md'))).toBe(true)
  })

  it('derives client and person names from the authored seats', () => {
    // A guard whose list came back empty would pass anything.
    expect(NAMES.length).toBeGreaterThan(FIXED_NAMES.length)
    expect(NAMES).not.toContain('SMD Services')
  })

  for (const file of libraryFiles) {
    const rel = file.slice(LIBRARY.length + 1)
    describe(rel, () => {
      const text = readFileSync(file, 'utf8')
      it('starts with a title and has stable sections', () => {
        expect(text.split('\n')[0]).toMatch(/^# \S/)
        expect(sections(text).length).toBeGreaterThan(0)
        const dupes = sections(text).filter((s, i, all) => all.indexOf(s) !== i)
        expect(dupes).toEqual([])
      })
      it('carries no dollar amount, em dash, client or person name, or runtime vocabulary', () => {
        // The explainer page's address is the one sanctioned host name (INDEX.md only).
        const scanned = rel === 'INDEX.md' ? text.replace(EXPLAINER_LINE, 'Explainer page:') : text
        expect(hygieneErrors(scanned, NAMES, { runtime: true })).toEqual([])
      })
      it("carries none of the send gate's banned fabrication markers", () => {
        expect(markerHits(text)).toEqual([])
      })
      it('has no relative link that fails to resolve', () => {
        expect(brokenLinks(file, text)).toEqual([])
      })
    })
  }

  it('INDEX.md lists every library file', () => {
    const index = readFileSync(join(LIBRARY, 'INDEX.md'), 'utf8')
    const missing = libraryFiles
      .map((f) => f.slice(LIBRARY.length + 1))
      .filter((rel) => rel !== 'INDEX.md' && !index.includes(`](${rel})`))
    expect(missing).toEqual([])
  })

  it('the skill and the role persona follow the same content rules', () => {
    for (const file of [
      join(SKILL_DIR, 'SKILL.md'),
      join(ROLE_DIR, 'persona.md'),
      join(ROLE_DIR, 'role.yaml'),
    ]) {
      expect({
        file,
        errors: hygieneErrors(readFileSync(file, 'utf8'), NAMES, { runtime: false }),
      }).toEqual({ file, errors: [] })
    }
  })
})

type KeyEntry = {
  id: string
  category: string
  question: string
  /**
   * `tool` (ADR 0106): the outcome comes from a tool the guide must call, not
   * from the library; graded on `tool` being called and on `must_contain` /
   * `must_not_contain`, with no citation.
   */
  expect: 'answer' | 'refuse' | 'tool'
  refusal?: string
  tool?: string
  /** Who sends the question, where it matters: `administrator` or `listed`. */
  sender?: 'administrator' | 'listed'
  cite?: { document: string; section: string }[]
  must_contain?: string[]
  must_not_contain?: string[]
}

describe('product guide answer key (ADR 0105)', () => {
  // Read lazily so a missing key fails these tests, not the library ones.
  const key = existsSync(KEY)
    ? (parseYaml(readFileSync(KEY, 'utf8')) as { questions: KeyEntry[] })
    : { questions: [] }
  const qs = key.questions ?? []

  // 28 = the 25 of ADR 0105 plus three invite-code cases of ADR 0106: one
  // refusal (a non-administrator asks; kind `invite-code`) and two `tool`
  // cases (an administrator's named request, and a name carrying a link and a
  // line break). Changed deliberately with ADR 0106; a count that moves again
  // needs the same reason written here. 29 (2026-11-10): r07 added, a
  // non-administrator asking WHO issues codes, so the reply that must not
  // reveal the capability is graded on the question path too, not only on
  // the tool path (r06). 31 (2026-11-10, ss-console #3946): q26 and q27 added
  // for the new prospect-fit page (who-smd-sells-to.md), which the training
  // curriculum's fit lesson reads.
  it('has 31 questions with unique ids, 7 of them refusals of the six kinds', () => {
    expect(qs.length).toBe(31)
    expect(new Set(qs.map((q) => q.id)).size).toBe(31)
    expect(qs.every((q) => ['answer', 'refuse', 'tool'].includes(q.expect))).toBe(true)
    const refusals = qs.filter((q) => q.expect === 'refuse')
    expect(refusals.length).toBe(7)
    expect(refusals.map((q) => q.refusal).sort()).toEqual([
      'client-documents',
      'ignore-rules',
      'invite-code',
      'invite-code',
      'named-client',
      'not-covered',
      'pricing',
    ])
  })

  it('the invite-code cases: a non-administrator learns nothing, an administrator reaches the tool', () => {
    const declines = qs.filter((q) => q.refusal === 'invite-code')
    expect(declines.length).toBe(2)
    for (const decline of declines) {
      expect(decline.sender).toBe('listed')
      const mc = (decline.must_contain ?? []).join(' ')
      expect(mc).toContain('access to the guide comes from SMD Services, at team@smd.services')
      const mnc = decline.must_not_contain ?? []
      expect(mnc.some((m) => /invite code/.test(m))).toBe(true)
      expect(mnc.some((m) => /makes or issues codes/.test(m))).toBe(true)
      expect(mnc.some((m) => /role/.test(m))).toBe(true)
    }

    const tools = qs.filter((q) => q.expect === 'tool')
    expect(tools.length).toBe(2)
    for (const q of tools) {
      expect({ id: q.id, tool: q.tool, sender: q.sender, cites: q.cite }).toEqual({
        id: q.id,
        tool: 'invite_code_issue',
        sender: 'administrator',
        cites: undefined,
      })
      expect((q.must_contain ?? []).length).toBeGreaterThanOrEqual(1)
      expect((q.must_not_contain ?? []).some((m) => /invite code/.test(m))).toBe(true)
    }
    // One is sent; one is refused for its name (a link and a line break).
    const badName = tools.filter((q) => /https?:\/\//.test(q.question) && q.question.includes('\n'))
    expect(badName.length).toBe(1)
    expect(badName[0].must_contain?.join(' ')).toMatch(/plain text/)
  })

  it('no code-shaped string appears in the key, the library or the skill', () => {
    // A planted one is caught (built from parts so no code-shaped literal is committed).
    const planted = ['SMD', 'ABCD', 'EFGH', 'JKMN'].join('-')
    expect(codeShaped(`use ${planted} today`)).toEqual([planted])
    const found: string[] = []
    for (const f of [KEY, join(SKILL_DIR, 'SKILL.md'), ...libraryFiles]) {
      for (const c of codeShaped(readFileSync(f, 'utf8'))) found.push(`${f}: ${c}`)
    }
    expect(found).toEqual([])
  })

  it('has the 5 practice-flow cases: start, a mid-scenario turn, stop, a haggle, a named person', () => {
    const practice =
      (existsSync(KEY)
        ? (
            parseYaml(readFileSync(KEY, 'utf8')) as {
              practice?: { step: string; expect: string[] }[]
            }
          ).practice
        : undefined) ?? []
    expect(practice.map((p) => p.step).sort()).toEqual([
      'haggle',
      'mid-scenario',
      'named-person',
      'start',
      'stop',
    ])
    for (const p of practice) expect(p.expect.length).toBeGreaterThanOrEqual(2)
  })

  it('every answer cites at least one section and names 2 to 4 facts', () => {
    for (const q of qs.filter((q) => q.expect === 'answer')) {
      expect({ id: q.id, cites: (q.cite ?? []).length > 0 }).toEqual({ id: q.id, cites: true })
      const n = (q.must_contain ?? []).length
      expect({ id: q.id, facts: n >= 2 && n <= 4 }).toEqual({ id: q.id, facts: true })
    }
  })

  it('every expected citation names a library document and a section it has', () => {
    const missing: string[] = []
    for (const q of qs) {
      for (const c of q.cite ?? []) {
        const path = join(LIBRARY, c.document)
        if (!existsSync(path)) {
          missing.push(`${q.id}: no document ${c.document}`)
          continue
        }
        if (!sections(readFileSync(path, 'utf8')).includes(c.section)) {
          missing.push(`${q.id}: ${c.document} has no section "${c.section}"`)
        }
      }
    }
    expect(missing).toEqual([])
  })

  it('the key itself carries no dollar amount, em dash or client name', () => {
    // A pricing question may say "price"; it may not carry an amount.
    expect(existsSync(KEY)).toBe(true)
    expect(hygieneErrors(readFileSync(KEY, 'utf8'), NAMES, { runtime: false })).toEqual([])
  })
})

describe('explainer page links (ADR 0105)', () => {
  const index = readFileSync(join(LIBRARY, 'INDEX.md'), 'utf8')

  it('the page address appears once, in INDEX.md, and nowhere else in the library or skills', () => {
    expect(index.split('\n').filter((l) => EXPLAINER_LINE.test(l)).length).toBe(1)
    const elsewhere = [
      ...libraryFiles.filter((f) => !f.endsWith('INDEX.md')),
      join(SKILL_DIR, 'SKILL.md'),
      join(PRACTICE_DIR, 'SKILL.md'),
      join(ROLE_DIR, 'persona.md'),
    ].filter((f) => readFileSync(f, 'utf8').includes('claude.ai/artifact'))
    expect(elsewhere).toEqual([])
  })

  it('every anchor the library names is one of the nine views', () => {
    const named: string[] = []
    for (const f of libraryFiles) {
      for (const line of readFileSync(f, 'utf8').split('\n')) {
        if (/^#+ /.test(line)) continue // a markdown heading, not an anchor
        for (const m of line.matchAll(/(?:^|[\s(`|])#([a-z][a-z-]*)\b/g)) named.push(m[1])
      }
    }
    expect(named.length).toBeGreaterThanOrEqual(EXPLAINER_ANCHORS.length)
    const allowed: readonly string[] = [...EXPLAINER_ANCHORS, EXAMPLE_ANCHOR]
    const unknown = named.filter((a) => !allowed.includes(a))
    expect(unknown).toEqual([])
  })

  it('each view is carried by exactly one explainer section and listed in the index', () => {
    const explainer = readFileSync(join(LIBRARY, 'how-the-operator-works.md'), 'utf8')
    for (const a of EXPLAINER_ANCHORS) {
      const n = explainer.split(`Explainer view: #${a}\n`).length - 1
      expect({ a, n }).toEqual({ a, n: 1 })
      // A table row whose first cell is the anchor (prettier pads the cell).
      const listed = index.split('\n').some((l) => l.replace(/\s+/g, ' ').startsWith(`| #${a} |`))
      expect({ a, listed }).toEqual({ a, listed: true })
    }
  })
})

type Scenario = {
  id: string
  role: string
  example: boolean
  situation: string
  objections: { says: string; answer: { document: string; section: string }[] }[]
}

describe('pitch practice material (ADR 0105)', () => {
  const doc = parseYaml(readFileSync(SCENARIOS, 'utf8')) as { scenarios: Scenario[] }
  const scenarios = doc.scenarios
  const rubric = readFileSync(RUBRIC, 'utf8')

  it('has 6 or 7 scenarios with unique ids, exactly one marked as the industry example', () => {
    expect(scenarios.length).toBeGreaterThanOrEqual(6)
    expect(scenarios.length).toBeLessThanOrEqual(7)
    expect(new Set(scenarios.map((x) => x.id)).size).toBe(scenarios.length)
    expect(scenarios.filter((x) => x.example === true).length).toBe(1)
    for (const x of scenarios) {
      expect(x.id).toMatch(/^[a-z][a-z-]*$/)
      expect(typeof x.role).toBe('string')
      expect(typeof x.situation).toBe('string')
    }
  })

  it('each scenario has 4 to 6 objections, each citing a library section that exists', () => {
    const missing: string[] = []
    for (const x of scenarios) {
      const n = x.objections.length
      expect({ id: x.id, ok: n >= 4 && n <= 6 }).toEqual({ id: x.id, ok: true })
      x.objections.forEach((o, i) => {
        if (!o.answer?.length) missing.push(`${x.id}[${i}]: no citation`)
        for (const c of o.answer ?? []) {
          const path = join(LIBRARY, c.document)
          if (!existsSync(path)) missing.push(`${x.id}[${i}]: no document ${c.document}`)
          else if (!sections(readFileSync(path, 'utf8')).includes(c.section))
            missing.push(`${x.id}[${i}]: ${c.document} has no section "${c.section}"`)
        }
      })
    }
    expect(missing).toEqual([])
  })

  it('every library section the rubric quotes exists', () => {
    const missing: string[] = []
    for (const m of rubric.matchAll(/([a-z-]+\.md), "([^"]+)"/g)) {
      const path = join(LIBRARY, m[1])
      if (!existsSync(path) || !sections(readFileSync(path, 'utf8')).includes(m[2]))
        missing.push(`${m[1]} "${m[2]}"`)
    }
    expect(missing).toEqual([])
    expect(sections(rubric).length).toBeGreaterThanOrEqual(5)
  })

  for (const [label, file] of [
    ['scenarios.yaml', SCENARIOS],
    ['rubric.md', RUBRIC],
    ['pitch-practice SKILL.md', join(PRACTICE_DIR, 'SKILL.md')],
    ['product-guide SKILL.md', join(SKILL_DIR, 'SKILL.md')],
  ] as const) {
    it(`${label}: no dollar amount, em dash, person, client or competitor name, or banned marker`, () => {
      const text = readFileSync(file, 'utf8')
      expect(hygieneErrors(text, NAMES, { runtime: false })).toEqual([])
      expect(COMPETITOR_NAMES.filter((n) => text.includes(n))).toEqual([])
      expect(markerHits(text)).toEqual([])
    })
  }

  it("a buyer's words clear the reply checklist: no exclamation, capitals or markup", () => {
    // In role the guide sends each objection word for word, so it must clear the
    // staff-send checklist the reply relay runs (overlay shared/output_checklist.py).
    const bad: string[] = []
    for (const x of scenarios)
      for (const o of x.objections) {
        if (o.says.includes('!')) bad.push(`${x.id}: exclamation`)
        const caps = o.says.match(/\b[A-Z]{4,}\b/)
        if (caps) bad.push(`${x.id}: ${caps[0]}`)
        if (/[*`#|]/.test(o.says)) bad.push(`${x.id}: markup`)
      }
    expect(bad).toEqual([])
  })

  it('a planted banned marker and a competitor name are caught', () => {
    expect(markerHits('We guarantee it.')).not.toEqual([])
    expect(markerHits('Plain sentence.')).toEqual([])
    expect(COMPETITOR_NAMES.filter((n) => 'We already use Clio.'.includes(n))).toEqual(['Clio'])
  })
})

describe('the checks can fail', () => {
  it('each hygiene rule catches a planted violation', () => {
    const run = (s: string) => hygieneErrors(s, NAMES, { runtime: true })
    expect(run('It costs $500 a month.')).not.toEqual([])
    expect(run('Billed at 400 dollars.')).not.toEqual([])
    expect(run('Fast — and safe.')).toEqual(['em dash'])
    expect(run('Built for the Captain.').length).toBeGreaterThan(0)
    expect(run('It runs on Hermes.').length).toBeGreaterThan(0)
    expect(run('See ADR 0090.').length).toBeGreaterThan(0)
    expect(run('Set in customer.yaml today.').length).toBeGreaterThan(0)
    expect(run('Read operator/bin/fleet for it.').length).toBeGreaterThan(0)
    expect(run('Enforced at enforce:136-152.').length).toBeGreaterThan(0)
    expect(run('Each client gets its own machine, and SMD Services runs it.')).toEqual([])
  })

  it('a derived client name is caught', () => {
    const someName = NAMES.find((n) => !FIXED_NAMES.includes(n))
    expect(someName).toBeDefined()
    expect(hygieneErrors(`Ask ${someName} about it.`, NAMES, { runtime: false })).not.toEqual([])
  })

  it('a broken relative link is caught', () => {
    expect(brokenLinks(join(LIBRARY, 'INDEX.md'), 'see [x](no-such-file.md)')).toEqual([
      'no-such-file.md',
    ])
  })
})
