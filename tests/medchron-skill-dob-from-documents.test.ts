/**
 * A client contact with no date of birth sends the Operator to the matter's
 * documents, not back to the firm (2026-10-27, Mazurenko 203508).
 *
 * A string gate on a skill body, for the reason the allowance gate gives: the
 * seat reads SKILL.md at turn time and acts on what it says. The chronology
 * was held asking a paralegal for two dates the police report, the intake
 * sheets and every record page header carried. Properties:
 *
 *   1. The date is read from a labeled DOB beside the client's name, and on a
 *      shared-surname joint matter only beside the client's FIRST name.
 *   2. The search is bounded (four documents per client, stop at two agreeing).
 *   3. Dates are quoted as written; scans are labeled, illegible or two-digit
 *      years are not used.
 *   4. Nothing is submitted on a document-read date until the requester
 *      confirms; a date the requester writes overrides the documents.
 *   5. A thread that already promised the dates gets them, not a question.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const body = readFileSync(
  resolve(import.meta.dirname, '../operator/skills/medical-chronology-maintainer/SKILL.md'),
  'utf8'
)
const run = body.slice(body.indexOf('## RUN'), body.indexOf('3. **Report the selection'))
const flat = run.replace(/\s+/g, ' ')

describe('medical-chronology-maintainer: a missing contact DOB is read from the documents', () => {
  it('no longer asks the requester for a DOB the contact lacks', () => {
    expect(flat).not.toContain('Any of these missing: ask')
    expect(flat).toContain('A contact with no date of birth is not a question for the firm yet')
  })

  it('reads a labeled DOB beside the client, first name on a shared surname', () => {
    expect(flat).toContain("Use `read_document` on the matter's own documents")
    expect(flat).toContain('LABELED as a date of birth')
    expect(flat).toContain("only beside that client's FIRST name")
  })

  it('bounds the search', () => {
    expect(flat).toContain(
      'Read at most four documents per client, and stop once two documents agree.'
    )
  })

  it('quotes dates as written and refuses illegible or two-digit years', () => {
    expect(flat).toContain('Write each date exactly as the document writes it')
    expect(flat).toContain('labeled a machine transcription')
    expect(flat).toContain('an `[illegible]` digit, or a two-digit year, is not used')
  })

  it('submits only after the requester confirms, and her own date wins', () => {
    expect(flat).toContain('Never submit on a date read from a document alone.')
    expect(flat).toContain('your own earlier email is not a source')
    expect(flat).toContain(
      'it overrides the documents and is submitted without a second confirmation'
    )
  })

  it('answers a promised thread with the dates, never a question', () => {
    expect(flat).toContain('A promise in the thread is a request.')
    expect(flat).toContain('Do not ask them for the dates.')
    expect(flat).toContain('Ask the requester only when no document carries a usable date')
  })
})
