/**
 * The "built N times" census: one idea that should live in one place, counted
 * by how many places it lives in today (2026-11-10 architecture review, wave 0
 * item 0.9, the "What done means, in numbers" table).
 *
 * Each concept below has ONE stated detection rule, a target, and a function
 * that returns its instances (files, or one representative file per distinct
 * copy). tests/sprawl-concepts.test.ts holds a known answer for every detector
 * in both directions; tests/sprawl-ratchet.test.ts fails when a count rises
 * past scripts/sprawl/concept-baseline.json, and when one falls without the
 * baseline being lowered (`npx tsx scripts/sprawl-audit.ts --write-concept-baseline`).
 *
 * Only ideas a pattern can find reliably are here. The review's table names
 * more (job host loops, verdict shapes, job-run engines, page readers, paid
 * page stores, firm-settings loaders, broker job ledgers); those need a reader
 * of the code, not a pattern, and are left out rather than guessed.
 */

import {
  INSTRUMENT_PATH,
  type RatchetResult,
  type SourceFile,
  countRatchet,
  isTestPath,
} from './analyze'

export const CONCEPT_BASELINE = 'scripts/sprawl/concept-baseline.json'

export type Concept = {
  id: string
  rule: string
  target: string
  instances: (files: SourceFile[]) => string[]
}

/** This file states the vocabulary it searches for; it is the instrument, not an instance. */
const SELF = 'scripts/sprawl/concepts.ts'

