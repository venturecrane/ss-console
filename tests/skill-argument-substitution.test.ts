/**
 * Regression guard: a slash-command skill must not contain `$` followed by a digit.
 *
 * Files under docs/skills/<name>/SKILL.md are symlinked to
 * .claude/commands/<name>.md by scripts/install-captain-skills.sh, which makes
 * them slash commands. The loader substitutes positional arguments into `$0`,
 * `$1`, `$2`... tokens, so a literal dollar figure like `$0.02` or `$3.68` is
 * rewritten with the caller's argument text before the model ever reads it.
 *
 * On 2026-09-08, invoking `/medchron <matter description>` served a copy of the
 * skill in which the argument string had been spliced into three separate cost
 * figures in the calibration section. The decision rules survived intact, but
 * the calibration prose did not, and nothing in the pipeline could have noticed.
 *
 * Write `USD 34`, `2 cents`, or `15/75 per million tokens` instead.
 *
 * @see docs/skills/medchron/SKILL.md — "Never write a bare `$` followed by a digit"
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, existsSync, statSync } from 'fs'
import { resolve, join } from 'path'

const SKILLS_ROOT = resolve('docs/skills')

/** Every SKILL.md that install-captain-skills.sh turns into a slash command. */
function collectSkillFiles(): string[] {
  if (!existsSync(SKILLS_ROOT)) return []
  const out: string[] = []
  for (const entry of readdirSync(SKILLS_ROOT)) {
    const dir = join(SKILLS_ROOT, entry)
    if (!statSync(dir).isDirectory()) continue
    const skill = join(dir, 'SKILL.md')
    if (existsSync(skill)) out.push(skill)
  }
  return out
}

/** `$` immediately followed by a digit — the exact shape the loader rewrites. */
const DOLLAR_DIGIT = /\$\d/

describe('slash-command skills survive argument substitution', () => {
  const files = collectSkillFiles()

  it('finds the skill files it is meant to guard', () => {
    // A check that cannot fail has measured nothing: if the glob breaks, the
    // rest of this suite would pass vacuously over an empty list.
    expect(files.length).toBeGreaterThan(0)
  })

  it.each(files)('%s contains no bare $ followed by a digit', (file) => {
    const offenders = readFileSync(file, 'utf-8')
      .split('\n')
      .map((line, i) => ({ line, n: i + 1 }))
      .filter(({ line }) => DOLLAR_DIGIT.test(line))
      .map(({ line, n }) => `  ${file}:${n}  ${line.trim()}`)

    expect(
      offenders,
      `The slash-command loader substitutes positional arguments into $0, $1, $2...\n` +
        `These lines would be rewritten with the caller's argument text:\n` +
        offenders.join('\n') +
        `\nWrite "USD 34", "2 cents", or "15/75 per million tokens" instead.`
    ).toEqual([])
  })

  it('the guard can actually fail', () => {
    // Positive control: the pattern must reject the shape it exists to catch.
    expect(DOLLAR_DIGIT.test('roughly $0.02 per audit call')).toBe(true)
    expect(DOLLAR_DIGIT.test('roughly 2 cents per audit call')).toBe(false)
    expect(DOLLAR_DIGIT.test('projects above USD 150')).toBe(false)
  })
})
