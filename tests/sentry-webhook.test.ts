/**
 * POST /api/webhooks/sentry.
 *
 * Two contracts. The replay window is a refusal, not a skip: Sentry signs the
 * raw body only, so the timestamp header is the whole replay defence (2026-09-09
 * review, Security LOW 5). And a verified delivery about a broken Operator is
 * never refused for its shape: from 2026-09-14 to 2026-10-01 all 19 deliveries
 * were refused `missing_tenant_tag` because the integration sends `issue`
 * payloads, which carry no tags, and no Operator error reached anyone. These
 * tests run the handler against a real migrated D1 so the row the fleet-alerts
 * Worker emails is the row asserted on.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
  installWorkerdPolyfills,
} from '@venturecrane/crane-test-harness'
import path from 'node:path'
import { env as testEnv } from 'cloudflare:workers'
import { POST, interpretDelivery, extractTenantTag } from '../src/pages/api/webhooks/sentry'

installWorkerdPolyfills()

const migrationsDir = path.resolve(__dirname, '../migrations')
const SECRET = 'sentry-webhook-secret-for-tests'
const ORG = 'org-sentry'

async function sign(body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(body))
  return Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

const fresh = () => String(Math.floor(Date.now() / 1000))

async function post(body: unknown, headers: Record<string, string> = {}): Promise<Response> {
  const raw = JSON.stringify(body)
  const request = new Request('https://smd.services/api/webhooks/sentry', {
    method: 'POST',
    headers: { 'sentry-hook-signature': await sign(raw), ...headers },
    body: raw,
  })
  return POST({ request } as unknown as Parameters<typeof POST>[0])
}

async function seedSeat(db: D1Database, entity: string, slug: string): Promise<void> {
  await db
    .prepare(
      `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'ongoing', datetime('now'), datetime('now'), datetime('now'))`
    )
    .bind(entity, ORG, slug, slug)
    .run()
  await db
    .prepare(
      `INSERT INTO customer_configs
         (entity_id, org_id, customer_slug, schema_version, personas_json, git_sha, synced_at)
       VALUES (?, ?, ?, '1.0.0', '[]', 'sha', '2026-10-05T00:00:00Z')`
    )
    .bind(entity, ORG, slug)
    .run()
}

const eventAlert = (tags: [string, string][], issueId = '7001') => ({
  action: 'triggered',
  data: {
    event: { issue_id: issueId, title: 'RuntimeError: boom', tags, web_url: 'https://sentry.io/x' },
    triggered_rule: 'SMD ops console',
  },
})

const issuePayload = (action: string, id = '8001') => ({
  action,
  data: { issue: { id, shortId: 'SMD-OPERATOR-9', title: 'TimedOut: Timed out' } },
})

interface SinkRow {
  customer_slug: string
  driver: string
  summary: string
  notified_at: string | null
}

async function rows(db: D1Database): Promise<SinkRow[]> {
  const res = await db
    .prepare(
      `SELECT customer_slug, driver, summary, notified_at FROM cost_anomaly_alerts
        WHERE source = 'sentry' ORDER BY driver`
    )
    .all<SinkRow>()
  return res.results ?? []
}

describe('sentry webhook replay window', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    Object.assign(testEnv, { SENTRY_WEBHOOK_SECRET: SECRET, DB: db })
  })
  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  it('refuses a validly signed body with no timestamp header', async () => {
    const res = await post(eventAlert([]))
    expect(res.status).toBe(401)
    expect(await res.json()).toMatchObject({ error: 'invalid_timestamp' })
  })

  it('refuses a validly signed body with a non-numeric timestamp', async () => {
    const res = await post(eventAlert([]), { 'sentry-hook-timestamp': 'abc' })
    expect(res.status).toBe(401)
    expect(await res.json()).toMatchObject({ error: 'invalid_timestamp' })
  })

  it('refuses a validly signed body older than the window', async () => {
    const old = String(Math.floor(Date.now() / 1000) - 3600)
    const res = await post(eventAlert([]), { 'sentry-hook-timestamp': old })
    expect(res.status).toBe(401)
    expect(await res.json()).toMatchObject({ error: 'stale' })
  })

  it('refuses a body whose signature does not match', async () => {
    const raw = JSON.stringify(eventAlert([]))
    const request = new Request('https://smd.services/api/webhooks/sentry', {
      method: 'POST',
      headers: { 'sentry-hook-signature': 'ab'.repeat(32), 'sentry-hook-timestamp': fresh() },
      body: raw,
    })
    const res = await POST({ request } as unknown as Parameters<typeof POST>[0])
    expect(res.status).toBe(401)
  })

  it('accepts the unsigned settings submission Sentry sends when a rule is saved, writing nothing', async () => {
    const request = new Request('https://smd.services/api/webhooks/sentry', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ fields: [{ name: 'note', value: 'Global Operator alert' }] }),
    })
    const res = await POST({ request } as unknown as Parameters<typeof POST>[0])
    expect(res.status).toBe(200)
    const count = await db
      .prepare('SELECT COUNT(*) AS n FROM cost_anomaly_alerts')
      .first<{ n: number }>()
    expect(count?.n).toBe(0)
  })

  it('still refuses an unsigned body that is not a settings form', async () => {
    const request = new Request('https://smd.services/api/webhooks/sentry', {
      method: 'POST',
      body: JSON.stringify(eventAlert([['tenant', 'scott']])),
    })
    const res = await POST({ request } as unknown as Parameters<typeof POST>[0])
    expect(res.status).toBe(401)
  })
})

describe('sentry webhook pages every Operator error', () => {
  let db: D1Database
  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
    await db
      .prepare(
        `INSERT INTO organizations (id, name, slug, created_at, updated_at)
         VALUES (?, 'SMD Test Org', 'smd-sentry-test-org', datetime('now'), datetime('now'))`
      )
      .bind(ORG)
      .run()
    await seedSeat(db, 'ent-smd', 'scott')
    await seedSeat(db, 'ent-firm', 'ashton-price')
    Object.assign(testEnv, { SENTRY_WEBHOOK_SECRET: SECRET, DB: db })
  })
  afterEach(() => {
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
  })

  const alertHeaders = () => ({
    'sentry-hook-timestamp': fresh(),
    'sentry-hook-resource': 'event_alert',
  })
  const issueHeaders = () => ({ 'sentry-hook-timestamp': fresh(), 'sentry-hook-resource': 'issue' })

  it('attributes an alert-rule delivery to the tenant it names', async () => {
    const res = await post(eventAlert([['tenant', 'ashton-price']]), alertHeaders())
    expect(res.status).toBe(200)
    expect(await rows(db)).toEqual([
      expect.objectContaining({
        customer_slug: 'ashton-price',
        driver: 'sentry:7001',
        notified_at: null,
      }),
    ])
  })

  it('pages an untagged error as fleet instead of refusing it', async () => {
    const res = await post(eventAlert([['level', 'error']]), alertHeaders())
    expect(res.status).toBe(200)
    expect((await rows(db))[0]).toMatchObject({ customer_slug: 'fleet', driver: 'sentry:7001' })
  })

  it('pages a tenant the console does not know as fleet', async () => {
    const res = await post(eventAlert([['tenant', 'retired-seat']]), alertHeaders())
    expect(res.status).toBe(200)
    expect((await rows(db))[0]).toMatchObject({ customer_slug: 'fleet' })
  })

  it('pages an issue-resource creation as fleet (the 2026-10-01 shape)', async () => {
    const res = await post(issuePayload('created'), issueHeaders())
    expect(res.status).toBe(200)
    const [row] = await rows(db)
    expect(row).toMatchObject({ customer_slug: 'fleet', driver: 'sentry:8001' })
    expect(row.summary).toContain('SMD-OPERATOR-9')
  })

  it('acknowledges and drops issue actions that are not breakage', async () => {
    for (const action of ['resolved', 'assigned', 'archived']) {
      const res = await post(issuePayload(action), issueHeaders())
      expect(res.status).toBe(200)
    }
    expect(await rows(db)).toEqual([])
  })

  it('drops issue deliveries once the alert rule owns paging', async () => {
    Object.assign(testEnv, { SENTRY_ISSUE_RESOURCE: 'ignore' })
    const res = await post(issuePayload('created'), issueHeaders())
    expect(res.status).toBe(200)
    expect(await rows(db)).toEqual([])
  })

  it('re-pages an issue emailed hours ago that fires again', async () => {
    await post(eventAlert([['tenant', 'scott']]), alertHeaders())
    await db
      .prepare(`UPDATE cost_anomaly_alerts SET notified_at = datetime('now', '-7 hours')`)
      .run()
    await post(eventAlert([['tenant', 'scott']]), alertHeaders())
    const all = await rows(db)
    expect(all).toHaveLength(1)
    expect(all[0].notified_at).toBeNull()
  })

  it('does not re-page a noisy issue inside the re-page window', async () => {
    await post(eventAlert([['tenant', 'scott']]), alertHeaders())
    await db.prepare(`UPDATE cost_anomaly_alerts SET notified_at = datetime('now')`).run()
    await post(eventAlert([['tenant', 'scott']]), alertHeaders())
    expect((await rows(db))[0].notified_at).not.toBeNull()
  })

  it('never re-pages an issue someone acknowledged', async () => {
    await post(eventAlert([['tenant', 'scott']]), alertHeaders())
    await db
      .prepare(
        `UPDATE cost_anomaly_alerts SET notified_at = datetime('now', '-7 hours'),
                acknowledged_at = datetime('now')`
      )
      .run()
    await post(eventAlert([['tenant', 'scott']]), alertHeaders())
    expect((await rows(db))[0].notified_at).not.toBeNull()
  })

  it('keeps two different issues on one seat on one day as two rows', async () => {
    await post(eventAlert([['tenant', 'scott']], '1'), alertHeaders())
    await post(eventAlert([['tenant', 'scott']], '2'), alertHeaders())
    expect((await rows(db)).map((r) => r.driver)).toEqual(['sentry:1', 'sentry:2'])
  })

  it('fails loudly when the fleet owner seat is missing', async () => {
    await db.prepare(`DELETE FROM customer_configs WHERE customer_slug = 'scott'`).run()
    const res = await post(eventAlert([]), alertHeaders())
    expect(res.status).toBe(500)
    expect(await rows(db)).toEqual([])
  })
})

describe('interpretDelivery', () => {
  it('reads no tenant from the placeholder the SDK uses when the slug is unset', () => {
    expect(extractTenantTag({ tags: [['tenant', 'unknown']] })).toBeNull()
  })
  it('ignores unknown resources', () => {
    expect(interpretDelivery('installation', { action: 'created', data: {} }, 'page')).toBeNull()
  })
})
