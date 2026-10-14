/**
 * An update goes INTO the delivered chronology (2026-10-13).
 *
 * Before this, the skill's UPDATE submitted the named documents as an "append
 * run": the runner built a standalone package of only those documents, and
 * the firm got a second, partial chronology beside the first. The runner now
 * takes `update: {into: "delivered_chronology"}` and files ONE complete
 * document (operator/runners/medchron/medchron/update/). A string gate on the
 * skill body, because the seat reads SKILL.md at turn time and submits what
 * it says:
 *
 *   1. UPDATE submits the update block with the delivered chronology's id
 *      (or lets the runner find it by the firm's own file name).
 *   2. A matter whose coverage record is missing but whose files hold a
 *      delivered chronology is still an UPDATE (the laptop pipeline's
 *      deliveries carry no record), measured against the delivered exhibits,
 *      never a "do not submit" and never a full RUN.
 *   3. The superseded document is removed by the runner, after the updated one
 *      is proven, and by nothing else; the skill itself never deletes.
 *   4. DELIVER says the chronology was updated in its folder.
 *   5. PR #3169's routing sentence stands.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const body = readFileSync(
  resolve(import.meta.dirname, '../operator/skills/medical-chronology-maintainer/SKILL.md'),
  'utf8'
)
const run = body.slice(body.indexOf('## RUN'), body.indexOf('## UPDATE')).replace(/\s+/g, ' ')
const update = body
  .slice(body.indexOf('## UPDATE'), body.indexOf('## DELIVER'))
  .replace(/\s+/g, ' ')
const deliver = body
  .slice(body.indexOf('## DELIVER'), body.indexOf('## The autonomy dial'))
  .replace(/\s+/g, ' ')

describe('medical-chronology-maintainer: an update goes into the delivered chronology', () => {
  it('submits the update block, not a standalone package of the named documents', () => {
    expect(update).toContain('**The update goes INTO the delivered chronology**')
    expect(update).toContain('`update: {into: "delivered_chronology", chronology_file_id: "<id>"}`')
    expect(update).toContain('`selection.include_file_ids`')
    expect(update).toMatch(
      /files ONE complete, updated chronology into the delivered chronology's own folder/
    )
    expect(update).toMatch(/Every delivered entry is carried word for word/)
    expect(update).toMatch(/before and after the incident/)
  })

  it('treats a delivered chronology with no coverage record as an update measured against its exhibits', () => {
    expect(update).toMatch(
      /but the matter holds a delivered chronology document\*\*[^.]*: it is still an update/
    )
    expect(update).toMatch(/read off the delivered chronology itself/)
    expect(update).not.toMatch(/\*\*do not submit\*\*/)
    expect(run).toMatch(/a chronology delivered before the record existed is still delivered/)
  })

  it('leaves the removal of the superseded document to the runner, after the proof, and nothing else', () => {
    expect(update).toMatch(
      /Only after the updated chronology is filed and proven on the matter does the runner remove/
    )
    expect(update).toMatch(/the runner removes nothing else/)
    expect(update).toContain("That removal is the runner's, never yours.")
    expect(body).toContain('Never `delete_file`.')
  })

  it('says in the reply that the chronology was updated', () => {
    expect(deliver).toContain('`Request: update into the delivered chronology.`')
    expect(deliver).toMatch(/the chronology was updated in its own folder/)
  })

  it('keeps the routing sentence: every ask on a matter with a delivery is an UPDATE', () => {
    expect(run).toContain(
      'On a matter that already holds a delivered chronology, every chronology request is an UPDATE, never a RUN.'
    )
    expect(update).toContain('**Name medical records only.**')
  })

  it('says the routing is enforced and how to self-correct on the refusal (2026-10-13)', () => {
    expect(run).toContain('**The broker and the runner enforce this, not only this text.**')
    expect(run).toContain('resubmit as the UPDATE below in the same turn')
    expect(run).toMatch(/could not be read is not a "no"/)
    expect(run).not.toMatch(/asks in so many words to rebuild it from scratch/)
  })
})
