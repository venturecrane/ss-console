/**
 * Tests for the Machine -> control-plane auth verifier (ADR 0023 #10, the
 * per-tenant upgrade of 2026-09-10).
 *
 * The property Wave 1 lacked and this pins: a seat's key verifies ONLY for
 * the slug it was minted for. Every failure returns the same 401 shape.
 *
 * The D1 lookup is mocked with the join row the verifier selects, so these
 * are unit tests; tests/machine-credentials.test.ts runs the real SQL.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import { createHmac } from 'node:crypto'
import { verifyMachineRequest } from '../src/lib/auth/machine-key'

/** Node's HMAC, independent of the verifier's WebCrypto path: the two must agree. */
function hmacHex(plaintext: string, saltHex: string): string {
  return createHmac('sha256', Buffer.from(saltHex, 'hex')).update(plaintext, 'utf8').digest('hex')
}

const SHARED = '0'.repeat(64)
const KEY_A = 'a'.repeat(64)
const KEY_B = 'b'.repeat(64)
const OLD_A = 'c'.repeat(64)
const SALT_A = '11'.repeat(16)
const SALT_B = '22'.repeat(16)
const SALT_OLD = '33'.repeat(16)

interface JoinRow {
  entity_id: string
  key_hash: string | null
  salt: string | null
  prev_key_hash: string | null
  prev_salt: string | null
  prev_expires_at: string | null
}

function mockDb(rows: Record<string, JoinRow>): D1Database & { prepare: ReturnType<typeof vi.fn> } {
  const prepare = vi.fn((_sql: string) => ({
    bind: (slug: string) => ({
      first: async <T>(): Promise<T | null> => (rows[slug] as T | undefined) ?? null,
    }),
  }))
  return { prepare } as unknown as D1Database & { prepare: ReturnType<typeof vi.fn> }
}

function req(headers: Record<string, string>): Request {
  return new Request('https://example/api/internal/heartbeat', { method: 'POST', headers })
}

function credentialed(
  entityId: string,
  key: string,
  salt: string,
  prev?: { key: string; salt: string; expiresAt: string }
): JoinRow {
  return {
    entity_id: entityId,
    key_hash: hmacHex(key, salt),
    salt,
    prev_key_hash: prev ? hmacHex(prev.key, prev.salt) : null,
    prev_salt: prev?.salt ?? null,
    prev_expires_at: prev?.expiresAt ?? null,
  }
}

function sqliteStamp(msFromNow: number): string {
  return new Date(Date.now() + msFromNow).toISOString().replace('T', ' ').slice(0, 19)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('verifyMachineRequest: per-tenant credentials', () => {
  it('pins the HMAC vector shared with operator/bin/lib/machine_credential.py', async () => {
    // The vector is what the Python minter produces; the verifier must accept
    // a row built from it, which exercises its WebCrypto HMAC end to end.
    const vectorRow: JoinRow = {
      entity_id: 'ent-v',
      key_hash: 'e858ae0a20bbe215e94b7e816abc9f918ec4317aa725c351d26730af9e13071e',
      salt: '000102030405060708090a0b0c0d0e0f',
      prev_key_hash: null,
      prev_salt: null,
      prev_expires_at: null,
    }
    expect(hmacHex('test-plaintext-key', vectorRow.salt ?? '')).toBe(vectorRow.key_hash)
    const db = mockDb({ 'seat-v': vectorRow })
    const r = await verifyMachineRequest(
      req({ Authorization: 'Bearer test-plaintext-key', 'X-Tenant-Slug': 'seat-v' }),
      undefined,
      db
    )
    expect(r).toEqual({ ok: true, entityId: 'ent-v', slug: 'seat-v' })
  })

  it('accepts a seat presenting its own key for its own slug', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${KEY_A}`, 'X-Tenant-Slug': 'seat-a' }),
      SHARED,
      db
    )
    expect(r).toEqual({ ok: true, entityId: 'ent-a', slug: 'seat-a' })
  })

  it('refuses a cross-tenant forge: seat A key with seat B slug', async () => {
    const db = mockDb({
      'seat-a': credentialed('ent-a', KEY_A, SALT_A),
      'seat-b': credentialed('ent-b', KEY_B, SALT_B),
    })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${KEY_A}`, 'X-Tenant-Slug': 'seat-b' }),
      SHARED,
      db
    )
    expect(r).toEqual({ ok: false, status: 401 })
  })

  it('refuses the shared key for a slug that has a credential row', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${SHARED}`, 'X-Tenant-Slug': 'seat-a' }),
      SHARED,
      db
    )
    expect(r).toEqual({ ok: false, status: 401 })
  })

  it('accepts the previous key while its TTL is live (rotation window)', async () => {
    const db = mockDb({
      'seat-a': credentialed('ent-a', KEY_A, SALT_A, {
        key: OLD_A,
        salt: SALT_OLD,
        expiresAt: sqliteStamp(60 * 60 * 1000),
      }),
    })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${OLD_A}`, 'X-Tenant-Slug': 'seat-a' }),
      undefined,
      db
    )
    expect(r).toEqual({ ok: true, entityId: 'ent-a', slug: 'seat-a' })
  })

  it('refuses the previous key once its TTL has passed', async () => {
    const db = mockDb({
      'seat-a': credentialed('ent-a', KEY_A, SALT_A, {
        key: OLD_A,
        salt: SALT_OLD,
        expiresAt: sqliteStamp(-1000),
      }),
    })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${OLD_A}`, 'X-Tenant-Slug': 'seat-a' }),
      undefined,
      db
    )
    expect(r).toEqual({ ok: false, status: 401 })
  })

  it('works with the shared key unset once the seat has a row (retired fallback)', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${KEY_A}`, 'X-Tenant-Slug': 'seat-a' }),
      undefined,
      db
    )
    expect(r).toEqual({ ok: true, entityId: 'ent-a', slug: 'seat-a' })
  })
})

