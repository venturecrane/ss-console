/**
 * Regression guard: the daily-needs-you-digest escalation pointer must not ask
 * the model to print a date from the escalation ledger.
 *
 * The ledger is the Operator's own record of what it sent. The overlay's
 * identifier gate certifies dates only from the firm's system of record, so a
 * "last raised <date>" copied from `escalation_state` is refused as
 * seat-sourced on every create_memo. On 2026-09-21 pilot-smokeball's digest
 * drafted its memo nine times; the seven carrying ledger dates were all
 * refused, and each refusal counts toward the seat's refusal-cascade brake.
 * Evidence: vfy_01M32EJR7393AFCVMJKKGQQXNH (replay of the real drafts through
 * the gate: the only unverified dates were the ledger's).
 *
 * @see operator/skills/daily-needs-you-digest/references/output-format.md
 */
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const FORMAT = readFileSync(
  'operator/skills/daily-needs-you-digest/references/output-format.md',
  'utf8'
)

function pointerTemplate(): string {
  const line = FORMAT.split('\n').find((l) =>
    l.includes('under active escalation by <owning skill>')
  )
  if (!line) throw new Error('escalation pointer template line not found in output-format.md')
  return line
}

describe('daily-needs-you-digest escalation pointer', () => {
  it('names the owning skill', () => {
    expect(pointerTemplate()).toContain('<owning skill>')
  })

  it('carries no date placeholder, because no ledger date can pass the identifier gate', () => {
    const line = pointerTemplate()
    expect(line).not.toMatch(/last raised/i)
    expect(line).not.toMatch(/<date>/)
  })
})

// The digest's own rules say "No em dashes anywhere" and the outbound gate's
// fabrication filter refuses a memo carrying one, yet every template line the
// model copies used them: on 2026-09-21 two digest memos were refused on the
// em dash alone. The skill must not model the character it forbids.
describe('daily-needs-you-digest skill text carries no em dash', () => {
  for (const file of ['SKILL.md', 'references/output-format.md', 'references/voice.md']) {
    it(file, () => {
      const text = readFileSync(`operator/skills/daily-needs-you-digest/${file}`, 'utf8')
      expect(text).not.toContain('—')
    })
  }
})
