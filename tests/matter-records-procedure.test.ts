import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/**
 * The router's records procedure (ADR 0102) answers every requested record
 * truthfully and gives the requester everything findable (2026-11-02).
 *
 * On a live A&P run the Operator's reply to an attorney said "the other three
 * could not be sent" without ever trying a page cut, guessed a record was
 * "only inside a chronology exhibit" without opening it, left out the folder
 * and file name of what it did not send, and raised none of the questions the
 * request implied (a cited visit date with no record; a "[confirm]" item whose
 * note was a different specialty than the stated purpose). Each rule below is
 * one of those failures, pinned in the text the model reads.
 */

const SKILL = 'operator/skills/matter-inbox-router/SKILL.md'
const HEADING = '## Requests for matter records'

function section(): string {
  const text = readFileSync(SKILL, 'utf8')
  const start = text.indexOf(HEADING)
  expect(start, `${SKILL} has no "${HEADING}" section`).toBeGreaterThan(-1)
  const end = text.indexOf('\n## ', start + HEADING.length)
  return text.slice(start, end === -1 ? undefined : end)
}

describe('matter records procedure', () => {
  const body = section()

  it('finds the pages, with the page-finder when present, and says so when absent', () => {
    expect(body).toMatch(/`find_pages_in_file\(matter_id, file_id, what=/)
    expect(body).toMatch(
      /`find_pages_in_file` is not in your tool list, say in the reply that you could not search inside/
    )
    // An incomplete index is never read as "not in the file".
    expect(body).toMatch(
      /When `complete` is false .*call it again .*before you say a record is not in that file/
    )
    // Stage with the returned ranges; cite their evidence to the requester.
    expect(body).toMatch(/Stage with the returned ranges/)
    expect(body).toMatch(/\*\*Attached:\*\*.*evidence `find_pages_in_file` gave/)
    expect(body).toMatch(/Never guess pages/)
  })

  it('never reports a file unsendable before a page cut was tried', () => {
    const cutRule = body.indexOf(
      'Never write that a file could not be sent unless `stage_matter_files` refused it with a page cut too'
    )
    expect(cutRule).toBeGreaterThan(-1)
    // The page-finding step comes before the staging step that carries the rule.
    expect(body.indexOf('Find the pages before you give up')).toBeLessThan(cutRule)
    expect(body).toMatch(/stage it again with the pages they need/)
  })

  it('never infers a record from a file name or an unopened file', () => {
    expect(body).toMatch(
      /never decide a record is "only inside" another file unless you opened that file/
    )
    expect(body).toMatch(/what you checked .*never a guess from a file name/)
  })

  it('names folder, exact file name and the concrete reason for anything not sent', () => {
    expect(body).toMatch(
      /\*\*Not sent:\*\* each by its folder and exact file name, with the concrete reason/
    )
  })

  it('answers every requested item and raises the open questions the request implies', () => {
    expect(body).toMatch(/Every item gets an answer in the reply/)
    expect(body).toMatch(/\*\*Open questions\*\*/)
    expect(body).toMatch(/a date it cites with no record found for that date/)
    expect(body).toMatch(/"\[confirm\]"/)
    expect(body).toMatch(/provider or specialty does not match the purpose/)
  })

  it('sends more than three files as follow-up replies in the same thread', () => {
    expect(body).toMatch(/more than three files are staged, .*further replies in the same thread/)
  })

  it("the cut ceiling it states is the connector's", () => {
    const connector = readFileSync(
      'operator/connectors/smokeball/smokeball_connector/matter_files.py',
      'utf8'
    )
    const cap = connector.match(/^MAX_EXTRACT_PAGES = (\d+)$/m)?.[1]
    expect(cap).toBeDefined()
    expect(body).toContain(`at most ${cap} per cut`)
  })
})
