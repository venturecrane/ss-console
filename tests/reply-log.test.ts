import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

/**
 * Reply-log endpoint — structural tests (issue #464).
 *
 * Verifies the API shape and wiring without booting D1. Behavioral
 * verification (context row insertion) lives in integration tests.
 */

const endpoint = () =>
  readFileSync(resolve('src/pages/api/admin/entities/[id]/reply-log.ts'), 'utf-8')
const detailPage = () => readFileSync(resolve('src/pages/admin/entities/[id].astro'), 'utf-8')
const listPage = () => readFileSync(resolve('src/pages/admin/entities/index.astro'), 'utf-8')
const dialog = () => readFileSync(resolve('src/components/admin/LogReplyDialog.astro'), 'utf-8')

describe('reply-log endpoint: allowed values', () => {
  it('exports the four sentiment values from the issue', () => {
    const code = endpoint()
    expect(code).toContain("'interested'")
    expect(code).toContain("'declined'")
    expect(code).toContain("'out_of_office'")
    expect(code).toContain("'other'")
  })

  it('exports the four next-action values from the issue', () => {
    const code = endpoint()
    expect(code).toContain("'book_meeting'")
    expect(code).toContain("'retry_later'")
    expect(code).toContain("'mark_lost'")
    expect(code).toContain("'continue_conversation'")
  })
})

describe('reply-log endpoint: context entry shape', () => {
  it('writes a context entry via appendContext', () => {
    expect(endpoint()).toContain('appendContext')
  })

  it("uses type 'note' as the issue specifies", () => {
    expect(endpoint()).toContain("type: 'note'")
  })

  it("uses source 'reply_log' as the issue specifies", () => {
    expect(endpoint()).toContain("source: 'reply_log'")
  })

  it('stores sentiment and next_action in metadata', () => {
    const code = endpoint()
    expect(code).toContain('sentiment')
    expect(code).toContain('next_action')
    expect(code).toMatch(/metadata:\s*\{/)
  })

  it('does not transition entity stage (AC: admin picks next action manually)', () => {
    const code = endpoint()
    // The endpoint never calls updateEntityStage or emits a stage_change context entry
    expect(code).not.toMatch(/updateEntityStage|stage_change|updateStage/)
  })
})

describe('reply-log endpoint: auth and validation', () => {
  it('rejects non-admin sessions', () => {
    const code = endpoint()
    expect(code).toContain("session.role !== 'admin'")
  })

  it('validates sentiment against allowed list', () => {
    expect(endpoint()).toContain('REPLY_SENTIMENTS.includes')
  })

  it('validates next_action against allowed list', () => {
    expect(endpoint()).toContain('REPLY_NEXT_ACTIONS.includes')
  })

  it('verifies entity exists before writing context', () => {
    expect(endpoint()).toContain('getEntity')
  })
})

describe('reply-log UI: dialog component', () => {
  it('renders a native <dialog> so it works without a framework', () => {
    expect(dialog()).toMatch(/<dialog[\s>]/)
  })

  it('POSTs to the reply-log endpoint', () => {
    expect(dialog()).toContain('/reply-log')
  })

  it('has a sentiment select with all four options', () => {
    const code = dialog()
    expect(code).toContain('name="sentiment"')
    expect(code).toContain('value="interested"')
    expect(code).toContain('value="declined"')
    expect(code).toContain('value="out_of_office"')
    expect(code).toContain('value="other"')
  })

  it('has a next_action select with all four options', () => {
    const code = dialog()
    expect(code).toContain('name="next_action"')
    expect(code).toContain('value="book_meeting"')
    expect(code).toContain('value="retry_later"')
    expect(code).toContain('value="mark_lost"')
    expect(code).toContain('value="continue_conversation"')
  })

  it('has a notes textarea', () => {
    expect(dialog()).toContain('name="notes"')
  })
})

describe('reply-log UI: entity detail surfaces reply entries with distinct badge', () => {
  it('imports the LogReplyDialog component', () => {
    expect(detailPage()).toContain('LogReplyDialog')
  })

  it('exposes a Log reply trigger', () => {
    expect(detailPage()).toContain('data-open-reply-dialog')
  })

  it('renders a distinct Reply badge for reply_log entries', () => {
    const code = detailPage()
    expect(code).toContain("entry.source === 'reply_log'")
    expect(code).toContain('isReplyLog')
  })
})

describe('reply-log UI: entities list (prospect rows)', () => {
  it('adds a Log reply button for prospect-stage rows', () => {
    const code = listPage()
    expect(code).toContain("e.stage === 'prospect'")
    expect(code).toContain('data-open-reply-dialog')
  })

  it('imports the dialog component', () => {
    expect(listPage()).toContain('LogReplyDialog')
  })
})
