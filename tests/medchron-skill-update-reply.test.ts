/**
 * An update request gets ONE useful reply (2026-10-29, matter 203508).
 *
 * A firm asked the Operator to add records the delivered chronology had not
 * read. It asked her to confirm two dates of birth the delivered chronology had
 * already run on, held the named records behind a question about four other
 * files, and buried its own question under the detail. Four emails followed.
 * String gates on the skill body the seat reads at turn time:
 *
 *   1. An update takes who it is for from the delivered chronology and never
 *      asks the firm to confirm a date of birth; the RUN rule is unchanged.
 *   2. Named records are submitted on the same turn; the rest are one offer
 *      line, never a blocking question.
 *   3. Records already on the matter get a sentence that admits no miss; the
 *      no-charge clause is gated on smd_page_credit_pages > 0.
 *   4. Any question leads the reply.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const dir = resolve(import.meta.dirname, '../operator/skills/medical-chronology-maintainer')
const body = readFileSync(resolve(dir, 'SKILL.md'), 'utf8')
const shapes = readFileSync(resolve(dir, 'references/reply-shapes.md'), 'utf8').replace(/\s+/g, ' ')
const update = body
  .slice(body.indexOf('## UPDATE'), body.indexOf('## DELIVER'))
  .replace(/\s+/g, ' ')
const run = body.slice(body.indexOf('## RUN'), body.indexOf('## UPDATE')).replace(/\s+/g, ' ')

const OFFER =
  "Also not yet in the chronology: <file name>, <N> pages. Reply add and I'll put them in a second update."
const ALREADY =
  "These files aren't listed among the records the chronology reviewed, so the update will check them against it."
const NO_CHARGE = "There's no charge to your allowance for this."

describe('medical-chronology-maintainer: an update request gets one useful reply', () => {
  it('takes identity from the delivered chronology only when the record holds it', () => {
    expect(update).toContain('comes from the delivered chronology when the record holds it')
    expect(update).toContain('`delivered_clients`')
    expect(update).toContain('and leave `dob` out')
    expect(update).toContain('Then never ask the firm about a date of birth')
    expect(update).not.toContain('identity-field rules apply unchanged')
  })

  it('degrades to the RUN rule when the fields are absent (skills roll before images)', () => {
    expect(update).toContain(
      '`delivered_clients` absent, the client not listed, or `dob_on_record` false or missing'
    )
    expect(update).toContain("RUN step 2's date-of-birth rules apply exactly as written")
    expect(update).toContain('Never leave `dob` out unless the entry says `dob_on_record: true`.')
    expect(update).toContain(
      'The field missing, 0, or anything that is not a number above 0: no billing sentence'
    )
    // RUN keeps its confirmation step.
    expect(run).toContain('Never submit on a date read from a document alone.')
  })

  it('submits the named records now and offers the rest in one line', () => {
    expect(update).toContain(
      '**The files the requester named go now; anything else is offered, never asked.**'
    )
    expect(update).toContain(OFFER)
    expect(update).toContain(
      'Never a question that waits on an answer before the named records are submitted'
    )
    expect(shapes).toContain(OFFER)
  })

  it('admits no miss, and gates the no-charge clause on a real credit', () => {
    expect(update).toContain(ALREADY)
    expect(update).toContain('Never say they were missed or left out')
    expect(update).toContain('Only when `job.smd_page_credit_pages` is greater than 0')
    expect(update).toContain(NO_CHARGE)
    expect(update).not.toContain('we missed them')
    expect(shapes).toContain(ALREADY)
    expect(shapes).toContain('`smd_page_credit_pages` is above 0')
  })

  it('leads with any question', () => {
    expect(update).toContain('**The reply: any question first, then the detail.**')
    expect(shapes).toContain('## Every reply: any question first, then the detail')
  })
})
