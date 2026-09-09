/**
 * Tests for the dual-mode surface resolver (domain-surface.ts) and the
 * change-request model (change-request.ts) — ADR 0041 §4.3, the Read+Request
 * half of the authority design.
 */

import { describe, it, expect } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { ORG_ID } from '../src/lib/constants'
import type { AuthorityPosture } from '../src/lib/operator/authority'
import type { ChangeRequestRow } from '../src/lib/portal/operator/change-request'
import {
  resolveDomainSurface,
  resolveDomainSurfaceMode,
} from '../src/lib/portal/operator/domain-surface'
import {
  createChangeRequest,
  listOpenChangeRequests,
  updateChangeRequestStatus,
} from '../src/lib/portal/operator/change-request'

// ---------------------------------------------------------------------------
// Dual-mode resolver
// ---------------------------------------------------------------------------

const MANAGED: AuthorityPosture = { default: 'managed', overrides: {} }
const PEOPLE_CLIENT: AuthorityPosture = {
  default: 'managed',
  overrides: { people_access: 'client' },
}

describe('resolveDomainSurfaceMode (Layer 1 ∧ Layer 2)', () => {
  it('is read_request when the authority switch is managed, even if the role permits', () => {
    expect(resolveDomainSurfaceMode(MANAGED, 'people_access', true)).toBe('read_request')
  })

  it('is read_request when the switch is client but the role does not permit', () => {
    expect(resolveDomainSurfaceMode(PEOPLE_CLIENT, 'people_access', false)).toBe('read_request')
  })

  it('is operable only when the switch is client AND the role permits', () => {
    expect(resolveDomainSurfaceMode(PEOPLE_CLIENT, 'people_access', true)).toBe('operable')
  })

  it('a null posture is never operable (launch-safe)', () => {
    expect(resolveDomainSurfaceMode(null, 'connectors', true)).toBe('read_request')
  })
})

describe('resolveDomainSurface (visibility + mode)', () => {
  it('cost is never visible to the client', () => {
    const s = resolveDomainSurface(MANAGED, 'cost', true)
    expect(s.visible).toBe(false)
  })

  it('provisioning is visible but never operable (SMD-only)', () => {
    const s = resolveDomainSurface(MANAGED, 'provisioning', true)
    expect(s).toEqual({ visible: true, mode: 'read_request' })
  })

  it('a switchable client domain with role permission is operable', () => {
    const s = resolveDomainSurface(PEOPLE_CLIENT, 'people_access', true)
    expect(s).toEqual({ visible: true, mode: 'operable' })
  })

  it('a switchable managed domain is visible read_request', () => {
    const s = resolveDomainSurface(MANAGED, 'connectors', true)
    expect(s).toEqual({ visible: true, mode: 'read_request' })
  })
})

// ---------------------------------------------------------------------------
// Change-request store
// ---------------------------------------------------------------------------

const migrationsDir = resolve(process.cwd(), 'migrations')
const ENTITY_ID = 'entity-cr'
const SLUG = 'smith-pi-firm'

async function freshDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
    .bind(ENTITY_ID, ORG_ID, 'Smith PI Firm', SLUG)
    .run()
  return db
}

const baseInput = {
  entity_id: ENTITY_ID,
  customer_slug: SLUG,
  requested_by_user_id: 'user-1',
  requested_by_email: 'partner@firm.com',
}

describe('createChangeRequest', () => {
  it('files a request for a valid switchable domain', async () => {
    const db = await freshDb()
    const r = await createChangeRequest(db, {
      ...baseInput,
      domain: 'connectors',
      summary: 'Please connect our Clio.',
    })
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.id).toBeGreaterThan(0)
  })

  it('rejects an invalid domain (not a switchable authority domain)', async () => {
    const db = await freshDb()
    const r = await createChangeRequest(db, {
      ...baseInput,
      domain: 'cost',
      summary: 'show me cost',
    })
    expect(r).toEqual({ ok: false, error: 'invalid_domain' })
  })

  it('rejects an empty summary', async () => {
    const db = await freshDb()
    const r = await createChangeRequest(db, {
      ...baseInput,
      domain: 'people_access',
      summary: '   ',
    })
    expect(r).toEqual({ ok: false, error: 'empty_summary' })
  })
})

/** Direct read of a customer's own requests, recent first. */
async function requestsForSlug(db: D1Database, slug: string): Promise<ChangeRequestRow[]> {
  const { results } = await db
    .prepare(
      `SELECT * FROM operator_change_requests
        WHERE customer_slug = ? ORDER BY created_at DESC`
    )
    .bind(slug)
    .all<ChangeRequestRow>()
  return results ?? []
}

describe('change-request read + lifecycle', () => {
  it('lists a customer’s own requests and the admin open inbox', async () => {
    const db = await freshDb()
    await createChangeRequest(db, { ...baseInput, domain: 'connectors', summary: 'connect Clio' })
    await createChangeRequest(db, { ...baseInput, domain: 'memory', summary: 'review a rule' })
    const mine = await requestsForSlug(db, SLUG)
    expect(mine).toHaveLength(2)
    const inbox = await listOpenChangeRequests(db)
    expect(inbox).toHaveLength(2)
    expect(inbox.every((r) => r.status === 'open')).toBe(true)
  })

  it('resolving stamps the resolver and drops it from the open inbox', async () => {
    const db = await freshDb()
    const created = await createChangeRequest(db, {
      ...baseInput,
      domain: 'connectors',
      summary: 'connect Clio',
    })
    if (!created.ok) throw new Error('setup failed')
    const ok = await updateChangeRequestStatus(db, {
      id: created.id,
      status: 'resolved',
      resolved_by_email: 'smd@smd.services',
      resolution_note: 'Connected.',
    })
    expect(ok).toBe(true)
    const inbox = await listOpenChangeRequests(db)
    expect(inbox).toHaveLength(0)
    const mine = await requestsForSlug(db, SLUG)
    expect(mine[0].status).toBe('resolved')
    expect(mine[0].resolved_by_email).toBe('smd@smd.services')
    expect(mine[0].resolved_at).not.toBeNull()
  })

  it('acknowledged keeps the request in the inbox without a resolver stamp', async () => {
    const db = await freshDb()
    const created = await createChangeRequest(db, {
      ...baseInput,
      domain: 'connectors',
      summary: 'connect Clio',
    })
    if (!created.ok) throw new Error('setup failed')
    await updateChangeRequestStatus(db, {
      id: created.id,
      status: 'acknowledged',
      resolved_by_email: 'smd@smd.services',
      resolution_note: null,
    })
    const inbox = await listOpenChangeRequests(db)
    expect(inbox).toHaveLength(1)
    expect(inbox[0].resolved_at).toBeNull()
  })

  it('returns false when updating a non-existent request', async () => {
    const db = await freshDb()
    const ok = await updateChangeRequestStatus(db, {
      id: 9999,
      status: 'resolved',
      resolved_by_email: 'smd@smd.services',
      resolution_note: null,
    })
    expect(ok).toBe(false)
  })
})
