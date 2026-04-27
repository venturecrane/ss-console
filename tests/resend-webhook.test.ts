/**
 * Route-level tests for POST /api/webhooks/resend.
 *
 * Exercises the Svix signature verification + dispatch path end-to-end
 * with a real D1 instance and the same env-injection pattern used by
 * other API-route tests (tests/_stubs/cloudflare-workers.ts).
 *
 * Generates valid Svix signatures via Node's crypto so we can assert
 * acceptance on a correctly-signed body and rejection on a wrong-secret
 * signature, stale timestamp, or replay.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import { createHmac, randomUUID } from 'node:crypto'
import type { D1Database } from '@cloudflare/workers-types'
import { env as testEnv } from 'cloudflare:workers'

import { POST } from '../src/pages/api/webhooks/resend'
import { recordEvent } from '../src/lib/db/outreach-events'

const migrationsDir = resolve(process.cwd(), 'migrations')

// --- Test secret. Encodes the string "test-secret-bytes" as base64 raw key.
//     Format mirrors what Resend stores: `whsec_<base64-raw-key>`.
const RAW_SECRET_BYTES = Buffer.from('test-secret-bytes-1234567890abcd')
const SECRET_BASE64 = RAW_SECRET_BYTES.toString('base64')
const WEBHOOK_SECRET = `whsec_${SECRET_BASE64}`

const ORG_ID = 'org-resend-route'
const ENTITY_ID = 'ent-resend-route'

function svixSign(svixId: string, svixTimestamp: string, body: string): string {
  const signed = `${svixId}.${svixTimestamp}.${body}`
  const sig = createHmac('sha256', RAW_SECRET_BYTES).update(signed).digest('base64')
  return `v1,${sig}`
}

function makeRequest(opts: {
  body: string
  svixId?: string
  timestamp?: string
  signature?: string
}): Request {
  const svixId = opts.svixId ?? `msg_${randomUUID()}`
  const ts = opts.timestamp ?? String(Math.floor(Date.now() / 1000))
  const sig = opts.signature ?? svixSign(svixId, ts, opts.body)
  return new Request('http://test.local/api/webhooks/resend', {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'svix-id': svixId,
      'svix-timestamp': ts,
      'svix-signature': sig,
    },
    body: opts.body,
  })
}

async function callRoute(req: Request) {
  return await POST({ request: req } as Parameters<typeof POST>[0])
}

describe('POST /api/webhooks/resend', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES (?, 'Test Org', 'test-org', datetime('now'), datetime('now'))`
      )
      .bind(ORG_ID)
      .run()

    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
         VALUES (?, ?, 'Biz', 'biz', 'signal', datetime('now'), datetime('now'), datetime('now'))`
      )
      .bind(ENTITY_ID, ORG_ID)
      .run()

    Object.assign(testEnv, {
      DB: db,
      RESEND_WEBHOOK_SECRET: WEBHOOK_SECRET,
    })
  })

  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('500s when RESEND_WEBHOOK_SECRET is unset', async () => {
    delete (testEnv as unknown as Record<string, unknown>).RESEND_WEBHOOK_SECRET
    const body = JSON.stringify({ type: 'email.opened', data: { email_id: 'm1' } })
    const res = await callRoute(makeRequest({ body }))
    expect(res.status).toBe(500)
  })

  it('400s when svix headers are missing', async () => {
    const req = new Request('http://test.local/api/webhooks/resend', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ type: 'email.opened', data: { email_id: 'm1' } }),
    })
    const res = await callRoute(req)
    expect(res.status).toBe(400)
  })

  it('401s on bad signature', async () => {
    const body = JSON.stringify({ type: 'email.opened', data: { email_id: 'm1' } })
    const ts = String(Math.floor(Date.now() / 1000))
    const req = makeRequest({
      body,
      timestamp: ts,
      signature: 'v1,' + Buffer.from('not-a-real-signature').toString('base64'),
    })
    const res = await callRoute(req)
    expect(res.status).toBe(401)
  })

  it('401s on stale timestamp', async () => {
    const body = JSON.stringify({ type: 'email.opened', data: { email_id: 'm1' } })
    const tsOld = String(Math.floor(Date.now() / 1000) - 600) // 10 min in the past
    const req = makeRequest({ body, timestamp: tsOld })
    const res = await callRoute(req)
    expect(res.status).toBe(401)
  })

  it('400s on invalid JSON even with a valid signature', async () => {
    const body = 'this is not json'
    const req = makeRequest({ body })
    const res = await callRoute(req)
    expect(res.status).toBe(400)
  })

  it('records an event and re-attributes to the entity via the sent row', async () => {
    // Pre-seed the synthetic 'sent' row that the send wrapper would write.
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'msg-route-1',
    })

    const body = JSON.stringify({
      type: 'email.opened',
      data: { email_id: 'msg-route-1' },
    })
    const res = await callRoute(makeRequest({ body }))
    expect(res.status).toBe(200)

    const json = (await res.json()) as { ok: boolean; recorded: boolean }
    expect(json.ok).toBe(true)
    expect(json.recorded).toBe(true)

    const row = await db
      .prepare(
        `SELECT entity_id, event_type FROM outreach_events
         WHERE message_id = ? AND event_type = 'open'`
      )
      .bind('msg-route-1')
      .first<{ entity_id: string; event_type: string }>()
    expect(row).not.toBeNull()
    expect(row!.entity_id).toBe(ENTITY_ID)
  })

  it('dedupes a Svix retry of the same svix-id', async () => {
    await recordEvent(db, {
      org_id: ORG_ID,
      entity_id: ENTITY_ID,
      event_type: 'sent',
      message_id: 'msg-route-2',
    })

    const body = JSON.stringify({
      type: 'email.opened',
      data: { email_id: 'msg-route-2' },
    })
    const svixId = `msg_${randomUUID()}`
    const ts = String(Math.floor(Date.now() / 1000))

    const first = await callRoute(makeRequest({ body, svixId, timestamp: ts }))
    expect(first.status).toBe(200)

    const retry = await callRoute(makeRequest({ body, svixId, timestamp: ts }))
    expect(retry.status).toBe(200)
    const retryJson = (await retry.json()) as { recorded: boolean; reason?: string }
    expect(retryJson.recorded).toBe(false)
    expect(retryJson.reason).toBe('deduped')

    const count = await db
      .prepare(
        `SELECT COUNT(*) AS n FROM outreach_events
         WHERE provider_event_id = ?`
      )
      .bind(svixId)
      .first<{ n: number }>()
    expect(count!.n).toBe(1)
  })

  it('acks unhandled event types without inserting a row', async () => {
    const body = JSON.stringify({
      type: 'email.delivery_delayed',
      data: { email_id: 'msg-route-3' },
    })
    const res = await callRoute(makeRequest({ body }))
    expect(res.status).toBe(200)
    const json = (await res.json()) as { recorded: boolean; reason?: string }
    expect(json.recorded).toBe(false)
    expect(json.reason).toBe('unhandled_event_type')
  })

  it('accepts a signature when the secret is provided WITHOUT the whsec_ prefix', async () => {
    // Replace env with the prefix-stripped secret. The route should still
    // verify because we tolerate either form.
    Object.assign(testEnv, { RESEND_WEBHOOK_SECRET: SECRET_BASE64 })

    const body = JSON.stringify({
      type: 'email.bounced',
      data: { email_id: 'msg-route-4' },
    })
    const res = await callRoute(makeRequest({ body }))
    expect(res.status).toBe(200)
  })

  it('accepts a signature when the header carries multiple v1 candidates', async () => {
    const body = JSON.stringify({
      type: 'email.opened',
      data: { email_id: 'msg-route-5' },
    })
    const svixId = `msg_${randomUUID()}`
    const ts = String(Math.floor(Date.now() / 1000))
    const validSig = svixSign(svixId, ts, body)
    const fakeSig = 'v1,' + Buffer.from('not-a-real-signature').toString('base64')
    const req = makeRequest({
      body,
      svixId,
      timestamp: ts,
      signature: `${fakeSig} ${validSig}`,
    })
    const res = await callRoute(req)
    expect(res.status).toBe(200)
  })
})
