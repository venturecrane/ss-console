/**
 * Tests for the 4-dimensional /scan rate limiter (#598).
 *
 * Each dimension is exercised against an in-memory D1 with the real
 * migration schema. The dimensions:
 *   1. Per-IP / 24h          (5 scans)
 *   2. Per-email-domain / 7d (1 scan, free providers exempt)
 *   3. Per-scanned-domain    (1 completed scan / 30d)
 *   4. Global / 24h          (200 scans)
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
  updateScanRequestRun,
  type ScanStatus,
} from '../src/lib/db/scan-requests'
import { checkScanRateLimits, RATE_LIMITS } from '../src/lib/diagnostic/rate-limit'
import { hashScanToken } from '../src/lib/scan/tokens'

const migrationsDir = resolve(process.cwd(), 'migrations')

async function freshDb(): Promise<D1Database> {
  const db = createTestD1() as unknown as D1Database
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

async function seed(
  db: D1Database,
  opts: {
    email: string
    domain: string
    ip?: string | null
    status?: ScanStatus
  }
): Promise<void> {
  const hash = await hashScanToken(`${opts.email}-${opts.domain}-${Math.random()}`)
  const row = await createScanRequest(db, {
    email: opts.email,
    domain: opts.domain,
    verification_token_hash: hash,
    request_ip: opts.ip ?? '1.2.3.4',
  })
  if (opts.status && opts.status !== 'pending_verification') {
    await updateScanRequestRun(db, row.id, { scan_status: opts.status })
  }
}

describe('checkScanRateLimits — per IP', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('allows the first scan from an IP', async () => {
    const r = await checkScanRateLimits(db, {
      ip: '5.5.5.5',
      emailDomain: 'newcustomer.com',
      scannedDomain: 'someplace.com',
    })
    expect(r.allowed).toBe(true)
  })

  it('blocks after the configured per-IP limit', async () => {
    for (let i = 0; i < RATE_LIMITS.per_ip_24h; i++) {
      await seed(db, {
        email: `u${i}@u${i}.com`,
        domain: `d${i}.com`,
        ip: '7.7.7.7',
      })
    }
    const r = await checkScanRateLimits(db, {
      ip: '7.7.7.7',
      emailDomain: 'newdomain.com',
      scannedDomain: 'newscan.com',
    })
    expect(r.allowed).toBe(false)
    if (!r.allowed) expect(r.dimension).toBe('per_ip')
  })

  it('does not block a different IP', async () => {
    for (let i = 0; i < RATE_LIMITS.per_ip_24h; i++) {
      await seed(db, {
        email: `u${i}@u${i}.com`,
        domain: `d${i}.com`,
        ip: '7.7.7.7',
      })
    }
    const r = await checkScanRateLimits(db, {
      ip: '8.8.8.8',
      emailDomain: 'fresh.com',
      scannedDomain: 'fresh-target.com',
    })
    expect(r.allowed).toBe(true)
  })
})

describe('checkScanRateLimits — per email domain', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('blocks a second scan from the same email domain within 7 days', async () => {
    await seed(db, {
      email: 'first@acmecorp.com',
      domain: 'first.com',
      ip: '1.2.3.4',
    })
    const r = await checkScanRateLimits(db, {
      ip: '9.9.9.9',
      emailDomain: 'acmecorp.com',
      scannedDomain: 'second.com',
    })
    expect(r.allowed).toBe(false)
    if (!r.allowed) expect(r.dimension).toBe('per_email_domain')
  })

  it('exempts free email providers from the per-domain limit', async () => {
    await seed(db, { email: 'a@gmail.com', domain: 'a.com', ip: '1.1.1.1' })
    const r = await checkScanRateLimits(db, {
      ip: '2.2.2.2',
      emailDomain: 'gmail.com',
      scannedDomain: 'b.com',
    })
    expect(r.allowed).toBe(true)
  })
})

describe('checkScanRateLimits — per scanned domain', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('blocks a re-scan of the same domain within 30 days when previous scan completed', async () => {
    await seed(db, {
      email: 'first@first.com',
      domain: 'targetbusiness.com',
      ip: '1.1.1.1',
      status: 'completed',
    })
    const r = await checkScanRateLimits(db, {
      ip: '2.2.2.2',
      emailDomain: 'second.com',
      scannedDomain: 'targetbusiness.com',
    })
    expect(r.allowed).toBe(false)
    if (!r.allowed) expect(r.dimension).toBe('per_domain')
  })

  it('does NOT block when the previous scan only thin-footprinted', async () => {
    // A refused thin-footprint scan shouldn't lock out a legit retry from
    // a different prospect at the same business who actually has a real
    // email + intent.
    await seed(db, {
      email: 'a@a.com',
      domain: 'thinbiz.com',
      ip: '1.1.1.1',
      status: 'thin_footprint',
    })
    const r = await checkScanRateLimits(db, {
      ip: '2.2.2.2',
      emailDomain: 'b.com',
      scannedDomain: 'thinbiz.com',
    })
    expect(r.allowed).toBe(true)
  })

  it('does NOT block when previous attempt is still pending verification', async () => {
    // A submitted-but-unverified row shouldn't lock the domain — that's
    // exactly the cost-guard reason verification exists.
    await seed(db, {
      email: 'a@a.com',
      domain: 'pendingbiz.com',
      ip: '1.1.1.1',
      status: 'pending_verification',
    })
    const r = await checkScanRateLimits(db, {
      ip: '2.2.2.2',
      emailDomain: 'b.com',
      scannedDomain: 'pendingbiz.com',
    })
    expect(r.allowed).toBe(true)
  })
})

describe('checkScanRateLimits — global cap', () => {
  let db: D1Database
  beforeEach(async () => {
    db = await freshDb()
  })

  it('returns allowed=true under the cap', async () => {
    const r = await checkScanRateLimits(db, {
      ip: '1.1.1.1',
      emailDomain: 'fresh.com',
      scannedDomain: 'fresh.com',
    })
    expect(r.allowed).toBe(true)
  })
})
