import { describe, it, expect } from 'vitest'
import {
  authorizationOf,
  describeAuthorization,
  describeObject,
  loadObjectAuditRecord,
  objectAuditCsvFilename,
  OBJECT_AUDIT_CSV_COLUMNS,
  parseObjectAuditRows,
  scopeToRef,
  toObjectAuditCsv,
  type ObjectAuditRow,
} from '../src/lib/portal/operator/object-audit-record'

/**
 * The per-reference audit record (ss#2122).
 *
 * The properties under test are the ones a compliance reader is entitled to
 * rely on, not the ones easiest to assert:
 *
 *   1. The authorization column is DERIVED, never invented. A row with no
 *      authorizing party reports `unattributed`, and the copy says so.
 *   2. An empty answer is never a clean zero. When a reference matches nothing
 *      but unattributed rows exist in the same window, the count rides along.
 *   3. A seam failure is `unavailable`, not an empty record. This is the
 *      difference between "we could not read" and "nothing happened", and
 *      conflating them in a legal record is the defect the issue opened on.
 *   4. The CSV is spreadsheet-safe. A compliance CSV is opened in Excel by
 *      definition, so a leading `=` must not become a formula.
 */

function row(over: Partial<ObjectAuditRow> = {}): ObjectAuditRow {
  return {
    id: '01J0000000000000000000000A',
    ts: '2026-08-01T10:00:00.000Z',
    actionType: 'TOOL_CALL_COMPLETED',
    actor: 'agent',
    actorRole: 'agent',
    skillName: null,
    matterRef: 'M-1',
    trustCeiling: 'draft_for_review',
    inputDigest: null,
    outputDigest: null,
    diffDigest: null,
    prevHash: null,
    rowHash: null,
    routine: null,
    sessionId: null,
    senderKey: null,
    vendorMessageId: null,
    objectId: null,
    objectKind: null,
    writtenBodySha256: null,
    bodyDigestAuthored: null,
    bodyDigestAuthoredHtml: null,
    ...over,
  }
}

describe('parseObjectAuditRows', () => {
  it('maps the overlay audit_export wire shape onto the typed row', () => {
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'REPLY_SENT',
          actor: 'christa@example.com',
          actor_role: 'principal',
          skill_name: 'matter-status-responder',
          matter_ref: 'M-7',
          trust_ceiling: 'autonomous',
          input_digest: 'sha256:aa',
          output_digest: 'sha256:bb',
          diff_digest: null,
          prev_hash: 'p0',
          row_hash: 'r0',
          metadata: JSON.stringify({ routine: 'matter-status-digest', cron_job_id: 'j1' }),
        },
      ],
      cursor: null,
    })
    expect(parsed).toHaveLength(1)
    expect(parsed[0]).toMatchObject({
      id: 'A',
      actionType: 'REPLY_SENT',
      actor: 'christa@example.com',
      actorRole: 'principal',
      matterRef: 'M-7',
      trustCeiling: 'autonomous',
      rowHash: 'r0',
      routine: 'matter-status-digest',
    })
  })

  it('drops a row missing any required field rather than rendering a partial line', () => {
    const parsed = parseObjectAuditRows({
      entries: [
        { ts: '2026-08-01T00:00:00.000Z', action_type: 'X', actor: 'agent' }, // no id
        { id: 'B', action_type: 'X', actor: 'agent' }, // no ts
        { id: 'C', ts: '2026-08-01T00:00:00.000Z', actor: 'agent' }, // no action_type
        'not an object',
      ],
    })
    expect(parsed).toEqual([])
  })

  it('survives an unparseable metadata blob without losing the row', () => {
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'X',
          actor: 'agent',
          metadata: '{not json',
        },
      ],
    })
    expect(parsed).toHaveLength(1)
    expect(parsed[0].routine).toBeNull()
  })

  /**
   * The joins (ss#2497). Measured on the live A&P ledger 2026-08-21
   * (vfy_01M0H8DR6JAPYVHFMNJZXQZ517), every one of these was absent: the record
   * could not name the person behind an action, the message it answered, or the
   * object it touched, so a Named Administrator reading this page saw a list of
   * verbs. The parser is where they become readable.
   */
  it('lifts the session, the sender key, the vendor message id and the object', () => {
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'TOOL_CALL_COMPLETED',
          actor: 'agent',
          metadata: JSON.stringify({
            session_id: '20260820_195837_68d654ce',
            sender_key: 'a'.repeat(64),
            vendor_message_id: 'am-msg-77',
            memo_id: 'memo-9',
            written_body_sha256: 'b'.repeat(64),
          }),
        },
      ],
    })
    expect(parsed[0]).toMatchObject({
      sessionId: '20260820_195837_68d654ce',
      senderKey: 'a'.repeat(64),
      vendorMessageId: 'am-msg-77',
      objectId: 'memo-9',
      objectKind: 'memo',
      writtenBodySha256: 'b'.repeat(64),
    })
  })

  it('reports the joins as absent on a row written before the writers emitted them', () => {
    // A real state of the ledger, not a parse failure: the overlay gained these
    // fields after seats had begun writing, exactly as matter_ref and
    // trust_ceiling did. Rows written before them carry NULL forever, and
    // naming that state is the whole point.
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'REPLY_SENT',
          actor: 'agent',
          metadata: JSON.stringify({ reply_channel: true }),
        },
      ],
    })
    expect(parsed[0]).toMatchObject({
      sessionId: null,
      senderKey: null,
      vendorMessageId: null,
      objectId: null,
      objectKind: null,
      writtenBodySha256: null,
      bodyDigestAuthored: null,
      bodyDigestAuthoredHtml: null,
    })
    expect(describeObject(parsed[0])).toBe('Not recorded')
  })

  it('names one object per row, in a stable order, and never folds in a listing', () => {
    // A file LISTING carries document_ids (plural) and names many objects. The
    // single-object column would have to pick one, which reads as "this is the
    // only one it touched" -- the same failure the matter column avoids by
    // staying null.
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'TOOL_CALL_COMPLETED',
          actor: 'agent',
          metadata: JSON.stringify({ document_ids: ['f1', 'f2'] }),
        },
      ],
    })
    expect(parsed[0].objectId).toBeNull()
    expect(parsed[0].objectKind).toBeNull()
  })
})

