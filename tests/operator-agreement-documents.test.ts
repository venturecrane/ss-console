/**
 * Executed Operator agreement documents (ss#2641).
 *
 * Two properties are worth testing and the rest is plumbing:
 *
 *   1. **Only executed paper can be recorded.** The portal renders whatever is
 *      here as the firm's operative terms, so a draft or a future-dated
 *      document must be impossible to enter.
 *   2. **A key is authorized by its ROW, not its shape.** The download
 *      endpoint serves three document families; an agreement key belonging to
 *      another firm must be refused outright rather than falling through to
 *      the engagement checks, and a caller without a governance role must be
 *      refused even for its own firm's paper.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import {
  createOperatorAgreementDocument,
  isExecutedOnValid,
  listOperatorAgreementDocuments,
} from '../src/lib/db/operator-agreements'
import { getOperatorAgreementKey } from '../src/lib/storage/r2'
import {
  getOperatorAgreementForKey,
  listAgreementsForInstance,
  AGREEMENT_READER_ROLES,
} from '../src/lib/portal/agreement-documents'

const TODAY = new Date('2026-08-29T18:00:00Z')

const ROW = {
  id: 'doc-1',
  org_id: 'org-1',
  entity_id: 'ent-1',
  instance_slug: 'ashton-price',
  title: 'Operator Service Agreement',
  executed_on: '2026-09-08',
  storage_key: 'org-1/operator/ashton-price/agreements/doc-1/agreement.pdf',
  file_name: 'agreement.pdf',
  uploaded_by: 'user-admin',
  created_at: '2026-09-08T00:00:00Z',
  updated_at: '2026-09-08T00:00:00Z',
}

/** D1 fake: one document row (or none) plus a role list. */
function makeDb(opts: { row?: typeof ROW | null; roles?: string[]; rows?: (typeof ROW)[] }) {
  const db = {
    prepare(sql: string) {
      return {
        bind(..._args: unknown[]) {
          return {
            first() {
              if (sql.includes('operator_agreement_documents')) {
                return Promise.resolve(opts.row ?? null)
              }
              throw new Error(`unexpected first(): ${sql}`)
            },
            all() {
              if (sql.includes('FROM product_roles')) {
                return Promise.resolve({ results: (opts.roles ?? []).map((role) => ({ role })) })
              }
              if (sql.includes('operator_agreement_documents')) {
                return Promise.resolve({ results: opts.rows ?? [] })
              }
              throw new Error(`unexpected all(): ${sql}`)
            },
          }
        },
      }
    },
  }
  return db as unknown as D1Database
}

describe('isExecutedOnValid', () => {
  it('accepts a past or present date', () => {
    expect(isExecutedOnValid('2026-08-29', TODAY)).toBe(true)
    expect(isExecutedOnValid('2026-07-27', TODAY)).toBe(true)
  })

  it('REFUSES a future date — an agreement executed tomorrow is not executed', () => {
    expect(isExecutedOnValid('2026-08-30', TODAY)).toBe(false)
    expect(isExecutedOnValid('2027-01-01', TODAY)).toBe(false)
  })

  it('refuses a malformed or impossible date rather than rolling it over', () => {
    expect(isExecutedOnValid('', TODAY)).toBe(false)
    expect(isExecutedOnValid('08/29/2026', TODAY)).toBe(false)
    expect(isExecutedOnValid('2026-8-29', TODAY)).toBe(false)
    // Date() would silently roll this into March; the round-trip guard catches it.
    expect(isExecutedOnValid('2026-02-31', TODAY)).toBe(false)
  })
})

describe('getOperatorAgreementForKey', () => {
  const args = { userId: 'user-1', orgId: 'org-1', entityId: 'ent-1' }

  it('allows a principal to read their own firm’s executed paper', async () => {
    const db = makeDb({ row: ROW, roles: ['principal'] })
    const decision = await getOperatorAgreementForKey(db, { ...args, key: ROW.storage_key })
    expect(decision.kind).toBe('allowed')
  })

  it('allows the compliance role too — the same set the Compliance page admits', async () => {
    const db = makeDb({ row: ROW, roles: ['compliance'] })
    const decision = await getOperatorAgreementForKey(db, { ...args, key: ROW.storage_key })
    expect(decision.kind).toBe('allowed')
    expect(AGREEMENT_READER_ROLES).toEqual(['principal', 'compliance'])
  })

  it('REFUSES a role outside the governance set', async () => {
    const db = makeDb({ row: ROW, roles: ['staff'] })
    const decision = await getOperatorAgreementForKey(db, { ...args, key: ROW.storage_key })
    expect(decision.kind).toBe('forbidden')
  })

  it('REFUSES another firm’s document instead of falling through to the engagement checks', async () => {
    const db = makeDb({ row: { ...ROW, entity_id: 'ent-OTHER' }, roles: ['principal'] })
    const decision = await getOperatorAgreementForKey(db, { ...args, key: ROW.storage_key })
    expect(decision.kind).toBe('forbidden')
  })

  it('REFUSES a document from another org', async () => {
    const db = makeDb({ row: { ...ROW, org_id: 'org-OTHER' }, roles: ['principal'] })
    const decision = await getOperatorAgreementForKey(db, { ...args, key: ROW.storage_key })
    expect(decision.kind).toBe('forbidden')
  })

  it('falls through for a non-agreement key so engagement documents still serve', async () => {
    const db = makeDb({ row: null, roles: ['principal'] })
    const decision = await getOperatorAgreementForKey(db, {
      ...args,
      key: 'org-1/engagements/eng-1/docs/aa11bb22/sow.pdf',
    })
    expect(decision.kind).toBe('not_agreement')
  })
})

