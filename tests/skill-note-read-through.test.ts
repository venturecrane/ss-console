/**
 * What a paralegal found reading live Operator notes on 2026-09-29, pinned
 * where the model reads it (output checklist items 2, 3, 5 and 13).
 *
 * After #2992 and #2993 deployed, a paralegal read the pilot's file notes and a
 * Word document and found five classes of defect, every one of them taught by
 * the skills' own templates:
 *
 *   1. Internal ids: "Oct 6 at 9:30 a.m., Dept 3 (event cef69a47)", "reported in
 *      task 98570c78". Twelve skill files taught "cite the source id".
 *   2. The same rule paragraph on every motion-calendar note, including matters
 *      with no motions, ending "attorney to confirm if any motion is pending".
 *   3. A ten-line "Training note:" paragraph at the end of the trial-binder note.
 *   4. Routine names as owners in the digest: "Owner: trial-binder-assembler",
 *      "Under active escalation by deadline-miss-escalator".
 *   5. A binder index Word document opening "Decision: assembled from matter
 *      components" with a line "CRITICAL: Trial is October 13, 2026".
 *
 * The model copies templates. A template in the old shape is the defect coming
 * back, so these checks read the templates (fenced blocks and `>` examples),
 * not the prose that explains why they changed. An example under a "**Bad"
 * label is exempt: it shows the defect on purpose. Machine-marker lines
 * (`fileId <id> recorded`, `op-mmou:...`, `Package job: ...`, `facts <digest>`)
 * are exempt: they are machine-read and never a person's reading.
 *
 * @see operator/verticals/law-firm/addons/pi/references/_shared-write-posture.md
 * @see operator/verticals/law-firm/addons/pi/references/_shared-training-output.md
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SKILLS = 'operator/skills'
const PI = 'operator/verticals/law-firm/addons/pi/references'

const SLUGS = readdirSync(SKILLS).filter((s) => existsSync(join(SKILLS, s, 'SKILL.md')))
/** A one-word slug ("workspace") is an ordinary word; only hyphenated slugs are names. */
const NAMED_SLUGS = SLUGS.filter((s) => s.includes('-'))

const MARKER = [
  /^\(?fileId\s+\S+\s+recorded\)?\.?$/,
  /^op-mmou:/,
  /^Package job: /,
  /^facts (?:[0-9a-f]{12}|<[^>]+>)$/,
]
const isMarker = (line: string) => MARKER.some((re) => re.test(line.trim()))

function skillFiles(): string[] {
  const out: string[] = []
  for (const skill of SLUGS) {
    for (const rel of ['SKILL.md', 'references/output-format.md', 'references/voice.md']) {
      const path = join(SKILLS, skill, rel)
      if (existsSync(path)) out.push(path)
    }
  }
  for (const name of readdirSync(PI)) if (name.endsWith('.md')) out.push(join(PI, name))
  return out
}

interface Block {
  line: number
  text: string[]
}

/**
 * The reader-facing template text in one file: every fenced block that is a
 * note (holds an `[Operator] ... as of` header) or a report/document
 * (```markdown), and every `>` example line in an output-format or voice file.
 * Blocks under a "**Bad" label are skipped.
 */
export function templateBlocks(text: string, withQuotes: boolean): Block[] {
  const lines = text.split('\n')
  const blocks: Block[] = []
  let label = ''
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (/^\*\*(Good|Bad)\b/.test(line)) label = line
    else if (/^#{1,6} /.test(line)) label = ''
    if (line.startsWith('```')) {
      const lang = line.slice(3).trim()
      const body: string[] = []
      let j = i + 1
      for (; j < lines.length && !lines[j].startsWith('```'); j++) body.push(lines[j])
      const isNote = body.some((l) => /^\[Operator\] .+ as of /.test(l))
      if ((isNote || lang === 'markdown') && !label.startsWith('**Bad')) {
        blocks.push({ line: i + 1, text: body.filter((l) => !isMarker(l)) })
      }
      i = j
      continue
    }
    if (withQuotes && line.startsWith('>') && !label.startsWith('**Bad')) {
      blocks.push({ line: i + 1, text: [line] })
    }
  }
  return blocks
}

const ID_PATTERNS: RegExp[] = [
  /\((?:event|task|file|memo|document)s?\s+(?:<[^>]*\bid\b[^>]*>|[0-9a-f]{6,}|ev-\d+)[^)]*\)/i,
  /\b(?:event|task|memo) ids?\b/i,
  /\b(?:event|task|file|memo) [0-9a-f]{8}\b/i,
  /\b(?:event|task|memo|file) <id>/i,
  /\bmatter <id>/i,
  /<fileId\/name>/,
]

