import { describe, it, expect } from 'vitest'
import { clientSummaryFor } from '../src/lib/portal/operator/activity-language'
import type { AuditEntry } from '../src/lib/portal/operator/audit'

/**
 * A reply that went out and then faulted before its record finished is written
 * as a REPLY_SENT stamped `trace_incomplete` (the overlay's
 * plugins/hermes-smd-reply/relay_sent.py), never a REPLY_FAILED: the email is
 * in the firm's inbox. The client feed must read it exactly as any sent reply,
 * whatever the row carries, and never as "A reply could not be sent".
 */
function sentRow(extra: Partial<AuditEntry>): AuditEntry {
  return {
    id: 'e-trace',
    ts: '2026-11-15T00:00:00Z',
    actor: 'agent',
    actorRole: 'agent',
    action: 'REPLY_SENT',
    target: null,
    decision: null,
    reason: null,
    skill: null,
    ...extra,
  }
}

describe('a sent reply whose record broke, in the client feed', () => {
  it('reads as the ordinary sent wording, with or without a fault on the row', () => {
    expect(clientSummaryFor(sentRow({}))).toBe('Replied to a message')
    expect(clientSummaryFor(sentRow({ reason: 'RuntimeError', skill: 'reply' }))).toBe(
      'Replied to a message'
    )
  })
  it('is never the failed-reply wording', () => {
    expect(clientSummaryFor(sentRow({ reason: 'RuntimeError' }))).not.toMatch(/could not/)
  })
})
