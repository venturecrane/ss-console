/**
 * Cross-language round trip for per-tenant Machine credentials (migration 0114).
 *
 * operator/bin/lib/machine_credential.py is the only writer of
 * machine_credentials.key_hash; src/lib/auth/machine-key.ts is the only reader.
 * This test mints with the Python CLI into a real D1 (miniflare, every numeric
 * migration applied), then verifies with the TypeScript verifier. A drift in
 * either side's HMAC input shape, salt encoding, or SQL turns this red, which
 * no single-language unit test can do.
 *
 * What would make it false: a mint whose row the verifier rejects, a rotation
 * that 401s the previous key inside its TTL, or a seat A key accepted for
 * seat B.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { spawnSync } from 'child_process'
import { readFileSync, mkdtempSync } from 'fs'
import { tmpdir } from 'os'
import { resolve, join } from 'path'
import {
  installWorkerdPolyfills,
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import { verifyMachineRequest } from '../src/lib/auth/machine-key'

installWorkerdPolyfills()

const MINTER = resolve(process.cwd(), 'operator/bin/lib/machine_credential.py')

function mintWithPython(slug: string, prevTtlHours = 24): { plaintext: string; sql: string } {
  const dir = mkdtempSync(join(tmpdir(), 'machine-credential-'))
  const sqlOut = join(dir, 'mint.sql')
  const proc = spawnSync(
    'python3',
    [MINTER, '--slug', slug, '--sql-out', sqlOut, '--prev-ttl-hours', String(prevTtlHours)],
    { encoding: 'utf8' }
  )
  if (proc.status !== 0) throw new Error(`minter failed: ${proc.stderr}`)
  return { plaintext: proc.stdout, sql: readFileSync(sqlOut, 'utf8') }
}

function req(key: string, slug: string): Request {
  return new Request('https://example/api/internal/heartbeat', {
    method: 'POST',
    headers: { Authorization: `Bearer ${key}`, 'X-Tenant-Slug': slug },
  })
}

async function seedSeat(db: D1Database, slug: string, entityId: string): Promise<void> {
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
    .bind(entityId, 'org-a', `Entity ${slug}`, entityId)
    .run()
  await db
    .prepare(
      'INSERT INTO customer_configs (customer_slug, entity_id, org_id, schema_version, personas_json, git_sha, synced_at) ' +
        "VALUES (?, ?, 'org-a', '1', '[]', 'deadbeef', datetime('now'))"
    )
    .bind(slug, entityId)
    .run()
}

describe('machine credentials: Python mint, TypeScript verify', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, {
      files: discoverNumericMigrations(resolve(process.cwd(), 'migrations')),
    })
    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind('org-a', 'Org A', 'org-a')
      .run()
    await seedSeat(db, 'seat-a', 'ent-a')
    await seedSeat(db, 'seat-b', 'ent-b')
  })

  it('a minted key verifies for its slug and resolves the entity', async () => {
    const { plaintext, sql } = mintWithPython('seat-a')
    await db.prepare(sql).run()
    expect(await verifyMachineRequest(req(plaintext, 'seat-a'), undefined, db)).toEqual({
      ok: true,
      entityId: 'ent-a',
      slug: 'seat-a',
    })
  })

  it('a seat A key is refused for seat B (the cross-tenant forge Wave 1 allowed)', async () => {
    const a = mintWithPython('seat-a')
    const b = mintWithPython('seat-b')
    await db.prepare(a.sql).run()
    await db.prepare(b.sql).run()
    expect(await verifyMachineRequest(req(a.plaintext, 'seat-b'), undefined, db)).toEqual({
      ok: false,
      status: 401,
    })
    expect(await verifyMachineRequest(req(b.plaintext, 'seat-b'), undefined, db)).toMatchObject({
      ok: true,
      entityId: 'ent-b',
    })
  })

  it('rotation keeps the previous key live inside its TTL and drops it after', async () => {
    const first = mintWithPython('seat-a')
    await db.prepare(first.sql).run()
    const second = mintWithPython('seat-a', 1)
    await db.prepare(second.sql).run()

    expect(
      await verifyMachineRequest(req(second.plaintext, 'seat-a'), undefined, db)
    ).toMatchObject({
      ok: true,
    })
    expect(await verifyMachineRequest(req(first.plaintext, 'seat-a'), undefined, db)).toMatchObject(
      {
        ok: true,
      }
    )

    await db
      .prepare(
        "UPDATE machine_credentials SET prev_expires_at = datetime('now', '-1 minute') WHERE customer_slug = ?"
      )
      .bind('seat-a')
      .run()
    expect(await verifyMachineRequest(req(first.plaintext, 'seat-a'), undefined, db)).toEqual({
      ok: false,
      status: 401,
    })
    expect(
      await verifyMachineRequest(req(second.plaintext, 'seat-a'), undefined, db)
    ).toMatchObject({
      ok: true,
    })
  })

  it('a mint for a slug the console has not projected inserts nothing', async () => {
    const { sql } = mintWithPython('unprojected')
    await db.prepare(sql).run()
    const row = await db
      .prepare('SELECT COUNT(*) AS n FROM machine_credentials WHERE customer_slug = ?')
      .bind('unprojected')
      .first<{ n: number }>()
    expect(row?.n).toBe(0)
  })

  it('the shared key is refused for a seat that has a row, and accepted only for one that does not', async () => {
    const shared = 's'.repeat(64)
    const { sql } = mintWithPython('seat-a')
    await db.prepare(sql).run()
    expect(await verifyMachineRequest(req(shared, 'seat-a'), shared, db)).toEqual({
      ok: false,
      status: 401,
    })
    expect(await verifyMachineRequest(req(shared, 'seat-b'), shared, db)).toMatchObject({
      ok: true,
      entityId: 'ent-b',
    })
  })

  it('deleting the customer_configs row cascades the credential (decommission)', async () => {
    const { sql } = mintWithPython('seat-a')
    await db.prepare(sql).run()
    await db.prepare('DELETE FROM customer_configs WHERE customer_slug = ?').bind('seat-a').run()
    const row = await db
      .prepare('SELECT COUNT(*) AS n FROM machine_credentials WHERE customer_slug = ?')
      .bind('seat-a')
      .first<{ n: number }>()
    expect(row?.n).toBe(0)
  })
})
