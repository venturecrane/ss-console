/**
 * Tests for the engagement_contacts data access layer.
 *
 * Covers:
 *   - Org scoping and the single-primary invariant against a migrated D1
 *   - Single-primary behaviour: addEngagementContact with is_primary=true
 *     clears any existing primary on the same engagement; without is_primary
 *     it does not touch existing primaries
 *   - setEngagementContactPrimary clears OTHER primaries but never the row
 *     being promoted
 *   - removeEngagementContact returns false for cross-org rows
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  addEngagementContact,
  getEngagementContact,
  listEngagementContacts,
  removeEngagementContact,
  setEngagementContactPrimary,
} from './engagement-contacts'
import { createContact } from './contacts'
import { migratedDb, seedEngagement, seedEntity, seedOrg } from '../../../tests/_stubs/behavioural'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const ORG_ID = 'org-001'
const ENGAGEMENT_ID = 'eng-001'
const CONTACT_ID = 'contact-001'

interface CapturedCall {
  sql: string
  params: unknown[]
}

function buildMockDb(options: {
  firstResults?: Record<string, unknown>
  allResults?: Record<string, unknown>
}): { db: D1Database; calls: CapturedCall[] } {
  const calls: CapturedCall[] = []
  const db = {
    prepare: vi.fn().mockImplementation((sql: string) => ({
      bind: vi.fn().mockImplementation((...params: unknown[]) => {
        calls.push({ sql, params })
        return {
          first: vi.fn().mockImplementation(async () => {
            for (const [snippet, result] of Object.entries(options.firstResults ?? {})) {
              if (sql.includes(snippet)) return result
            }
            return null
          }),
          all: vi.fn().mockImplementation(async () => {
            for (const [snippet, result] of Object.entries(options.allResults ?? {})) {
              if (sql.includes(snippet)) return { results: result }
            }
            return { results: [] }
          }),
          run: vi.fn().mockResolvedValue({}),
        }
      }),
    })),
  } as unknown as D1Database
  return { db, calls }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('engagement-contacts: SQL safety', () => {
  it('uses parameterized queries (no string interpolation in SQL)', () => {
    const code = readFileSync(resolve('src/lib/db/engagement-contacts.ts'), 'utf-8')
    expect(code).toContain('.bind(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })
})

// ---------------------------------------------------------------------------
// Org scoping and the single-primary invariant against a migrated D1
// ---------------------------------------------------------------------------

describe('engagement-contacts against real D1', () => {
  const ORG_A = 'org-a'
  const ORG_B = 'org-b'
  const ENG_A = 'eng-a'
  let db: D1Database
  let contactA: string
  let contactB: string

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: 'ent-a', orgId: ORG_A })
    await seedEngagement(db, { id: ENG_A, orgId: ORG_A, entityId: 'ent-a' })
    contactA = (await createContact(db, ORG_A, 'ent-a', { name: 'Avery Owner' })).id
    contactB = (await createContact(db, ORG_A, 'ent-a', { name: 'Blake Champion' })).id
  })

  it('lists and reads an assignment only within its own org', async () => {
    const row = await addEngagementContact(db, ENG_A, { contact_id: contactA, role: 'owner' })
    const listed = await listEngagementContacts(db, ORG_A, ENG_A)
    expect(listed.map((r) => r.id)).toEqual([row.id])
    expect(listed[0].contact_name).toBe('Avery Owner')
    expect(await getEngagementContact(db, ORG_A, row.id)).not.toBeNull()

    expect(await listEngagementContacts(db, ORG_B, ENG_A)).toEqual([])
    expect(await getEngagementContact(db, ORG_B, row.id)).toBeNull()
    expect(await setEngagementContactPrimary(db, ORG_B, row.id)).toBeNull()
    expect(await removeEngagementContact(db, ORG_B, row.id)).toBe(false)
    expect(await getEngagementContact(db, ORG_A, row.id)).not.toBeNull()
  })

  it('keeps exactly one primary as primaries are added and promoted', async () => {
    const first = await addEngagementContact(db, ENG_A, {
      contact_id: contactA,
      role: 'owner',
      is_primary: true,
    })
    const second = await addEngagementContact(db, ENG_A, {
      contact_id: contactB,
      role: 'champion',
      is_primary: true,
    })
    const primaries = async () =>
      (await listEngagementContacts(db, ORG_A, ENG_A)).filter((r) => r.is_primary).map((r) => r.id)
    expect(await primaries()).toEqual([second.id])

    const promoted = await setEngagementContactPrimary(db, ORG_A, first.id)
    expect(promoted?.is_primary).toBe(1)
    expect(await primaries()).toEqual([first.id])

    await setEngagementContactPrimary(db, ORG_A, first.id)
    expect(await primaries()).toEqual([first.id])
  })

  it('gives each assignment a distinct id', async () => {
    const a = await addEngagementContact(db, ENG_A, { contact_id: contactA, role: 'owner' })
    const b = await addEngagementContact(db, ENG_A, { contact_id: contactB, role: 'champion' })
    expect(a.id).not.toBe(b.id)
  })
})

// ---------------------------------------------------------------------------
// Single-primary behaviour: addEngagementContact
// ---------------------------------------------------------------------------

describe('addEngagementContact: single-primary invariant', () => {
  it('clears existing primary on the engagement when is_primary=true', async () => {
    const { db, calls } = buildMockDb({
      firstResults: {
        // Final retrieve after insert
        'SELECT * FROM engagement_contacts WHERE id = ?': {
          id: 'ec-new',
          engagement_id: ENGAGEMENT_ID,
          contact_id: CONTACT_ID,
          role: 'owner',
          is_primary: 1,
          notes: null,
          created_at: '2026-05-01T00:00:00Z',
        },
      },
    })

    await addEngagementContact(db, ENGAGEMENT_ID, {
      contact_id: CONTACT_ID,
      role: 'owner',
      is_primary: true,
    })

    const clearCall = calls.find(
      (c) =>
        c.sql.includes('UPDATE engagement_contacts SET is_primary = 0') &&
        c.sql.includes('WHERE engagement_id = ? AND is_primary = 1')
    )
    expect(clearCall).toBeDefined()
    expect(clearCall?.params).toEqual([ENGAGEMENT_ID])

    const insertCall = calls.find((c) => c.sql.includes('INSERT INTO engagement_contacts'))
    expect(insertCall).toBeDefined()
    // Insert binds: id, engagementId, contactId, role, is_primary (1), notes
    expect(insertCall?.params[1]).toBe(ENGAGEMENT_ID)
    expect(insertCall?.params[2]).toBe(CONTACT_ID)
    expect(insertCall?.params[3]).toBe('owner')
    expect(insertCall?.params[4]).toBe(1)
  })

  it('does NOT clear existing primary when is_primary=false', async () => {
    const { db, calls } = buildMockDb({
      firstResults: {
        'SELECT * FROM engagement_contacts WHERE id = ?': {
          id: 'ec-new',
          engagement_id: ENGAGEMENT_ID,
          contact_id: CONTACT_ID,
          role: 'champion',
          is_primary: 0,
          notes: null,
          created_at: '2026-05-01T00:00:00Z',
        },
      },
    })

    await addEngagementContact(db, ENGAGEMENT_ID, {
      contact_id: CONTACT_ID,
      role: 'champion',
      is_primary: false,
    })

    const clearCall = calls.find((c) =>
      c.sql.includes('UPDATE engagement_contacts SET is_primary = 0')
    )
    expect(clearCall).toBeUndefined()
  })
})

// ---------------------------------------------------------------------------
// Single-primary behaviour: setEngagementContactPrimary
// ---------------------------------------------------------------------------

describe('setEngagementContactPrimary: single-primary invariant', () => {
  it('clears every other primary row on the engagement, then sets target', async () => {
    const existingRow = {
      id: 'ec-target',
      engagement_id: ENGAGEMENT_ID,
      contact_id: CONTACT_ID,
      role: 'decision_maker',
      is_primary: 0,
      notes: null,
      created_at: '2026-05-01T00:00:00Z',
    }
    const { db, calls } = buildMockDb({
      firstResults: {
        // Both the precondition lookup and the post-update reload return the row.
        'SELECT ec.* FROM engagement_contacts ec': existingRow,
      },
    })

    const result = await setEngagementContactPrimary(db, ORG_ID, 'ec-target')

    expect(result).toEqual(existingRow)

    // The clear-others UPDATE excludes the target row via `id <> ?`.
    const clearOthers = calls.find(
      (c) =>
        c.sql.includes('UPDATE engagement_contacts SET is_primary = 0') && c.sql.includes('id <> ?')
    )
    expect(clearOthers).toBeDefined()
    expect(clearOthers?.params).toEqual([ENGAGEMENT_ID, 'ec-target'])

    const setSelf = calls.find(
      (c) =>
        c.sql.includes('UPDATE engagement_contacts SET is_primary = 1') &&
        c.sql.includes('WHERE id = ?')
    )
    expect(setSelf).toBeDefined()
    expect(setSelf?.params).toEqual(['ec-target'])
  })

  it('returns null and writes nothing when row is not in caller org', async () => {
    const { db, calls } = buildMockDb({
      // No firstResults for the JOIN lookup → returns null → cross-org or missing.
    })

    const result = await setEngagementContactPrimary(db, ORG_ID, 'ec-other-org')

    expect(result).toBeNull()
    const writes = calls.filter((c) => c.sql.includes('UPDATE engagement_contacts'))
    expect(writes).toHaveLength(0)
  })
})

// ---------------------------------------------------------------------------
// removeEngagementContact
// ---------------------------------------------------------------------------

describe('removeEngagementContact', () => {
  it('returns false and writes nothing when row is not in caller org', async () => {
    const { db, calls } = buildMockDb({})
    const ok = await removeEngagementContact(db, ORG_ID, 'ec-other-org')
    expect(ok).toBe(false)
    const deletes = calls.filter((c) => c.sql.includes('DELETE FROM engagement_contacts'))
    expect(deletes).toHaveLength(0)
  })

  it('issues a DELETE bound to the row id when row is in scope', async () => {
    const { db, calls } = buildMockDb({
      firstResults: {
        'SELECT ec.* FROM engagement_contacts ec': {
          id: 'ec-target',
          engagement_id: ENGAGEMENT_ID,
          contact_id: CONTACT_ID,
          role: 'owner',
          is_primary: 0,
          notes: null,
          created_at: '2026-05-01T00:00:00Z',
        },
      },
    })
    const ok = await removeEngagementContact(db, ORG_ID, 'ec-target')
    expect(ok).toBe(true)
    const del = calls.find((c) => c.sql.startsWith('DELETE FROM engagement_contacts'))
    expect(del).toBeDefined()
    expect(del?.params).toEqual(['ec-target'])
  })
})