export function idProblems(block: Block): string[] {
  return block.text
    .filter((l) => ID_PATTERNS.some((re) => re.test(l)))
    .map((l) => `line ~${block.line}: names a record by id: ${l.trim().slice(0, 80)}`)
}

export function slugProblems(block: Block): string[] {
  const out: string[] = []
  for (const l of block.text) {
    for (const slug of NAMED_SLUGS) {
      if (new RegExp(`(^|[^a-z-])${slug}($|[^a-z-])`).test(l)) {
        out.push(`line ~${block.line}: names the routine "${slug}": ${l.trim().slice(0, 80)}`)
      }
    }
  }
  return out
}

const withQuotes = (path: string) => !path.endsWith('SKILL.md')

describe('a record is named the way a person finds it, never by its id', () => {
  it('the check fires on the pre-2026-09-29 templates (a check that cannot fail measures nothing)', () => {
    const old = [
      '```',
      '[Operator] Motion calendar as of <localDate>',
      'Next: MSJ on Oct 6 at 9:30 a.m., Dept 3 (event <id>).',
      '```',
      '> Hearing 2026-08-14 "MSJ" (event ev-3320) has no matching filed-MSJ item.',
      '> Reported in task 98570c78, unconfirmed.',
    ].join('\n')
    const found = templateBlocks(old, true).flatMap(idProblems)
    expect(found).toHaveLength(3)
    // The same line under a Bad label shows the defect on purpose.
    expect(templateBlocks(`**Bad - an id:**\n\n${old}`, true).flatMap(idProblems)).toEqual([])
    // A machine marker is not reader text.
    const marker = '```\n[Operator] X as of <localDate>\nServed.\nfileId <file id> recorded\n```'
    expect(templateBlocks(marker, true).flatMap(idProblems)).toEqual([])
  })

  for (const path of skillFiles()) {
    it(path, () => {
      const blocks = templateBlocks(readFileSync(path, 'utf8'), withQuotes(path))
      expect(blocks.flatMap(idProblems)).toEqual([])
    })
  }

  it('the write posture states the rule', () => {
    const posture = readFileSync(join(PI, '_shared-write-posture.md'), 'utf8')
    expect(posture).toContain('Name a record the way a person finds it, never by its id')
    expect(posture).toContain('a task by its title')
  })
})

describe('a person or the Operator owns an item, never a routine', () => {
  it('the check fires on the digest line a paralegal read on 2026-09-29', () => {
    const old = [
      '```',
      '[Operator] Daily digest as of <localDate>',
      'Due soon: matter 2026-0142, binder, due Oct 2. Owner: trial-binder-assembler / attorney.',
      'Matter 2026-0177, verification: under active escalation by deadline-miss-escalator.',
      '```',
    ].join('\n')
    expect(templateBlocks(old, true).flatMap(slugProblems)).toHaveLength(2)
    expect(NAMED_SLUGS.length).toBeGreaterThan(40)
  })

  for (const path of skillFiles()) {
    if (path.endsWith('SKILL.md')) continue // a SKILL.md names its neighbours in guidance, not in a template
    it(path, () => {
      const blocks = templateBlocks(readFileSync(path, 'utf8'), withQuotes(path))
      expect(blocks.flatMap(slugProblems)).toEqual([])
    })
  }

  it('the digest dates items the firm way and names a person as owner', () => {
    const format = readFileSync(
      join(SKILLS, 'daily-needs-you-digest', 'references', 'output-format.md'),
      'utf8'
    )
    const note = templateBlocks(format, false)
      .flatMap((b) => b.text)
      .join('\n')
    expect(note).toContain('Owner: <person, or the Operator>')
    expect(note).not.toMatch(/Owner: <routine>|YYYY-MM-DD|\d{4}-\d{2}-\d{2}/)
  })
})

