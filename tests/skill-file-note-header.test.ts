/**
 * Every Operator file note has one shape (output checklist items 3, 11, 13).
 *
 * On 2026-09-29 the pilot's notes carried `> **What:**` blocks, markdown
 * headings and tables, each skill in its own shape, and a paralegal could not
 * tell which routine wrote a note or when it last checked the matter. The pack
 * now has one file-note shape (`_shared-write-posture.md`, section 5):
 *
 *   [Operator] <Routine name> as of <localDate>
 *   <one line: what it found>
 *   <one line: what a person needs to do, or "Nothing to do.">
 *
 * This pins it where the model reads it: every skill output-format that tells
 * the model to write a `create_memo` shows the header line, and the lines of a
 * note under that header carry no quote marks, emphasis, headings or tables.
 * The model copies these templates; a template in the old shape is the
 * defect coming back.
 *
 * @see operator/verticals/law-firm/addons/pi/references/_shared-write-posture.md
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SKILLS = 'operator/skills'
const SHARED_ASSEMBLER =
  'operator/verticals/law-firm/addons/pi/references/_shared-assembler-output-format.md'
const HEADER = /^\[Operator\] .+ as of .+$/m

function outputFormats(): string[] {
  const out: string[] = []
  for (const skill of readdirSync(SKILLS)) {
    const path = join(SKILLS, skill, 'references', 'output-format.md')
    if (existsSync(path)) out.push(path)
  }
  return [...out, SHARED_ASSEMBLER]
}

/** Problems in one output-format's text; empty when it follows the shape. */
export function noteProblems(text: string): string[] {
  const problems: string[] = []
  if (!text.includes('create_memo')) return problems
  if (!HEADER.test(text))
    problems.push('no "[Operator] <Routine name> as of <localDate>" header line')
  if (/Internal log \(create_memo body/.test(text))
    problems.push('an old "Internal log (create_memo body)" block')
  if (/\*\*What:\*\*/.test(text)) problems.push('an old "**What:**" training block')
  const lines = text.split('\n')
  lines.forEach((line, i) => {
    if (!/^\[Operator\] .+ as of /.test(line)) return
    for (let j = i + 1; j < lines.length; j++) {
      const note = lines[j]
      if (note.trim() === '' || note.startsWith('```')) break
      if (/^\s*[>#|]/.test(note) || note.includes('**')) {
        problems.push(`note line ${j + 1} carries markup: ${note.slice(0, 60)}`)
      }
      // A machine-read marker passes the seat's note gate only when it is the
      // whole line (the overlay anchors its exemption).
      if (note.includes('fileId') && !/^fileId \S.* recorded\.?$/.test(note)) {
        problems.push(`note line ${j + 1} folds the fileId marker into prose`)
      }
    }
  })
  return problems
}

function voiceFiles(): string[] {
  return readdirSync(SKILLS)
    .map((skill) => join(SKILLS, skill, 'references', 'voice.md'))
    .filter((path) => existsSync(path))
}

/**
 * A voice.md teaches tone by example, and the model copies its examples. It
 * must not teach the retired "internal log (create_memo body)" or show a
 * file-note example as a `>` quote.
 */
export function voiceProblems(text: string): string[] {
  const problems: string[] = []
  if (/internal log/i.test(text)) problems.push('names the retired "internal log"')
  const lines = text.split('\n')
  lines.forEach((line, i) => {
    if (!/^\*\*(Good|Bad)[^*]*(file note|internal log)[^*]*:\*\*/i.test(line)) return
    const next = lines.slice(i + 1).find((l) => l.trim() !== '')
    if (next !== undefined && next.startsWith('>')) {
      problems.push(`the example after line ${i + 1} is a ">" quote`)
    }
  })
  return problems
}

describe('the one file-note shape', () => {
  it('the check fires on the pre-2026-09-29 shape (a check that cannot fail measures nothing)', () => {
    const old = [
      '## Internal log (create_memo body)',
      '',
      '> Chase <#> for <holder>/<lien-type>; payoff/reduction still outstanding.',
    ].join('\n')
    expect(noteProblems(old).length).toBeGreaterThan(0)
    const quoted =
      'create_memo\n[Operator] Records chase as of <localDate>\n> **What:** chased the provider.\n'
    expect(noteProblems(quoted)).toContain('an old "**What:**" training block')
    expect(noteProblems('create_memo\n[Operator] X as of <localDate>\n| a | b |\n').length).toBe(1)
    expect(
      noteProblems(
        'create_memo\n[Operator] X as of <localDate>\nServed (fileId <file id> recorded).\n'
      )
    ).toHaveLength(1)
    expect(
      noteProblems(
        'create_memo\n[Operator] X as of <localDate>\nServed.\nfileId <file id> recorded\n'
      )
    ).toEqual([])
    const plain =
      'create_memo\n[Operator] Records chase as of <localDate>\nRecords still outstanding.\nNothing to do.\n'
    expect(noteProblems(plain)).toEqual([])
  })

  for (const path of outputFormats()) {
    it(path, () => {
      expect(noteProblems(readFileSync(path, 'utf8'))).toEqual([])
    })
  }

  it('the voice check fires on the retired voice shape', () => {
    const old =
      '## The internal log (create_memo body)\n\n**Good - internal log:**\n\n> Assembled it.\n'
    expect(voiceProblems(old).length).toBeGreaterThan(0)
    expect(voiceProblems('**Good - file note:**\n\n> Assembled it.\n')).toHaveLength(1)
    expect(
      voiceProblems('**Good - file note:**\n\n```\n[Operator] X as of <localDate>\n```\n')
    ).toEqual([])
  })

  for (const path of voiceFiles()) {
    it(path, () => {
      expect(voiceProblems(readFileSync(path, 'utf8'))).toEqual([])
    })
  }

  it('the write posture states the one-note rule', () => {
    const posture = readFileSync(
      'operator/verticals/law-firm/addons/pi/references/_shared-write-posture.md',
      'utf8'
    )
    expect(posture).toContain('One file note per routine per matter')
    expect(posture).toContain('[Operator] <Routine name> as of <localDate>')
    expect(posture).toContain('unchanged: true')
  })
})
