/**
 * The self-test's refusal demonstration cannot be performed against the firm's
 * records (ss#2511).
 *
 * Step 4 of `operator-self-test` exists to prove that the fabrication guard
 * refuses a case number the Operator never read. It said "create an internal
 * draft memo." On 2026-08-21, during the A&P stand-up rehearsal on
 * `hermes-ashton-price`, the Operator resolved "memo" to
 * `mcp_smokeball_create_memo` — a live write to the practice-management system,
 * not a draft of anything. The sentinel matter 404d, the Operator retried, and
 * the memo landed on a real matter in the firm's production Smokeball. It was
 * removed within the hour.
 *
 * Two things went wrong that day and this file pins one of them. The other, the
 * poisoned provenance register that let the sentinel verify in the first place,
 * is fixed in the overlay (`hermes-smd-overlay` PR for the same issue) and
 * tested there. This gate is the belt to that fix's braces: even with the guard
 * working, a self-test whose demonstration surface is a practice-management
 * write is one refusal-regression away from writing to a client matter again.
 *
 * The rule, mechanically: step 4 names a mail draft tool and no
 * practice-management write tool. Scoped to step 4 alone, because step 3 files
 * a certificate through the Smokeball render path on purpose (ss#2237) and a
 * whole-file ban would forbid the thing the skill is supposed to do.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const SKILL_PATH = resolve('operator/skills/operator-self-test/SKILL.md')

/**
 * Step 4's prose: from the "**4. Refuse" heading to the "**5. Deliver" heading.
 * If either marker moves, this throws rather than silently matching an empty
 * string — a gate that cannot find its subject has measured nothing.
 */
function stepFour(): string {
  const body = readFileSync(SKILL_PATH, 'utf8')
  const start = body.indexOf('**4. Refuse')
  const end = body.indexOf('**5. Deliver')
  if (start < 0 || end < 0 || end <= start) {
    throw new Error(
      `could not locate step 4 in ${SKILL_PATH} (start=${start}, end=${end}); ` +
        'the step headings changed shape and this gate needs re-pointing'
    )
  }
  return body.slice(start, end)
}

/**
 * Every Smokeball write verb the connector exposes. A write is a write whether
 * it is a memo, a task, an event or a file, and the incident turned on the
 * model picking the one that sounded most like "draft".
 */
const PRACTICE_MANAGEMENT_WRITES = [
  'mcp_smokeball_create_memo',
  'mcp_smokeball_create_task',
  'mcp_smokeball_update_task',
  'mcp_smokeball_create_event',
  'mcp_smokeball_create_matter',
  'mcp_smokeball_add_file',
  // Prefix, so it catches render_docx_draft and render_docx_template alike.
  'mcp_smokeball_render_docx',
  'practice_management_create_note',
]

describe('operator-self-test step 4 (ss#2511)', () => {
  it('names a mail draft tool as the demonstration surface', () => {
    const step = stepFour()
    const mailDrafts = ['mcp_msgraph_mail_create_draft', 'mcp_agentmail_create_draft']
    expect(mailDrafts.filter((t) => step.includes(t))).toEqual(mailDrafts)
  })

  it('names no practice-management write tool in the part that instructs', () => {
    const step = stepFour()
    // Step 4 has two halves. The first instructs: it says what to attempt and
    // with which tool, and that half must be clean. The second forbids and
    // explains, and it necessarily quotes `mcp_smokeball_create_memo` by name
    // so a reader knows exactly what not to reach for. Splitting at the
    // prohibition heading is what lets this gate be strict about the
    // instruction without banning the warning that exists because of it.
    const split = step.indexOf('**Step 4 never touches the practice-management system.**')
    expect(split, 'the prohibition paragraph is missing from step 4').toBeGreaterThan(0)
    const instruction = step.slice(0, split)
    for (const tool of PRACTICE_MANAGEMENT_WRITES) {
      expect(instruction, `step 4 must not reach for ${tool}`).not.toContain(tool)
    }
  })

  it('says in so many words that step 4 makes no practice-management write', () => {
    expect(stepFour()).toContain('never touches the practice-management system')
  })

  it('keeps the sentinel, which is the control the step exists to exercise', () => {
    expect(stepFour()).toContain('ZZ-9999-0001')
  })
})