// ---------------------------------------------------------------------------
// A3 (claims-2026-09-04): keyed by the row, so two documents can share a name
//
// The name-hash key convention makes "the same filename replaces". For a
// deliverable that is a feature; for executed paper it destroyed the
// original: an amendment uploaded as `agreement.pdf` overwrote the
// agreement's bytes in R2 and, through the UNIQUE storage_key upsert, took
// over its row. Both documents are signed; both must stay.
// ---------------------------------------------------------------------------

describe('getOperatorAgreementKey', () => {
  it('puts the document id, not a name hash, between the prefix and the filename', () => {
    const key = getOperatorAgreementKey(
      'org-1',
      'ashton-price',
      'doc-9',
      'Amendment 1 (signed).pdf'
    )
    expect(key).toBe('org-1/operator/ashton-price/agreements/doc-9/Amendment_1__signed_.pdf')
  })

  it('two documents with the same filename get two keys', () => {
    const a = getOperatorAgreementKey('org-1', 'ashton-price', 'doc-a', 'agreement.pdf')
    const b = getOperatorAgreementKey('org-1', 'ashton-price', 'doc-b', 'agreement.pdf')
    expect(a).not.toBe(b)
    // The download endpoint's two checks: org prefix, and no traversal.
    for (const key of [a, b]) {
      expect(key.startsWith('org-1/')).toBe(true)
      expect(key).not.toContain('..')
      expect(key).not.toContain('//')
      expect(key.split('/').pop()).toBe('agreement.pdf')
    }
  })
})

describe('createOperatorAgreementDocument (real D1)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
  })

  function doc(id: string, over: Partial<{ title: string; executed_on: string }> = {}) {
    return {
      id,
      org_id: 'org-1',
      entity_id: 'ent-1',
      instance_slug: 'ashton-price',
      title: 'Operator Service Agreement',
      executed_on: '2026-08-01',
      storage_key: getOperatorAgreementKey('org-1', 'ashton-price', id, 'agreement.pdf'),
      file_name: 'agreement.pdf',
      uploaded_by: 'user-admin',
      ...over,
    }
  }

  it('two uploads with the same filename are two rows with two keys, both listed', async () => {
    const first = await createOperatorAgreementDocument(db, doc('doc-a'))
    const second = await createOperatorAgreementDocument(
      db,
      doc('doc-b', { title: 'Amendment 1', executed_on: '2026-08-20' })
    )
    expect(first.id).toBe('doc-a')
    expect(second.id).toBe('doc-b')
    expect(first.storage_key).not.toBe(second.storage_key)

    const listed = await listOperatorAgreementDocuments(db, 'ent-1', 'ashton-price')
    expect(listed.map((d) => d.id)).toEqual(['doc-b', 'doc-a']) // newest executed first
    expect(listed.map((d) => d.title)).toEqual(['Amendment 1', 'Operator Service Agreement'])
  })

  it('is a plain INSERT: re-using a storage key is refused, never silently merged', async () => {
    await createOperatorAgreementDocument(db, doc('doc-a'))
    await expect(
      createOperatorAgreementDocument(db, {
        ...doc('doc-c', { title: 'Something else' }),
        storage_key: getOperatorAgreementKey('org-1', 'ashton-price', 'doc-a', 'agreement.pdf'),
      })
    ).rejects.toThrow()
    const listed = await listOperatorAgreementDocuments(db, 'ent-1', 'ashton-price')
    expect(listed).toHaveLength(1)
    expect(listed[0].title).toBe('Operator Service Agreement')
  })
})

describe('listAgreementsForInstance', () => {
  it('renders nothing rather than promising paper that does not exist', async () => {
    const db = makeDb({ rows: [] })
    expect(await listAgreementsForInstance(db, 'ent-1', 'ashton-price')).toEqual([])
  })

  it('carries the authored title and date through to the portal row', async () => {
    const db = makeDb({ rows: [ROW] })
    const items = await listAgreementsForInstance(db, 'ent-1', 'ashton-price')
    expect(items).toHaveLength(1)
    expect(items[0].title).toBe('Operator Service Agreement')
    expect(items[0].executedOn).toBe('2026-09-08')
    expect(items[0].href).toBe(`/api/portal/documents/${ROW.storage_key}`)
  })
})
