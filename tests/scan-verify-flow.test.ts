/**
 * Tests for the magic-link verification + scan_request DAL flow (#598).
 *
 * Exercises:
 *   - Token hash storage (raw token never persisted)
 *   - Idempotent verification (re-clicking the link doesn't re-flag)
 *   - 24h expiry enforcement via isScanTokenFresh
 *   - State transitions: pending_verification -> verified -> completed
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

import {
  createScanRequest,
  getScanRequest,
  getScanRequestByTokenHash,
  markScanVerified,
  updateScanRequestRun,
} from '../src/lib/db/scan-requests'
import { generateScanToken, hashScanToken, isScanTokenFresh } from '../src/lib/scan/tokens'

const migrationsDir = resolve(process.cwd(), 'migrations')

async function freshDb(): Promise<D1Database> {
  const db = createTestD1() as unknown as D1Database
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

describe('scan_request DAL', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('persists only the token hash, never the raw token', async () => {
    const { token, hash } = await generateScanToken()
    const row = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    expect(row.verification_token_hash).toBe(hash)
    // The raw token must never appear in any column.
    const json = JSON.stringify(row)
    expect(json).not.toContain(token)
  })

  it('looks up rows by hash', async () => {
    const { token, hash } = await generateScanToken()
    const created = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    const recomputed = await hashScanToken(token)
    const found = await getScanRequestByTokenHash(db, recomputed)
    expect(found?.id).toBe(created.id)
  })

  it('rejects invalid tokens (different hash) without leaking', async () => {
    const { hash } = await generateScanToken()
    await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    const wrongHash = await hashScanToken('attacker-guess')
    const found = await getScanRequestByTokenHash(db, wrongHash)
    expect(found).toBeNull()
  })

  it('markScanVerified sets verified_at and transitions status', async () => {
    const { hash } = await generateScanToken()
    const row = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    expect(row.scan_status).toBe('pending_verification')
    expect(row.verified_at).toBeNull()
    const verified = await markScanVerified(db, row.id)
    expect(verified?.scan_status).toBe('verified')
    expect(verified?.verified_at).toBeTruthy()
  })

  it('markScanVerified is idempotent — re-click does not overwrite verified_at', async () => {
    const { hash } = await generateScanToken()
    const row = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    const v1 = await markScanVerified(db, row.id)
    // Wait for clock granularity to advance so we can detect a no-op.
    await new Promise((r) => setTimeout(r, 5))
    const v2 = await markScanVerified(db, row.id)
    expect(v2?.verified_at).toBe(v1?.verified_at)
  })

  it('updateScanRequestRun transitions to completed', async () => {
    const { hash } = await generateScanToken()
    const row = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'example.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    await markScanVerified(db, row.id)
    await updateScanRequestRun(db, row.id, {
      scan_status: 'completed',
      scan_completed_at: new Date().toISOString(),
      email_sent_at: new Date().toISOString(),
    })
    const final = await getScanRequest(db, row.id)
    expect(final?.scan_status).toBe('completed')
    expect(final?.email_sent_at).toBeTruthy()
  })

  it('updateScanRequestRun records thin_footprint refusal', async () => {
    const { hash } = await generateScanToken()
    const row = await createScanRequest(db, {
      email: 'a@b.com',
      domain: 'thin.com',
      verification_token_hash: hash,
      request_ip: '1.1.1.1',
    })
    await markScanVerified(db, row.id)
    await updateScanRequestRun(db, row.id, {
      scan_status: 'thin_footprint',
      thin_footprint_skipped: true,
      scan_completed_at: new Date().toISOString(),
      error_message: 'thin_footprint:no_website_no_places',
    })
    const final = await getScanRequest(db, row.id)
    expect(final?.scan_status).toBe('thin_footprint')
    expect(final?.thin_footprint_skipped).toBe(1)
    expect(final?.error_message).toContain('no_website_no_places')
  })

  it('rejects rows with control characters in email/domain', async () => {
    await expect(
      createScanRequest(db, {
        email: 'evil\nuser@example.com',
        domain: 'example.com',
        verification_token_hash: 'h',
        request_ip: '1.1.1.1',
      })
    ).rejects.toThrow()
    await expect(
      createScanRequest(db, {
        email: 'good@example.com',
        domain: 'evil\ndomain.com',
        verification_token_hash: 'h',
        request_ip: '1.1.1.1',
      })
    ).rejects.toThrow()
  })
})

describe('token expiry', () => {
  it('treats a fresh row as fresh', async () => {
    const created = new Date().toISOString()
    expect(isScanTokenFresh(created)).toBe(true)
  })

  it('treats a 25h-old row as expired', async () => {
    const created = new Date(Date.now() - 25 * 60 * 60 * 1000).toISOString()
    expect(isScanTokenFresh(created)).toBe(false)
  })
})