describe('describeObject', () => {
  it('names the object and a checkable prefix of the content digest', () => {
    const line = describeObject(
      row({ objectId: 'memo-9', objectKind: 'memo', writtenBodySha256: 'c'.repeat(64) })
    )
    expect(line).toBe('memo memo-9, content cccccccccccc')
    // House style: no em dashes reach a client-facing surface.
    expect(line).not.toContain('\u2014')
  })

  it('falls back to the vendor message when there is no object', () => {
    expect(describeObject(row({ vendorMessageId: 'am-msg-77' }))).toBe('message am-msg-77')
  })

  it('says Not recorded rather than describing a row it cannot describe', () => {
    expect(describeObject(row())).toBe('Not recorded')
  })
})

describe('authorizationOf', () => {
  it('names the scheduled routine when one opened the session', () => {
    const basis = authorizationOf(row({ routine: 'medical-records-chaser' }))
    expect(basis).toEqual({
      basis: 'routine',
      routine: 'medical-records-chaser',
      ceiling: 'draft_for_review',
    })
    expect(describeAuthorization(basis)).toBe(
      'Scheduled routine "medical-records-chaser" (permission level: draft_for_review)'
    )
  })

  it('names the person when a human actor and role are recorded', () => {
    const basis = authorizationOf(
      row({ actor: 'christa@example.com', actorRole: 'principal', trustCeiling: 'autonomous' })
    )
    expect(basis).toEqual({
      basis: 'person',
      person: 'christa@example.com',
      role: 'principal',
      ceiling: 'autonomous',
    })
  })

  it('reports unattributed rather than guessing when the writer named nobody', () => {
    // This is the state of every row written before the writer carried an
    // actor: actor='agent', actor_role='agent', no routine. It must not be
    // rendered as if the agent authorized itself.
    const basis = authorizationOf(row({ trustCeiling: null }))
    expect(basis).toEqual({ basis: 'unattributed', ceiling: null })
    expect(describeAuthorization(basis)).toBe('Not recorded (no permission level recorded)')
  })

  it('a routine wins over a bare agent actor', () => {
    expect(authorizationOf(row({ routine: 'ar-chaser', actor: 'agent' })).basis).toBe('routine')
  })
})

