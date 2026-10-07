/**
 * A held chronology goes to the requester by reply, never to the attorney
 * (2026-10-07).
 *
 * A string gate on a skill body, for the reason the allowance gate gives: the
 * seat reads SKILL.md at turn time and acts on what it says. Two properties:
 *
 *   1. An audit-gate hold (a citation the record does not fully support,
 *      graded PARTIAL or UNSUPPORTED) is the runner's to weaken or drop and
 *      SMD's to clear. A skill that frames it as an attorney determination
 *      hands the firm work the product exists to do.
 *   2. Holds go to the requester only, by reply. The responsible attorney is
 *      reached through a review task on a delivered package and never by email.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const body = readFileSync(
  resolve(import.meta.dirname, '../operator/skills/medical-chronology-maintainer/SKILL.md'),
  'utf8'
)
const held = body.slice(body.indexOf('4. **Held:**'), body.indexOf('5. **No requester**'))

describe('medical-chronology-maintainer: where a hold goes', () => {
  it('sends a hold to the requester only, by reply', () => {
    expect(held).toContain('the requester only, by\n   reply')
    expect(held).toMatch(/Never email the responsible attorney/)
  })

  it('names the audit-gate hold as SMD work, never an attorney determination', () => {
    expect(held).toContain('`audit coverage:`')
    expect(held).toMatch(/never the firm's to decide/)
    expect(held).toMatch(/never framed as\s+one/)
  })

  it('never asks the attorney to determine, review or confirm a PARTIAL anywhere', () => {
    expect(body).not.toMatch(/attorney (to )?(determin|review|confirm)[^.]*PARTIAL/i)
    expect(body).not.toMatch(/PARTIAL[^.]*attorney (to )?(determin|review|confirm)/i)
    expect(body).not.toMatch(/(email|send)[^.\n]*to the (responsible )?attorney/i)
  })
})
