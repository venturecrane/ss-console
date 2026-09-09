import { describe, it, expect } from 'vitest'
import {
  CLIENT_ACTIVITY_CATEGORIES,
  MAPPED_ACTIONS,
  SUPPRESSED_ACTIONS,
  mappedActionsForCategories,
  clientSummaryFor,
} from '../src/lib/portal/operator/activity-language'
import { AUDIT_ACTION_TYPES } from '../src/lib/portal/operator/audit'
import type { AuditEntry } from '../src/lib/portal/operator/audit'

function entry(action: string, extra: Partial<AuditEntry> = {}): AuditEntry {
  return {
    id: `e-${action}`,
    ts: '2026-07-07T00:00:00Z',
    actor: 'agent',
    actorRole: 'agent',
    action,
    target: null,
    decision: null,
    reason: null,
    skill: null,
    ...extra,
  }
}

describe('activity-language exhaustiveness (writer parity)', () => {
  it('every writer-side action is deliberately MAPPED or SUPPRESSED, never both', () => {
    const mapped = new Set(MAPPED_ACTIONS)
    for (const action of AUDIT_ACTION_TYPES) {
      const inMapped = mapped.has(action)
      const inSuppressed = SUPPRESSED_ACTIONS.has(action)
      expect(inMapped || inSuppressed, `${action} has no client-language decision`).toBe(true)
      expect(inMapped && inSuppressed, `${action} is in both sets`).toBe(false)
    }
  })

  it('every category action has authored language', () => {
    const mapped = new Set(MAPPED_ACTIONS)
    for (const category of CLIENT_ACTIVITY_CATEGORIES) {
      for (const action of category.actions) {
        expect(mapped.has(action), `${category.key}:${action} lacks language`).toBe(true)
      }
    }
  })

  it('the mapped vocabulary is a deliberate snapshot (additions require editing this test)', () => {
    expect([...MAPPED_ACTIONS].sort()).toEqual(
      [
        // ss#2536. The two halves of an act the firm was asked to confirm: one
        // row for the question, one for the act. Both are broker-written and
        // both must render, because an unmapped type shows the client nothing
        // and reads exactly like a suppression.
        'ACT_COMMITTED',
        'ACT_PROPOSED',
        'AGENT_RESUMED',
        // ss#2546 (the operations half). A routine, a schedule, a channel, a
        // memory setting, an autonomy level, an on/off: the firm asks, SMD
        // decides. All three render, because a request whose answer shows
        // nothing on the feed reads exactly like the answer never coming.
        'OPS_REQUEST_RECORDED',
        'OPS_REQUEST_RESOLVED',
        'OPS_REQUEST_LAPSED',
        // ss#2614 (routine 11). A chronology package's life on the seat, five
        // rows the broker writes on the runner's report. All render: the one
        // that matters most to the firm is the hold, and a hold that shows
        // nothing on the feed reads exactly like a package that never came.
        'MEDCHRON_JOB_SUBMITTED',
        'MEDCHRON_JOB_RUNNING',
        'MEDCHRON_JOB_HELD',
        'MEDCHRON_JOB_DELIVERED',
        'MEDCHRON_JOB_FAILED',
        'AGENT_STOPPED',
        'COMPLIANCE_PACKET_EXPORTED',
        // ss#2122: a Named Administrator pulled the per-matter audit record
        // from the portal. Console-plane, synthesized from portal_action_events.
        'COMPLIANCE_RECORD_EXPORTED',
        'CONFIG_CHANGE_REJECTED',
        'CONFIG_CHANGE_SUBMITTED',
        'CONFIRM_SEND_DISPATCHED',
        'CONFIRM_SEND_FAILED',
        'CONNECTOR_AUTH_EXPIRED',
        'CONNECTOR_AUTH_RESTORED',
        'CONNECTOR_BOUND',
        'CONNECTOR_RECONSENT_REQUESTED',
        'CONNECTOR_UNBOUND',
        'CORRECTION_PROPOSED',
        'DRAFT_APPROVED',
        // ss#2529 / ADR 0085. The conversational establishment path, in the
        // three beats a client can tell apart: a rule stated back and waiting,
        // a rule they confirmed, and the rule reaching the work. The last two
        // have been written to client ledgers since establishment shipped and
        // rendered as nothing for want of a decision here.
        'ESTABLISHMENT_RESULT',
        'ESTABLISHMENT_SUBMITTED',
        'RULE_PROPOSED',
        // ss#2546. The three beats that used to happen in silence when the
        // person who asked was not an administrator: their request reaching an
        // administrator, that administrator refusing it, and nobody answering
        // at all. Each renders, because a request whose outcome shows nothing
        // on the feed is the same as the outcome never being reported.
        'RULE_DECLINED',
        'RULE_LAPSED',
        'RULE_REQUEST_NOTIFIED',
        'DRAFT_CREATED',
        'DRAFT_EXPIRED',
        'DRAFT_REJECTED',
        'ENTITLEMENT_CHANGED',
        'ESCALATION_ACKNOWLEDGED',
        'ESCALATION_FIRED',
        'OUTPUT_SPEC_AUTHORED',
        'OUTPUT_SPEC_REJECTED',
        'PORTAL_LOGIN',
        'REPLY_FAILED',
        'REPLY_HELD',
        'REPLY_SENT',
        // #2498: a routine crossing the scheduled line. Deliberately separate
        // from SKILL_ENABLED/SKILL_DISABLED below — a skill being on is
        // permission, a routine being on is a schedule, and a seat can have
        // every skill enabled while initiating nothing.
        'ROUTINE_DISABLED',
        'ROUTINE_ENABLED',
        'SCOPE_CHANGED',
        'SKILL_DISABLED',
        'SKILL_ENABLED',
        'TEAM_INVITE_SENT',
        'TEAM_ROLE_GRANTED',
        'TEAM_ROLE_REVOKED',
        'TRUST_DEMOTED',
        'TRUST_PROMOTED',
      ].sort()
    )
  })
})