describe('scopeToRef', () => {
  const rows = [
    row({ id: '1', ts: '2026-07-01T00:00:00.000Z', matterRef: 'M-1' }),
    row({ id: '2', ts: '2026-08-01T00:00:00.000Z', matterRef: 'M-1' }),
    row({ id: '3', ts: '2026-08-02T00:00:00.000Z', matterRef: 'M-2' }),
    row({ id: '4', ts: '2026-08-03T00:00:00.000Z', matterRef: null }),
    row({ id: '5', ts: '2026-08-04T00:00:00.000Z', matterRef: null }),
  ]

  it('scopes to the named reference inside the window', () => {
    const rec = scopeToRef(rows, { ref: 'M-1', from: '2026-07-15', to: null })
    expect(rec.rows.map((r) => r.id)).toEqual(['2'])
  })

  it('a date-only "to" bound covers the whole day', () => {
    const rec = scopeToRef(rows, { ref: 'M-2', from: null, to: '2026-08-02' })
    expect(rec.rows.map((r) => r.id)).toEqual(['3'])
  })

  it('counts unattributed rows in the window so an empty answer is never silent', () => {
    const rec = scopeToRef(rows, { ref: 'M-99', from: null, to: null })
    expect(rec.rows).toEqual([])
    expect(rec.unattributedInPeriod).toBe(2)
    expect(rec.scannedInPeriod).toBe(5)
  })
})

describe('toObjectAuditCsv', () => {
  it('emits the stable column order with a derived authorization column', () => {
    const rec = scopeToRef([row({ routine: 'ar-chaser' })], {
      ref: 'M-1',
      from: null,
      to: null,
    })
    const csv = toObjectAuditCsv(rec)
    const lines = csv.trimEnd().split('\n')
    expect(lines[0]).toBe(OBJECT_AUDIT_CSV_COLUMNS.join(','))
    expect(lines[1]).toContain('Scheduled routine ""ar-chaser""')
    expect(lines[1]).toContain(',routine,')
  })

  it('neutralizes a spreadsheet formula prefix', () => {
    // A matter reference is an opaque handle from a source system. If one ever
    // begins with '=', Excel would execute it on open.
    const rec = scopeToRef([row({ matterRef: '=1+1' })], {
      ref: '=1+1',
      from: null,
      to: null,
    })
    const csv = toObjectAuditCsv(rec)
    expect(csv).toContain(`"'=1+1"`)
    expect(csv).not.toMatch(/,=1\+1,/)
  })

  it('quotes and escapes embedded quotes and commas', () => {
    const rec = scopeToRef([row({ skillName: 'a,"b"' })], {
      ref: 'M-1',
      from: null,
      to: null,
    })
    expect(toObjectAuditCsv(rec)).toContain('"a,""b"""')
  })

  it('exports the joins, appended so an existing column never shifts', () => {
    // ss#2497. A firm diffing this month's export against last month's must see
    // new columns arrive at the END; interleaving them would move every existing
    // column one place right and make the diff unreadable.
    const before = [
      'id',
      'ts',
      'action_type',
      'matter_ref',
      'authorized_by',
      'authorization_basis',
      'trust_ceiling',
      'actor',
      'actor_role',
      'routine',
      'skill_name',
      'input_digest',
      'output_digest',
      'diff_digest',
      'prev_hash',
      'row_hash',
    ]
    expect(OBJECT_AUDIT_CSV_COLUMNS.slice(0, before.length)).toEqual(before)
    expect(OBJECT_AUDIT_CSV_COLUMNS.slice(before.length)).toEqual([
      'session_id',
      'sender_key',
      'vendor_message_id',
      'object_kind',
      'object_id',
      'written_body_sha256',
      'body_digest_authored',
      'body_digest_authored_html',
    ])

    const rec = scopeToRef(
      [
        row({
          sessionId: 'sess-1',
          senderKey: 'd'.repeat(64),
          vendorMessageId: 'am-msg-77',
          objectId: 'memo-9',
          objectKind: 'memo',
          writtenBodySha256: 'e'.repeat(64),
        }),
      ],
      { ref: 'M-1', from: null, to: null }
    )
    const dataLine = toObjectAuditCsv(rec).trimEnd().split('\n')[1]
    const cells = dataLine.split(',')
    expect(cells).toHaveLength(OBJECT_AUDIT_CSV_COLUMNS.length)
    expect(cells.slice(-8, -2)).toEqual([
      'sess-1',
      'd'.repeat(64),
      'am-msg-77',
      'memo',
      'memo-9',
      'e'.repeat(64),
    ])
  })

  it('exports the digests a firm can recompute from its own copy of the email', () => {
    // ss#2501. These two are what make a send row checkable by someone who does
    // not trust us: they cover exactly the bytes handed to the mail system,
    // where `body_digest` folds in a subject the wire never carries. Appended
    // at the very end for the same diff-stability reason as the ss#2497 joins.
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'REPLY_SENT',
          actor: 'agent',
          matter_ref: 'M-1',
          metadata: JSON.stringify({
            body_digest: '9'.repeat(64),
            body_digest_authored: 'a'.repeat(64),
            body_digest_authored_html: 'b'.repeat(64),
          }),
        },
      ],
    })
    expect(parsed[0]).toMatchObject({
      bodyDigestAuthored: 'a'.repeat(64),
      bodyDigestAuthoredHtml: 'b'.repeat(64),
    })
    const rec = scopeToRef(parsed, { ref: 'M-1', from: null, to: null })
    const cells = toObjectAuditCsv(rec).trimEnd().split('\n')[1].split(',')
    expect(cells.slice(-2)).toEqual(['a'.repeat(64), 'b'.repeat(64)])
    // The internal scan digest is deliberately NOT a column: publishing a hash
    // nobody outside can reproduce invites an auditor to chase it.
    expect(OBJECT_AUDIT_CSV_COLUMNS).not.toContain('body_digest')
  })

  it('leaves the digest columns empty for a reply sent with no html body', () => {
    // Absent, not the sha256 of the empty string. A reader must be able to tell
    // "no html was sent" from "html whose content was empty".
    const parsed = parseObjectAuditRows({
      entries: [
        {
          id: 'A',
          ts: '2026-08-01T00:00:00.000Z',
          action_type: 'REPLY_SENT',
          actor: 'agent',
          matter_ref: 'M-1',
          metadata: JSON.stringify({ body_digest_authored: 'c'.repeat(64) }),
        },
      ],
    })
    expect(parsed[0].bodyDigestAuthoredHtml).toBeNull()
    const rec = scopeToRef(parsed, { ref: 'M-1', from: null, to: null })
    const cells = toObjectAuditCsv(rec).trimEnd().split('\n')[1].split(',')
    expect(cells.slice(-2)).toEqual(['c'.repeat(64), ''])
  })

  it('never exports a raw email address', () => {
    // The issue's non-goal, enforced where it matters: this file leaves the
    // Machine. The sender is a KEY, and there is no column that could carry an
    // address for it to hide in.
    const rec = scopeToRef([row({ senderKey: 'f'.repeat(64) })], {
      ref: 'M-1',
      from: null,
      to: null,
    })
    expect(toObjectAuditCsv(rec)).not.toContain('@')
  })
})

