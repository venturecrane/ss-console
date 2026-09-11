/**
 * The seat skill speaks the routine-11 allowance in PAGES (2026-09-09).
 *
 * This is a string gate on a skill body because the skill body IS the runtime
 * for these turns: the seat reads `/app/skills/<slug>/SKILL.md` at turn time
 * and the model acts on what it says. There is no code path between the
 * sentence and the act to unit-test instead.
 *
 * Three properties, each with a real failure behind it:
 *
 *   1. The unit. The broker's response field is `allowance_remaining_pages`;
 *      `allowance_remaining_documents` is carried for one release only, for an
 *      old broker restarting mid-rollout, and the skill must not learn it. A
 *      skill that quotes the document field would tell a firm 15,000 documents
 *      remain when 15,000 PAGES do.
 *
 *   2. No dollar figures. The seat's content gates refuse an agent-drafted
 *      dollar amount on sight (live 2026-08-31: a held-job report quoting the
 *      runner's cost projection was refused four times and never landed), so a
 *      skill that models one in its own prose teaches the model to write a
 *      reply that cannot be sent.
 *
 *   3. The limits are named by setting, so a hold reply can say which one held
 *      it. A reply that says only "it was held" sends the firm to us to find
 *      out what changed.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const SKILL = resolve(
  import.meta.dirname,
  '../operator/skills/medical-chronology-maintainer/SKILL.md'
)
const body = readFileSync(SKILL, 'utf8')

describe('medical-chronology-maintainer: the allowance unit', () => {
  it('names the pages field the broker returns and never the document field', () => {
    expect(body).toContain('allowance_remaining_pages')
    expect(
      body,
      'the document field is a one-release compatibility key; the skill must not learn it'
    ).not.toContain('allowance_remaining_documents')
  })

  it('tells the turn to quote the unit field rather than a remembered unit', () => {
    expect(body).toContain('`unit`')
    expect(body.toLowerCase()).toContain('page allowance')
  })

  it('models no dollar figure in the sections that say what to write back', () => {
    // From BUILD onward: the request, append, and deliver turns, plus the
    // boundaries. The earlier "Inputs" section deliberately quotes a figure as
    // an example of untrusted record CONTENT, which is the opposite lesson.
    const start = body.indexOf('## BUILD')
    expect(start).toBeGreaterThan(-1)
    const figures = body.slice(start).match(/\$\s?\d|\bUSD\b/g) ?? []
    expect(figures, `the reply guidance models a dollar figure: ${figures.join(', ')}`).toEqual([])
  })

  it('names no per-matter page ceiling: the cycle allowance is the only page limit', () => {
    // Removed 2026-09-10 (Captain). The firm buys a cycle allowance and spends
    // it as it likes, so a matter is never held for its size alone. If this
    // string returns to the skill body, the Operator would explain a hold the
    // runner can no longer produce -- and the firm would be told its own
    // allowance is unusable in one piece.
    expect(body).not.toContain('single_matter_page_threshold')
  })

  it('names each limit by the setting a hold reason carries', () => {
    for (const setting of [
      'per_job_cap_usd',
      'chronology_package_page_allowance_per_month',
      'monthly_budget_usd',
    ]) {
      expect(body, `a hold naming ${setting} must be explainable from the skill body`).toContain(
        setting
      )
    }
  })
})