/**
 * ss#2320. Every reply and confirmed-send OUTCOME reaches the client, not just
 * the ones that went well. Before this, REPLY_SENT and REPLY_HELD rendered
 * while REPLY_FAILED rendered nothing, so a client watching their own feed saw
 * a filtered operation: a reply that never arrived left no trace of the attempt.
 * These assert the rendering, not the table — a membership check would pass on
 * an entry mapped to an empty string.
 */
describe('failure outcomes are visible to the client (ss#2320)', () => {
  const outcomes: ReadonlyArray<[string, string]> = [
    ['REPLY_SENT', 'Replied to a message'],
    ['REPLY_HELD', 'Held a reply for your review'],
    ['REPLY_FAILED', 'A reply could not be sent'],
    ['CONFIRM_SEND_DISPATCHED', 'Sent a confirmed message'],
    ['CONFIRM_SEND_FAILED', 'A confirmed message could not be sent'],
    ['CORRECTION_PROPOSED', 'Captured your correction'],
    // ss#2529. Asserted as RENDERING, not membership: the two ESTABLISHMENT
    // types were already reaching client ledgers and showing nothing, and a
    // membership check would pass on an entry mapped to an empty string, which
    // is the same silence in a different place.
    ['RULE_PROPOSED', 'Stated a rule back for confirmation'],
    ['ESTABLISHMENT_SUBMITTED', 'Committed a rule you confirmed'],
    ['ESTABLISHMENT_RESULT', 'Applied a rule to how work is written'],
    // ss#2536, and the first line has to read as a QUESTION: at ACT_PROPOSED
    // nothing has happened and somebody has been asked. A client scanning the
    // feed must be able to tell the asking from the doing.
    ['ACT_PROPOSED', 'Asked you to confirm something before doing it'],
    ['ACT_COMMITTED', 'Did what you confirmed'],
    // ss#2546, and the same reason the two ESTABLISHMENT lines are asserted as
    // rendering: these three exist BECAUSE the outcome used to be invisible, so
    // a mapping that rendered an empty string would reproduce the defect the
    // work was done to fix. The wording is from the reader's side - what became
    // of the request they made - and carries no timing promise.
    ['RULE_REQUEST_NOTIFIED', 'Asked an administrator to apply a rule'],
    ['RULE_DECLINED', 'An administrator declined a rule'],
    ['RULE_LAPSED', 'A rule request lapsed unanswered'],
    // ss#2614. The five beats of a chronology package, from the reader's side
    // and with no timing promise; the hold names that there is a reason to read.
    ['MEDCHRON_JOB_SUBMITTED', 'Started a medical chronology package for a matter'],
    ['MEDCHRON_JOB_RUNNING', 'Is building a medical chronology package'],
    ['MEDCHRON_JOB_HELD', 'Paused a medical chronology package and surfaced why'],
    ['MEDCHRON_JOB_DELIVERED', 'Filed a medical chronology package on the matter'],
    ['MEDCHRON_JOB_FAILED', 'Could not finish a medical chronology package'],
  ]

  for (const [action, copy] of outcomes) {
    it(`${action} renders for the client`, () => {
      const summary = clientSummaryFor(entry(action))
      expect(summary, `${action} rendered nothing on the client feed`).not.toBeNull()
      expect(summary).toBe(copy)
    })
  }

  it('no reply or confirmed-send outcome is silently withheld', () => {
    const withheld = outcomes
      .map(([action]) => action)
      .filter((action) => SUPPRESSED_ACTIONS.has(action))
    expect(withheld, 'an outcome the client cannot see').toEqual([])
  })

  it('the failure copy promises no retry the system does not perform', () => {
    // Pattern A: a sentence implying future business behaviour we have not
    // contracted. The system does not retry these sends.
    for (const [action] of outcomes) {
      const summary = clientSummaryFor(entry(action)) ?? ''
      expect(summary, `${action} implies a commitment`).not.toMatch(
        /\b(will|we'll|retry|retrying|shortly|follow up|try again)\b/i
      )
    }
  })
})

describe('clientSummaryFor', () => {
  // The batch mapper this block once exercised (`toClientActivity`) fed the
  // Home feeds that no page loaded; it was removed 2026-09-09. The language
  // table it read is live through this single-entry renderer, so the same
  // anti-fabrication guards hold here.
  it('renders nothing for unmapped and unknown actions', () => {
    const summaries = [
      entry('INVARIANT_VIOLATION'),
      entry('LLM_TURN_COMPLETED'),
      entry('HONCHO_CONCLUSION_DISMISSED'),
      entry('DRAFT_CREATED'),
    ].map(clientSummaryFor)
    expect(summaries.filter((s) => s !== null)).toHaveLength(1)
    expect(summaries[3]).toContain('draft')
  })

  it('never leaks raw action vocabulary into summaries', () => {
    for (const action of AUDIT_ACTION_TYPES) {
      const summary = clientSummaryFor(entry(action))
      if (summary === null) continue
      expect(summary).not.toMatch(/[A-Z]{2,}_[A-Z]/)
      expect(summary.toLowerCase()).not.toContain('invariant')
    }
  })

  it('interpolates real row data only where present', () => {
    expect(clientSummaryFor(entry('SKILL_ENABLED', { skill: 'inbox-triage' }))).toBe(
      'A skill was turned on: inbox-triage'
    )
    expect(clientSummaryFor(entry('SKILL_ENABLED'))).toBe('A skill was turned on')
    expect(clientSummaryFor(entry('ESCALATION_FIRED', { reason: 'Payment bounced' }))).toBe(
      'Payment bounced'
    )
  })

  it('the connections category owns CONNECTOR_BOUND', () => {
    expect(mappedActionsForCategories(['connections'])).toContain('CONNECTOR_BOUND')
    expect(clientSummaryFor(entry('CONNECTOR_BOUND', { target: 'Google Calendar' }))).toBe(
      'Connected Google Calendar'
    )
  })
})

describe('mappedActionsForCategories', () => {
  it('empty selection returns the full mapped vocabulary (the SQL-side default filter)', () => {
    expect(mappedActionsForCategories([]).sort()).toEqual([...MAPPED_ACTIONS].sort())
  })

  it('a category selection returns only its actions', () => {
    expect(mappedActionsForCategories(['escalations']).sort()).toEqual([
      'ESCALATION_ACKNOWLEDGED',
      'ESCALATION_FIRED',
    ])
  })
})