describe('objectAuditCsvFilename', () => {
  it('is filesystem safe for an arbitrary source-system handle', () => {
    expect(objectAuditCsvFilename('ashton-price', 'ABC/123 x')).toBe(
      'audit-record-ashton-price-ABC_123_x.csv'
    )
  })
})

describe('loadObjectAuditRecord', () => {
  const query = { ref: 'M-1', from: null, to: null }
  const actor = { actor: 'u1', actorRole: 'principal' }
  const db = {} as never

  it('reports not_configured instead of an empty record when the seam is unwired', async () => {
    const rec = await loadObjectAuditRecord(
      { db, env: {}, actorUserId: 'u1' },
      'slug',
      actor,
      query
    )
    expect(rec.unavailable).toBe('not_configured')
    expect(rec.rows).toEqual([])
  })

  it('reports unreachable instead of a clean zero when the Machine does not answer', async () => {
    // The distinction under test: a fail-closed empty here would be
    // indistinguishable from "the Operator did nothing on this matter".
    const rec = await loadObjectAuditRecord(
      {
        db: {
          prepare: () => ({ bind: () => ({ run: async () => {} }) }),
        } as never,
        env: {
          OPERATOR_RUNTIME_READ_URL: 'https://{app}.example.invalid',
          OPERATOR_RUNTIME_READ_SECRET: 'x'.repeat(40),
        },
        actorUserId: 'u1',
      },
      'no-such-customer-slug',
      actor,
      query
    )
    expect(rec.unavailable).toBe('unreachable')
    expect(rec.rows).toEqual([])
  })
})
