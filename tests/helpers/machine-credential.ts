/**
 * Seed a per-seat Machine credential row for a test seat, the way
 * operator/bin/lib/machine_credential.py mints one in production: a random
 * 16-byte salt and HMAC-SHA256(salt, plaintext) as hex. The seat's
 * customer_configs row must exist first (foreign key).
 *
 * Every test that POSTs to /api/internal/heartbeat, /runtime-summary or
 * /sentry-probe against a real D1 goes through here since the shared-key
 * fallback was removed (2026-09-14): there is no other way to authenticate.
 */

import { createHmac, randomBytes } from 'node:crypto'
import type { D1Database } from '@cloudflare/workers-types'

export function machineKeyHashHex(plaintext: string, saltHex: string): string {
  return createHmac('sha256', Buffer.from(saltHex, 'hex')).update(plaintext, 'utf8').digest('hex')
}

export async function seedMachineCredential(
  db: D1Database,
  slug: string,
  plaintext: string
): Promise<void> {
  const salt = randomBytes(16).toString('hex')
  await db
    .prepare(
      `INSERT INTO machine_credentials (customer_slug, entity_id, key_hash, salt, created_at)
       SELECT customer_slug, entity_id, ?, ?, datetime('now') FROM customer_configs WHERE customer_slug = ?`
    )
    .bind(machineKeyHashHex(plaintext, salt), salt, slug)
    .run()
  const row = await db
    .prepare('SELECT 1 AS n FROM machine_credentials WHERE customer_slug = ?')
    .bind(slug)
    .first<{ n: number }>()
  if (!row) throw new Error(`seedMachineCredential: no customer_configs row for slug ${slug}`)
}
