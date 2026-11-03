import { describe, expect, it } from 'vitest'
import { alertHtml, alertWords, sourceWords } from './alert-words'
import { conditionIsQuiet } from './mail-policy'
import { UNPREFIXED_CONDITIONS } from './conditions'

describe('alertWords', () => {
  it('gives every condition that still emails its own words, not its machine label', () => {
    const emailing = UNPREFIXED_CONDITIONS.filter(
      (c) => !conditionIsQuiet(c) && c !== 'send_refused'
    )
    for (const condition of emailing) {
      const words = alertWords(condition)
      expect(words.happened, condition).not.toMatch(/\u2014|SEV1|ADR|heartbeat|gateway|HARD_STOP/i)
      expect(words.todo.length, condition).toBeGreaterThan(0)
      expect(words.subject, condition).toMatch(/^[a-z]/)
    }
  })

  it('names the connection, and says whose job an expiring sign-in is', () => {
    expect(alertWords('connector_down:smokeball').subject).toBe(
      'the smokeball connection is failing'
    )
    expect(alertWords('connector_token_expiring:smokeball').todo).toContain('Email the client')
  })

  it('a condition nobody classified still reads', () => {
    const words = alertWords('a_new_condition')
    expect(words.subject).toBe('a_new_condition')
    expect(words.todo).toContain('agent')
  })

  it('sink sources read in words; an unknown one still reads', () => {
    expect(sourceWords('audit_integrity').todo).toContain('your decision')
    expect(sourceWords('obligation').subject).toBe('obligation alert')
  })

  it('escapes machine text and keeps it below the fold', () => {
    const html = alertHtml('Something.', 'Nothing.', ['Detail: <img onerror=x>'])
    expect(html).toContain('&lt;img')
    expect(html.indexOf('WHAT YOU NEED TO DO')).toBeLessThan(html.indexOf('DETAILS'))
  })
})