describe('verifyMachineRequest: transitional shared-key fallback', () => {
  const uncredentialed: JoinRow = {
    entity_id: 'ent-legacy',
    key_hash: null,
    salt: null,
    prev_key_hash: null,
    prev_salt: null,
    prev_expires_at: null,
  }

  it('accepts the shared key for a slug with no credential row, and says so in the log', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const db = mockDb({ legacy: uncredentialed })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${SHARED}`, 'X-Tenant-Slug': 'legacy' }),
      SHARED,
      db
    )
    expect(r).toEqual({ ok: true, entityId: 'ent-legacy', slug: 'legacy' })
    expect(warn).toHaveBeenCalledTimes(1)
    expect(String(warn.mock.calls[0]?.[0])).toContain('shared-key fallback')
    expect(String(warn.mock.calls[0]?.[0])).not.toContain(SHARED)
  })

  it('fails closed for a slug with no row once the shared key is unset', async () => {
    const db = mockDb({ legacy: uncredentialed })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${SHARED}`, 'X-Tenant-Slug': 'legacy' }),
      undefined,
      db
    )
    expect(r).toEqual({ ok: false, status: 401 })
  })

  it('refuses a wrong shared key', async () => {
    const db = mockDb({ legacy: uncredentialed })
    const r = await verifyMachineRequest(
      req({ Authorization: `Bearer ${'f'.repeat(64)}`, 'X-Tenant-Slug': 'legacy' }),
      SHARED,
      db
    )
    expect(r).toEqual({ ok: false, status: 401 })
  })
})

describe('verifyMachineRequest: uniform 401 on malformed input', () => {
  it('rejects a missing Authorization header', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    expect(await verifyMachineRequest(req({ 'X-Tenant-Slug': 'seat-a' }), SHARED, db)).toEqual({
      ok: false,
      status: 401,
    })
  })

  it('rejects a non-Bearer scheme and an empty bearer', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    expect(
      await verifyMachineRequest(
        req({ Authorization: `Basic ${KEY_A}`, 'X-Tenant-Slug': 'seat-a' }),
        SHARED,
        db
      )
    ).toEqual({ ok: false, status: 401 })
    expect(
      await verifyMachineRequest(
        req({ Authorization: 'Bearer ', 'X-Tenant-Slug': 'seat-a' }),
        SHARED,
        db
      )
    ).toEqual({ ok: false, status: 401 })
  })

  it('rejects a missing slug without touching the database', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    expect(
      await verifyMachineRequest(req({ Authorization: `Bearer ${KEY_A}` }), SHARED, db)
    ).toEqual({
      ok: false,
      status: 401,
    })
    expect(db.prepare).not.toHaveBeenCalled()
  })

  it('rejects an unknown slug with the same shape as a bad key', async () => {
    const db = mockDb({ 'seat-a': credentialed('ent-a', KEY_A, SALT_A) })
    expect(
      await verifyMachineRequest(
        req({ Authorization: `Bearer ${KEY_A}`, 'X-Tenant-Slug': 'nope' }),
        SHARED,
        db
      )
    ).toEqual({ ok: false, status: 401 })
  })
})
