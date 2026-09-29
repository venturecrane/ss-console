/**
 * A skill's output-format says the same delivery path its SKILL.md does
 * (output checklist item 12: a table or a document belongs in a Word document,
 * and the note points to it).
 *
 * Found 2026-09-29: three output-format files still told the model to put a
 * work product into a memo after their SKILL.md moved it to
 * `render_docx_draft`. discovery-response-drafter's said the draft is "written
 * into the matter (`create_memo`)", mediation-brief-drafter's said the brief
 * lives in "`create_memo` or a staged document", and
 * medical-chronology-maintainer's described a "running chronology (create_memo
 * body)" its SKILL.md says it never keeps. The model reads both files, and the
 * stale one wins often enough to matter.
 *
 * The check is as narrow as the real files allow: a skill whose SKILL.md names
 * `render_docx_draft` must name it in its output-format too, and no
 * output-format may head a block "(create_memo body" (the pack's notes are
 * "File note (create_memo)" blocks now; a memo BODY heading is how an artifact
 * got into a note).
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SKILLS = 'operator/skills'

/** Problems for one skill, given its SKILL.md and output-format.md text. */
export function parityProblems(skillMd: string, outputFormat: string): string[] {
  const problems: string[] = []
  if (skillMd.includes('render_docx_draft') && !outputFormat.includes('render_docx_draft')) {
    problems.push(
      'SKILL.md files a Word document with render_docx_draft; output-format.md never says so'
    )
  }
  if (/\(create_memo body/.test(outputFormat)) {
    problems.push('output-format.md puts content in a "create_memo body"')
  }
  if (/`create_memo` or a staged document/.test(outputFormat)) {
    problems.push('output-format.md lets the artifact live in a memo')
  }
  return problems
}

function skillsWithBoth(): string[] {
  return readdirSync(SKILLS).filter(
    (s) =>
      existsSync(join(SKILLS, s, 'SKILL.md')) &&
      existsSync(join(SKILLS, s, 'references', 'output-format.md'))
  )
}

describe('output-format matches the SKILL.md delivery path', () => {
  it('the check fires on the pre-fix text (a check that cannot fail measures nothing)', () => {
    const skill = 'file it with `mcp_smokeball_render_docx_draft(matter_id, ...)`'
    expect(
      parityProblems(skill, 'Written into the matter (`create_memo`, confirmed by read)')
    ).toHaveLength(1)
    expect(
      parityProblems('', 'The brief text lives in the matter (`create_memo` or a staged document).')
    ).toHaveLength(1)
    expect(parityProblems('', '## The running chronology (create_memo body)')).toHaveLength(1)
    expect(parityProblems(skill, 'filed with `render_docx_draft`; the note names it')).toEqual([])
  })

  for (const skill of skillsWithBoth()) {
    it(skill, () => {
      const skillMd = readFileSync(join(SKILLS, skill, 'SKILL.md'), 'utf8')
      const format = readFileSync(join(SKILLS, skill, 'references', 'output-format.md'), 'utf8')
      expect(parityProblems(skillMd, format)).toEqual([])
    })
  }
})