const COMMENT_LINE = /^\s*(#|\/\/|\/\*|\*)/
const PROGRAM = /\.(ts|tsx|mjs|cjs|js|astro|py|sh|bash)$|^(operator\/bin|\.claude\/bin)\/[^/.]+$/

function codeLines(text: string): string[] {
  return text.split('\n').filter((line) => !COMMENT_LINE.test(line))
}

/** Non-test program files (no docs, no config data), the instrument itself left out. */
function programs(files: SourceFile[]): SourceFile[] {
  return files.filter(
    (f) =>
      f.path !== SELF &&
      !INSTRUMENT_PATH.test(f.path) &&
      !isTestPath(f.path) &&
      PROGRAM.test(f.path)
  )
}

function matching(files: SourceFile[], test: (code: string[]) => boolean): string[] {
  return files
    .filter((f) => test(codeLines(f.text)))
    .map((f) => f.path)
    .sort()
}

/** The queued job lanes and the skill that authors each (marketing has firm inputs, no daemon job). */
const LANE_SKILLS: Record<string, string> = {
  medchron: 'medical-chronology-maintainer',
  demand: 'demand-letter-drafter',
  drafting: 'document-drafter',
  litigation: 'litigation-status',
  casefile: 'case-file-manager',
  negotiation: 'negotiation-watch',
  note: 'smd-note',
  records: 'matter-records',
  library_setup: 'document-drafter',
  marketing: 'marketing-request-router',
}
const LANES = Object.keys(LANE_SKILLS)

const LANE_SET = new Set(LANES)

/** The lane names written as whole words on a line (a word is a run of letters, digits, _ and -). */
function laneWords(line: string): Set<string> {
  return new Set(line.split(/[^\w-]+/).filter((w) => LANE_SET.has(w)))
}

/** Distinct lanes written as a quoted string, an object key, or a shell case arm. */
function laneTokens(code: string[]): Set<string> {
  const out = new Set<string>()
  for (const line of code) {
    for (const m of line.matchAll(/['"`]([\w-]+)['"`]|([\w-]+)\s*[:)]/g)) {
      const word = m[1] ?? m[2]
      if (LANE_SET.has(word)) out.add(word)
    }
  }
  return out
}

function listsLanes(code: string[]): boolean {
  if (laneTokens(code).size >= 4) return true
  return code.some((line) => laneWords(line).size >= 4)
}

function laneSkillPairs(code: string[]): number {
  const pairs = new Set<string>()
  for (const line of code) {
    for (const lane of laneWords(line)) if (line.includes(LANE_SKILLS[lane])) pairs.add(lane)
  }
  return pairs.size
}

/** customer.yaml, customer_yaml or its upper-case constant, in any case. */
const NAMES_CUSTOMER_YAML = /customer[._]ya?ml/i

function parsesCustomerYaml(code: string[]): boolean {
  const text = code.join('\n')
  return /\b(yaml\.(safe_)?load|safe_load)\(/.test(text) && NAMES_CUSTOMER_YAML.test(text)
}

/** A function (declaration or arrow) whose name starts format/fmt and names money. A value such as
 * `const formattedAmount = \`$...\`` is a formatted string, not a formatter, and does not count. */
const MONEY_FORMATTER =
  /(?:function\s+(?:format|fmt)\w*(?:Currency|Money|Dollar|Cent|Amount|Price)\w*\s*\(|(?:const|let)\s+(?:format|fmt)\w*(?:Currency|Money|Dollar|Cent|Amount|Price)\w*\s*=\s*(?:\([^)]*\)|\w+)\s*(?::[^=]+)?=>)/

function definesMoneyFormatter(code: string[]): boolean {
  return code.some((line) => MONEY_FORMATTER.test(line))
}

const ISSUE_CALL = /\bgh issue \w+|\bissues\.(create|update|createComment|listForRepo)\b/g

function rollsAnIssue(code: string[]): boolean {
  return (code.join('\n').match(ISSUE_CALL) ?? []).length >= 4
}

/** Surplus byte-identical copies: per group of same-named identical files, every file past the first. */
function surplusCopies(files: SourceFile[]): string[] {
  const groups = new Map<string, string[]>()
  for (const f of files) {
    const key = `${f.path.split('/').pop()}\u0000${f.text}`
    groups.set(key, [...(groups.get(key) ?? []), f.path])
  }
  return [...groups.values()].flatMap((paths) => [...paths].sort().slice(1)).sort()
}

const WRITING_RULES = '## Delivery channels + refusal fallback (law seat rule)'

/** One representative SKILL.md per distinct body of the law-seat writing-rules section. */
function distinctWritingRuleBodies(files: SourceFile[]): string[] {
  const first = new Map<string, string>()
  for (const f of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    if (!f.path.endsWith('/SKILL.md')) continue
    const at = f.text.indexOf(WRITING_RULES)
    if (at < 0) continue
    const rest = f.text.slice(at + WRITING_RULES.length)
    const end = rest.search(/^## /m)
    const body = (end < 0 ? rest : rest.slice(0, end)).trim()
    if (!first.has(body)) first.set(body, f.path)
  }
  return [...first.values()].sort()
}

export const CONCEPTS: Concept[] = [
  {
    id: 'job-lane-lists',
    rule: 'Non-test program file naming 4+ job lanes as quoted strings, object keys or case arms, or 4+ on one line.',
    target: '1 declaration plus generated files',
    instances: (files) => matching(programs(files), listsLanes),
  },
  {
    id: 'lane-skill-tables',
    rule: 'Non-test program file pairing 4+ lanes with their skill names on the same line.',
    target: '1',
    instances: (files) => matching(programs(files), (code) => laneSkillPairs(code) >= 4),
  },
  {
    id: 'customer-yaml-hand-parsers',
    rule: 'Non-test Python file that calls a yaml load and names customer.yaml.',
    target: '1 reader per process',
    instances: (files) =>
      matching(
        programs(files).filter((f) => f.path.endsWith('.py')),
        parsesCustomerYaml
      ),
  },
  {
    id: 'broker-socket-clients',
    rule: 'Non-test Python file that opens an AF_UNIX socket and calls .connect(.',
    target: '1 client per process',
    instances: (files) =>
      matching(
        programs(files).filter((f) => f.path.endsWith('.py')),
        (code) =>
          code.some((l) => l.includes('AF_UNIX')) && code.some((l) => l.includes('.connect('))
      ),
  },
  {
    id: 'money-formatters',
    rule: 'File under src/ or workers/ defining a function (declaration or arrow) named format*/fmt* with Currency, Money, Dollar, Cent, Amount or Price in the name.',
    target: '1 (src/lib/format/money.ts)',
    instances: (files) =>
      matching(
        programs(files).filter((f) => /^(src|workers)\//.test(f.path)),
        definesMoneyFormatter
      ),
  },
  {
    id: 'resend-senders',
    rule: 'Non-test program file that posts to api.resend.com/emails itself.',
    target: '1 per language',
    instances: (files) =>
      matching(programs(files), (code) => code.some((l) => l.includes('api.resend.com/emails'))),
  },
  {
    id: 'inline-rolling-issue-workflows',
    rule: 'Workflow under .github/workflows with 4+ `gh issue` or `issues.*` write/list calls inline.',
    target: '0 (one rolling-issue command)',
    instances: (files) =>
      matching(
        files.filter((f) => /^\.github\/workflows\/[^/]+\.ya?ml$/.test(f.path)),
        rollsAnIssue
      ),
  },
  {
    id: 'hand-kept-python-copies',
    rule: 'Non-test Python file under operator/ byte-identical to a same-named file; every copy past the first counts.',
    target: '0 (stamped by a tool)',
    instances: (files) =>
      surplusCopies(programs(files).filter((f) => /^operator\/.*\.py$/.test(f.path))),
  },
  {
    id: 'writing-rules-bodies',
    rule: `Distinct bodies of the "${WRITING_RULES}" section across SKILL.md files.`,
    target: '1 plus stated additions',
    instances: distinctWritingRuleBodies,
  },
]

export type ConceptRow = {
  id: string
  count: number
  target: string
  rule: string
  instances: string[]
}

export function conceptCensus(files: SourceFile[], concepts = CONCEPTS): ConceptRow[] {
  return concepts.map((c) => {
    const instances = c.instances(files)
    return { id: c.id, count: instances.length, target: c.target, rule: c.rule, instances }
  })
}

/**
 * The fitness function: a count may not rise past its baseline (a concept with
 * no baseline row may not exist), and a count that fell fails until the
 * baseline is lowered, so a consolidation is locked in.
 *
 * @public Merge-gate check. tests/sprawl-ratchet.test.ts imports it.
 */
export function conceptRatchet(
  rows: ConceptRow[],
  baseline: Record<string, number>
): RatchetResult {
  return countRatchet(new Map(rows.map((r) => [r.id, r.count])), baseline, -1, 'places')
}

/** The baseline document the generator writes: counts gate; `$` keys carry rule and target for the reader. */
export function conceptBaselineDoc(rows: ConceptRow[]): Record<string, unknown> {
  const doc: Record<string, unknown> = {
    $comment:
      'Written by scripts/sprawl-audit.ts --write-concept-baseline; gated by tests/sprawl-ratchet.test.ts. Lower only.',
  }
  for (const r of rows) {
    doc[r.id] = r.count
    doc[`$${r.id}`] = `target ${r.target}. ${r.rule}`
  }
  return doc
}
