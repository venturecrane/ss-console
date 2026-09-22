/**
 * Every scheduled skill that used to walk matters' memos states the rule.
 *
 * THE INCIDENT. A scheduled routine runs ONE model session across every open
 * matter. The overlay's matter-mixing fence refuses a second matter's memo or
 * document read inside one session, correctly: one session must not hold two
 * matters' content. On 2026-09-22 pilot-smokeball logged 16 such refusals in a
 * morning (motion-calendar-tracker 8, service-confirmation-watcher 7,
 * discovery-response-tracker 1), 15 of them inside one 30-minute window against
 * a brake that hard-stops the seat at 20.
 *
 * THE FIX, AND WHY IT NEEDS A TEST. The cross-matter read moved into code:
 * `operator/templates/pre_run_gate.py` reads the memos in the connector venv and
 * hands the model derived facts. But the code half cannot make the model stop
 * asking - only the skill's own text does that, and skill text is edited by
 * hand, skill by skill, month after month. A skill that quietly loses the rule
 * line goes back to asking, and the only place that shows up is a seat's
 * refusal count the following morning. This is the thing that notices first.
 *
 * WHY THE EXACT SENTENCE. Matching a paraphrase would let the line decay into
 * something that reads like the rule without stating it. One sentence, quoted,
 * in every skill that walks matters on a schedule.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = 'operator/skills'

/**
 * The rule, verbatim. Change it here and in every skill, or not at all.
 *
 * It names the two fenced tools AND says they are the only two, because the
 * first draft of this rule did not: a skill read "never calls read_document"
 * as "no document reads on a scan" and stopped the service watcher at the file
 * listing, which would have cost it the served date and method it exists to
 * capture. `matter_binding._CONTENT_READ_TOOLS` fences `get_memos_on_matter`
 * and `read_document` and nothing else; `get_files_on_matter`, `get_file` and
 * `get_download_url` are unfenced and stay available on a scan.
 */
const RULE =
  "a scheduled scan never calls `get_memos_on_matter` or `read_document`, the only two matter-content tools the seat fences, because it refuses a second matter's content in one session"

/**
 * The skills whose scheduled path used to make a fenced read. Four, not three:
 * medical-records-chaser derives its roster from tracking TASKS through its own
 * bespoke gate, so it needed no facts plumbing, but its text still pointed at
 * `get_memos_on_matter` for the roster and would have kept asking.
 */
const SKILLS = [
  'service-confirmation-watcher',
  'motion-calendar-tracker',
  'discovery-response-tracker',
  'medical-records-chaser',
]

function skillText(skill: string): string {
  // Markdown line-wraps, so the rule is matched against one flat string.
  return readFileSync(join(ROOT, skill, 'SKILL.md'), 'utf-8').replace(/\s+/g, ' ')
}

describe('the scheduled-scan memo fence rule', () => {
  it.each(SKILLS)('%s states the rule', (skill) => {
    expect(
      skillText(skill),
      `${skill}/SKILL.md no longer states the scheduled-scan rule. A scheduled run ` +
        `walks every open matter, and the seat refuses the second matter's memo read, ` +
        `so a skill that stops saying so goes back to burning the refusal brake.`
    ).toContain(RULE)
  })

  it('the rule is quoted, not merely described', () => {
    // Law 12 on the instrument: a probe that cannot fail measured nothing. If
    // the constant above ever drifted to something vacuous (an empty string,
    // a word every skill contains), every assertion here would pass forever.
    expect(RULE.length).toBeGreaterThan(80)
    const unrelated = skillText('trial-binder-assembler')
    expect(unrelated, 'the rule matches a skill that never had the problem').not.toContain(RULE)
  })
})
