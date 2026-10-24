/**
 * "My cases" is the asker's own files, never the whole firm (2026-10-24).
 *
 * On 2026-10-23 an attorney asked the litigation lane for the service status of
 * his own litigated files and the request ran firm-wide: the skill read "my
 * cases" as every case, and the tool had no scope for it. The tool now takes
 * `mine`, the broker fills the verified sender, and the job finds that person in
 * the firm's staff list by email. A string gate on the skill body, because the
 * seat reads SKILL.md at turn time and acts on what it says.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const flat = (s: string) => s.replace(/\s+/g, ' ')
const SKILL = flat(
  readFileSync(
    resolve(import.meta.dirname, '../operator/skills/litigation-status/SKILL.md'),
    'utf8'
  )
)
const TOOL = flat(
  readFileSync(
    resolve(
      import.meta.dirname,
      '../operator/hermes-smd-overlay/plugins/hermes-smd-medchron/litigation.py'
    ),
    'utf8'
  )
)

describe('litigation-status: whose files a request names', () => {
  it('maps "my cases" and "tagged on" to mine, never to every case', () => {
    expect(SKILL).toContain('"every litigated file I\'m tagged on"')
    expect(SKILL).toContain('Pass `mine: true`')
    expect(SKILL).toContain('never for "my" cases')
    expect(SKILL).toContain('You never name who "me" is')
  })

  it('says one person is every matter they hold any staff role on', () => {
    expect(SKILL).toContain('any staff role on (responsible, assisting or originating)')
    expect(SKILL).toContain('not only those where she is the person assisting')
  })

  it('names the two-email sequence and submits from the email that names the columns', () => {
    expect(SKILL).toContain('A request can arrive in two emails.')
    expect(SKILL).toContain('The job reads only the words of the email whose turn submits it.')
    expect(SKILL).toContain("submit from that email's turn")
    expect(SKILL).toContain('Never submit the same ask twice.')
  })

  it('the tool offers mine and keeps "my cases" off all', () => {
    expect(TOOL).toContain('"mine": {')
    expect(TOOL).toContain('Never "my cases": that is mine.')
  })
})
