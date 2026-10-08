/**
 * A chronology's completion wake replies once, to the requester, through the
 * bound reply, and never reaches the attorney by email (2026-10-07).
 *
 * A string gate on a skill body, for the reason the allowance gate gives: the
 * seat reads SKILL.md at turn time and acts on what it says. Three properties:
 *
 *   1. DELIVER binds the reply to the JOB (`reply_bind` with only `job_id`),
 *      and a refused bind sends nothing to anyone. The overlay refuses every
 *      send in a "chronology job <id>" wake except the bound reply, so a skill
 *      that teaches any other route teaches a send that cannot land.
 *   2. A `failed` job is SMD's: nothing is sent to anyone.
 *   3. An audit-gate hold (a citation the record does not fully support,
 *      graded PARTIAL or UNSUPPORTED) is the runner's to weaken or drop and
 *      SMD's to clear, never framed as an attorney determination, and the
 *      responsible attorney is reached only by the review task on a delivered
 *      package, never by email.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const body = readFileSync(
  resolve(import.meta.dirname, '../operator/skills/medical-chronology-maintainer/SKILL.md'),
  'utf8'
)
const deliver = body.slice(body.indexOf('## DELIVER'), body.indexOf('## The autonomy dial'))
const flat = deliver.replace(/\s+/g, ' ')

describe('medical-chronology-maintainer: DELIVER replies through the bound reply', () => {
  it('binds the reply to the job id alone, never the email', () => {
    expect(flat).toContain('`reply_bind` and ONLY `job_id` = the id in the wake')
    expect(flat).toContain('Never pass `internet_message_id` or `graph_message_id`')
  })

  it('sends nothing to anyone when the bind is refused', () => {
    expect(flat).toContain('If the bind is refused, send NOTHING to anyone.')
    expect(flat).toMatch(/Not the responsible attorney, not the matter's staff/)
  })

  it('sends nothing on a failed job and reads the job once', () => {
    expect(flat).toMatch(/outcome is `failed`[^]*Send NOTHING to anyone[^]*`medchron_job_status`/)
  })

  it('replies once with create_draft to the bound sender only, then stops', () => {
    expect(flat).toContain('`create_draft` addressed to the bound sender only')
    expect(flat).toMatch(/no email to the responsible attorney/)
  })
})

describe('medical-chronology-maintainer: a PARTIAL is never the attorney', () => {
  it('names the audit-gate hold as SMD work, never an attorney determination', () => {
    expect(flat).toContain('`audit coverage:`')
    expect(flat).toMatch(/never the firm's to decide/)
    expect(flat).toMatch(/never framed as one/)
  })

  it('never asks anyone to determine, review or confirm a PARTIAL, nor emails the attorney', () => {
    const all = body.replace(/\s+/g, ' ')
    expect(all).not.toMatch(/attorney (to )?(determin|review|confirm)[^.]*PARTIAL/i)
    expect(all).not.toMatch(/PARTIAL[^.]*attorney (to )?(determin|review|confirm)/i)
    // Direct "email/send/surface ... to the attorney" instructions, excluding
    // the skill's own prohibitions ("no email to the responsible attorney").
    expect(all).not.toMatch(
      /(?<!no )(?<!never )\b(email|send|surface|notify)( it| this| the hold| a hold)? to the (responsible )?attorney/i
    )
    expect(all).not.toContain('attorney-confirm note')
  })
})

describe('medical-chronology-maintainer: a delivered package never relays the job reason', () => {
  // 2026-10-07: a job held at the audit gate was resumed and delivered; its row
  // still carried the hold reason, and the DELIVER reply told the requester two
  // entries needed attorney review that the delivered document did not have.
  // The ledger now clears a stale reason on delivery; the skill must not lean
  // on that alone, because a row written before the fix still carries one.
  it('reads the reason only on a hold, never on a delivery', () => {
    expect(flat).toContain(
      '**On `delivered`, the `reason` field is not part of the delivery and is never relayed**'
    )
    expect(flat).toContain('The `reason` is read and acted on only when the state is `held`.')
  })

  it('the delivered reply carries nothing from the reason', () => {
    const delivered = flat.slice(flat.indexOf('- **delivered**:'), flat.indexOf('- **held**:'))
    expect(delivered).toContain("nothing from the job's `reason`")
    expect(delivered).toMatch(
      /never say an entry is flagged, partial, or needs review unless the delivery itself reports it/
    )
  })
})