describe('a file note carries no training paragraph', () => {
  const INSTRUCTS = [
    /Training note:/,
    /appends?,? (?:a short [a-z -]*note )?(?:to|in) the matter memo/i,
    /carries, in the (?:matter memo|file note)/i,
    /Training output \(built into every/,
    /training note rides in/i,
  ]

  it('the check fires on the pre-2026-09-29 wording', () => {
    const old =
      'Every run appends, to the matter memo, a short note a junior paralegal learns from:'
    expect(INSTRUCTS.some((re) => re.test(old))).toBe(true)
    expect(INSTRUCTS.some((re) => re.test('## Training output (built into every run)'))).toBe(true)
  })

  // The two shared references quote "Training note:" to forbid it; they are
  // checked for the rule itself below.
  for (const path of skillFiles().filter((p) => !p.startsWith(PI))) {
    it(path, () => {
      const text = readFileSync(path, 'utf8')
      expect(INSTRUCTS.filter((re) => re.test(text)).map(String)).toEqual([])
    })
  }

  it('the shared training reference sends the explanation to the person who asks', () => {
    const shared = readFileSync(join(PI, '_shared-training-output.md'), 'utf8')
    expect(shared).toContain('A file note never carries a training paragraph')
    expect(shared).toContain('The explanation is given on request')
  })
})

describe('nothing to report is two short lines', () => {
  const MOTION = readFileSync(
    join(SKILLS, 'motion-calendar-tracker', 'references', 'output-format.md'),
    'utf8'
  )

  it('a motion calendar with no motions is exactly "No motions on file." and "Nothing to do."', () => {
    const empty = templateBlocks(MOTION, false).find((b) => b.text.includes('No motions on file.'))
    expect(empty?.text).toEqual([
      '[Operator] <Routine name> as of <localDate>',
      'No motions on file.',
      'Nothing to do.',
    ])
  })

  it('no note template asks a person to confirm what is not there, or speaks of an earlier run', () => {
    for (const path of skillFiles()) {
      const notes = templateBlocks(readFileSync(path, 'utf8'), false)
        .filter((b) => b.text.some((l) => /^\[Operator\] /.test(l)))
        .flatMap((b) => b.text)
      for (const line of notes) {
        expect(line, path).not.toMatch(/if any motion is pending|Prior surface|last surfaced/i)
      }
    }
  })

  it('the rule sentence is never a standing line on the motion note', () => {
    const noteLines = templateBlocks(MOTION, false)
      .filter((b) => b.text.some((l) => /^\[Operator\] /.test(l)))
      .flatMap((b) => b.text)
    // The rule text is conditional ("Only when a window is missing") and in plain
    // words; the section-number paragraph from the live notes is gone.
    expect(noteLines.join('\n')).not.toMatch(/CCP 1005\(b\); summary judgment follows CCP 437c/)
    expect(noteLines.join('\n')).toMatch(/Only when a window is missing/)
  })

  const FACT_TRACKERS = [
    'motion-calendar-tracker',
    'discovery-response-tracker',
    'service-confirmation-watcher',
    'client-verification-tracker',
    'lien-ledger-tracker',
    'mediation-settlement-tracker',
  ]
  for (const skill of FACT_TRACKERS) {
    it(`${skill} copies the facts digest and says nothing when there is nothing`, () => {
      const text = readFileSync(join(SKILLS, skill, 'references', 'output-format.md'), 'utf8')
      expect(text).toContain('facts_digest')
      expect(text).toMatch(/copied exactly/)
      expect(text).toMatch(/\nNothing to do\.\n/)
    })
  }

  it('the write posture teaches the facts line', () => {
    const posture = readFileSync(join(PI, '_shared-write-posture.md'), 'utf8')
    expect(posture).toContain('`facts <digest>`')
    expect(posture).toContain('Nothing to report is two short lines')
  })
})

describe('a document reads like a paralegal wrote it', () => {
  const ASSEMBLERS = [
    join(SKILLS, 'trial-binder-assembler', 'references', 'output-format.md'),
    join(SKILLS, 'separate-statement-assembler', 'references', 'output-format.md'),
    join(SKILLS, 'motion-package-assembler', 'references', 'output-format.md'),
    join(SKILLS, 'minors-compromise-packet', 'references', 'output-format.md'),
    join(SKILLS, 'settlement-statement-feeder', 'references', 'output-format.md'),
    join(PI, '_shared-assembler-output-format.md'),
  ]

  /** Shape A: the first ```markdown block, the Word document's draft_markdown. */
  function documentBlock(path: string): string[] {
    const block = templateBlocks(readFileSync(path, 'utf8'), false).find((b) =>
      b.text.some((l) => l.startsWith('# '))
    )
    if (!block) throw new Error(`no document template in ${path}`)
    return block.text
  }

  it('the check fires on the binder index a paralegal opened on 2026-09-29', () => {
    const old = [
      '# Trial Binder - <matter descriptor> - matter <id> - YYYY-MM-DD',
      '',
      '**Decision:** assembled from matter components; staged for <attorney> to finalize.',
      'CRITICAL: Trial is October 13, 2026.',
    ]
    expect(documentProblems(old)).toHaveLength(3)
  })

  for (const path of ASSEMBLERS) {
    it(path, () => {
      expect(documentProblems(documentBlock(path))).toEqual([])
    })
  }

  it('the write posture bans capitals for emphasis', () => {
    const posture = readFileSync(join(PI, '_shared-write-posture.md'), 'utf8')
    expect(posture).toContain('No capitals for emphasis')
  })
})

export function documentProblems(lines: string[]): string[] {
  const problems: string[] = []
  const first = lines.find((l) => l.trim() !== '') ?? ''
  if (!/^# .*\(draft\)/.test(first))
    problems.push(`first line is not a title: ${first.slice(0, 60)}`)
  if (lines.some((l) => /Decision:/.test(l))) problems.push('a "Decision:" line')
  if (lines.some((l) => /\b(?:CRITICAL|URGENT|IMPORTANT|WARNING)\b/.test(l)))
    problems.push('capitals for emphasis')
  return problems
}

/**
 * A rule never quotes the thing it bans (2026-09-29, the #2860 trap again).
 *
 * After the fix round the pilot's binder index still opened with a capitalised
 * warning line, because the shared assembler rule quoted exactly that phrase
 * as the thing not to write, and the model reproduced it. Skills that forbade
 * em dashes while showing one had the same effect (#2860). A rule states the
 * positive form; it does not show the banned form in quotes.
 *
 * Checked on every model-read skill file and shared reference: an all-caps
 * word of five or more letters inside quotes, next to a ban word ("no",
 * "never", "not", "avoid", "without") or a mention of emphasis; an em dash
 * anywhere; an entry id shown as "(event <id>)" or similar.
 */
const BANNED_CAPS_QUOTE =
  /(?:\b(?:no|never|not|avoid|without)\b[^"\n]{0,30}|emphasis[^"\n]{0,60})"[^"\n]*"/gi
const CAPS_WORD = /\b[A-Z][A-Z']{4,}\b/
const QUOTED_ID = /"\(?(?:event|task|memo|file|document) (?:<id>|[0-9a-f]{8}|ev-\d+)\)?"/

export function antiExampleProblems(text: string): string[] {
  const out: string[] = []
  text.split('\n').forEach((line, i) => {
    // The ban word is matched case-insensitively, the capitals are not: a
    // lower-case quote next to "no" is ordinary prose.
    for (const m of line.matchAll(BANNED_CAPS_QUOTE)) {
      if (CAPS_WORD.test(m[0].slice(m[0].indexOf('"')))) {
        out.push(`line ${i + 1}: quotes a capitalised word as the thing to avoid`)
        break
      }
    }
    if (line.includes('—')) out.push(`line ${i + 1}: an em dash`)
    if (QUOTED_ID.test(line)) out.push(`line ${i + 1}: shows an entry id as the thing to avoid`)
  })
  return out
}

describe('a rule never quotes the thing it bans', () => {
  it('the check fires on the rule lines that taught the defect (a check that cannot fail measures nothing)', () => {
    const old = [
      '   for emphasis ("CRITICAL: Trial is ..."): a date paragraph says',
      '- No capitals for emphasis ("DRAFT"); say "a draft".',
      '- No "URGENT," no guilt, no manufactured deadline.',
      '- **No capitals for emphasis** in a note, an email or a document ("CRITICAL",',
      '  its date. Never "(event <id>)", "task <id>" or "memo <id>". A',
      '  human can check it. Never by id: a paralegal cannot look up "event cef69a47".',
      'this — copy it and add only what is skill-specific.',
    ].join('\n')
    expect(antiExampleProblems(old)).toHaveLength(7)
    // Not a ban: a file name, a caption title, a quoted firm label with no ban word.
    const fine = [
      'the requests read from "RFP SET ONE RESPONSES - served by defendant.pdf".',
      'title "SEPARATE STATEMENT IN SUPPORT OF MOTION TO COMPEL FURTHER RESPONSES"',
      '   "the task is marked URGENT in Smokeball" / "the next line"',
      '- Ordinary case throughout, no capitals for emphasis: say "a draft".',
    ].join('\n')
    expect(antiExampleProblems(fine)).toEqual([])
  })

  for (const path of skillFiles()) {
    it(path, () => {
      expect(antiExampleProblems(readFileSync(path, 'utf8'))).toEqual([])
    })
  }
})
