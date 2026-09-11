/**
 * Behavioural tests for the SignWell integration: the API client, the send
 * flow, the completion finalizer's edge paths, the webhook route's dispatch,
 * and the admin send-for-signature route.
 *
 * Until 2026-09-11 this file matched source text (review 2026-09-10, Testing
 * 3). Two seams are faked: the network (a stubbed global fetch for the
 * SignWell API) and, in the route suites, the module boundary each route
 * calls across. Everything else is real: a migrated D1, an in-memory R2, and
 * the exported functions. The happy path of the completion finalizer (rows
 * created, milestones, portal user) lives in
 * src/lib/webhooks/signwell-handler.test.ts; the HMAC verification of the
 * webhook route in tests/webhooks/signwell-verify.test.ts.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import { createSignatureRequest, getSignedPdf } from '../src/lib/signwell/client'
import type {
  SignWellCreateDocumentRequest,
  SignWellWebhookPayload,
} from '../src/lib/signwell/types'
import { authorizeAndSendSOW, finalizeCompletedSOWSignature } from '../src/lib/sow/service'
import { createContact } from '../src/lib/db/contacts'
import { createQuote, getQuote, updateQuote } from '../src/lib/db/quotes'
import { listFollowUps } from '../src/lib/db/follow-ups'
import {
  adminSession,
  bindEnv,
  formRequest,
  jsonRequest,
  locationOf,
  locationQuery,
  memoryBucket,
  migratedDb,
  routeContext,
  seedAssessment,
  seedEntity,
  seedOrg,
  type MemoryBucket,
} from './_stubs/behavioural'

const handleDocumentCompleted = vi.fn()
vi.mock('../src/lib/webhooks/signwell-handler', () => ({
  handleDocumentCompleted: (...args: unknown[]) => handleDocumentCompleted(...args),
}))

const authorizeAndSendSOWMock = vi.fn()
vi.mock('../src/lib/sow/service', async (importOriginal) => {
  const real = await importOriginal<typeof import('../src/lib/sow/service')>()
  return {
    ...real,
    // The send route binds this name; the send-flow suite below reaches the
    // real function through `real`, so both are exercised.
    authorizeAndSendSOW: (...args: unknown[]) =>
      authorizeAndSendSOWMock.getMockImplementation()
        ? authorizeAndSendSOWMock(...args)
        : (real.authorizeAndSendSOW as (...a: unknown[]) => unknown)(...args),
  }
})

// Import AFTER the mocks so the routes bind the mocked modules.
import { POST as webhookRoute } from '../src/pages/api/webhooks/signwell'
import { POST as signRoute } from '../src/pages/api/admin/quotes/[id]/sign'

const ORG = 'org-a'
const ENT = 'ent-a'
const ASSESS = 'assess-a'
const API_KEY = 'sw_test_key'
const SECRET = 'test-signwell-webhook-secret'
const PDF_BYTES = new TextEncoder().encode('%PDF-1.7 test')

type FetchMock = ReturnType<typeof vi.fn>
let fetchMock: FetchMock

function stubFetch(handler: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  fetchMock = vi.fn(handler)
  vi.stubGlobal('fetch', fetchMock)
}

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  authorizeAndSendSOWMock.mockReset()
  handleDocumentCompleted.mockReset()
})

describe('SignWell API client', () => {
  const params: SignWellCreateDocumentRequest = {
    name: 'SOW',
    files: [{ file_base64: 'AAAA', name: 'sow.pdf' }],
    recipients: [{ id: 'r-1', name: 'Dana', email: 'dana@example.com' }],
    callback_url: 'https://smd.services/api/webhooks/signwell',
    text_tags: true,
  }

  it('createSignatureRequest posts the request as JSON to /documents with the API key header', async () => {
    stubFetch(() => jsonResponse(201, { id: 'doc-1', status: 'pending', signers: [] }))
    const doc = await createSignatureRequest(API_KEY, params)
    expect(doc.id).toBe('doc-1')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('https://www.signwell.com/api/v1/documents')
    expect(init.method).toBe('POST')
    expect(init.headers).toEqual({ 'Content-Type': 'application/json', 'X-Api-Key': API_KEY })
    expect(JSON.parse(String(init.body))).toEqual(params)
  })

  it('createSignatureRequest throws with the status and the body when SignWell refuses', async () => {
    stubFetch(() => new Response('{"error":"bad recipient"}', { status: 422 }))
    await expect(createSignatureRequest(API_KEY, params)).rejects.toThrow(
      'SignWell createSignatureRequest failed (422): {"error":"bad recipient"}'
    )
  })

  it('getSignedPdf fetches /documents/:id/completed_pdf with the key and returns the bytes', async () => {
    stubFetch(() => new Response(PDF_BYTES, { status: 200 }))
    const bytes = await getSignedPdf(API_KEY, 'doc-1')
    expect(bytes).toBeInstanceOf(Uint8Array)
    expect(new TextDecoder().decode(bytes)).toBe('%PDF-1.7 test')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('https://www.signwell.com/api/v1/documents/doc-1/completed_pdf')
    expect(init.method).toBe('GET')
    expect(init.headers).toEqual({ 'X-Api-Key': API_KEY })
  })

  it('getSignedPdf throws with the status when the document is not ready', async () => {
    stubFetch(() => new Response('not completed', { status: 409 }))
    await expect(getSignedPdf(API_KEY, 'doc-1')).rejects.toThrow(
      'SignWell getSignedPdf failed (409): not completed'
    )
  })

  it('the webhook payload type carries the completion event and the signer timestamps (compile-time contract)', () => {
    const payload: SignWellWebhookPayload = {
      event: { type: 'document_completed', time: 1, hash: 'h' },
      data: {
        object: {
          id: 'doc-1',
          name: 'SOW',
          status: 'completed',
          signers: [{ id: 's', name: 'Dana', email: 'd@example.com', signed_at: null }],
          completed_at: null,
        },
        account_id: 'acct',
      },
    }
    expect(JSON.parse(JSON.stringify(payload))).toEqual(payload)
  })
})

// ---------------------------------------------------------------------------
// Send flow and finalizer, against real rows
// ---------------------------------------------------------------------------

interface SeededQuote {
  quoteId: string
  contactId: string
  revisionId: string
  unsignedKey: string
}

async function seedSendableQuote(db: D1Database, storage: MemoryBucket): Promise<SeededQuote> {
  await seedOrg(db, ORG)
  await seedEntity(db, { id: ENT, orgId: ORG, stage: 'proposing', name: 'Alpha Plumbing' })
  await seedAssessment(db, { id: ASSESS, orgId: ORG, entityId: ENT })
  const created = await createQuote(db, ORG, {
    entityId: ENT,
    assessmentId: ASSESS,
    lineItems: [{ problem: 'intake', description: 'Rebuild the intake form', estimated_hours: 10 }],
    rate: 175,
  })
  const quote = (await updateQuote(db, ORG, created.id, {
    schedule: [{ label: 'Week 1', body: 'We shadow the desk.' }],
    deliverables: [{ title: 'Intake form', body: 'One form, one queue.' }],
  }))!
  const contact = await createContact(db, ORG, ENT, {
    name: 'Dana Reyes',
    email: 'dana@example.com',
    title: 'Owner',
  })
  const revisionId = 'rev-1'
  const unsignedKey = `orgs/${ORG}/quotes/${quote.id}/sow/${revisionId}/unsigned.pdf`
  await storage.bucket.put(unsignedKey, PDF_BYTES)
  await db
    .prepare(
      `INSERT INTO sow_revisions (id, org_id, quote_id, quote_version, sow_number, status, unsigned_storage_key, checksum_sha256, rendered_by, rendered_at)
       VALUES (?, ?, ?, ?, 'SOW-202609-001', 'rendered', ?, 'abc', 'admin', '2026-09-01T00:00:00Z')`
    )
    .bind(revisionId, ORG, quote.id, quote.version, unsignedKey)
    .run()
  return { quoteId: quote.id, contactId: contact.id, revisionId, unsignedKey }
}

function sendArgs(
  db: D1Database,
  storage: MemoryBucket,
  seeded: SeededQuote,
  quote: NonNullable<Awaited<ReturnType<typeof getQuote>>>
) {
  return {
    db,
    storage: storage.bucket,
    apiKey: API_KEY,
    orgId: ORG,
    actorId: 'admin-1',
    quote,
    entityName: 'Alpha Plumbing',
    signer: {
      contactId: seeded.contactId,
      name: 'Dana Reyes',
      email: 'dana@example.com',
      title: 'Owner',
    },
    callbackBaseEnv: { APP_BASE_URL: 'https://smd.services' } as unknown as Parameters<
      typeof authorizeAndSendSOW
    >[0]['callbackBaseEnv'],
  }
}

describe('authorizeAndSendSOW against real rows', () => {
  let db: D1Database
  let storage: MemoryBucket
  let seeded: SeededQuote

  beforeEach(async () => {
    db = await migratedDb()
    storage = memoryBucket()
    seeded = await seedSendableQuote(db, storage)
  })

  it('sends the rendered PDF with text tags, no coordinate fields, and the webhook callback; then records the authorization, the request, and the send', async () => {
    stubFetch(() => jsonResponse(201, { id: 'sw-doc-1', status: 'pending', signers: [] }))
    const quote = (await getQuote(db, ORG, seeded.quoteId))!
    const before = Date.now()
    const request = await authorizeAndSendSOW(sendArgs(db, storage, seeded, quote))

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(String(init.body)) as SignWellCreateDocumentRequest
    expect(body.text_tags).toBe(true)
    expect(body.fields).toBeUndefined()
    expect(body.callback_url).toBe('https://smd.services/api/webhooks/signwell')
    expect(body.recipients).toEqual([
      { id: expect.any(String), name: 'Dana Reyes', email: 'dana@example.com' },
    ])
    expect(body.files[0].file_base64).toBe(btoa(String.fromCharCode(...PDF_BYTES)))
    expect(body.draft).toBe(false)

    expect(request).toMatchObject({
      status: 'sent',
      provider: 'signwell',
      provider_request_id: 'sw-doc-1',
      quote_id: seeded.quoteId,
      sow_revision_id: seeded.revisionId,
    })
    expect(Date.parse(request.sent_at!)).toBeGreaterThanOrEqual(before - 1000)
    expect(JSON.parse(request.signer_snapshot_json)).toEqual({
      contactId: seeded.contactId,
      name: 'Dana Reyes',
      email: 'dana@example.com',
      title: 'Owner',
    })

    const authorization = await db
      .prepare(
        'SELECT signer_contact_id, authorized_by FROM sow_send_authorizations WHERE quote_id = ?'
      )
      .bind(seeded.quoteId)
      .first()
    expect(authorization).toEqual({ signer_contact_id: seeded.contactId, authorized_by: 'admin-1' })
    const revision = await db
      .prepare('SELECT status FROM sow_revisions WHERE id = ?')
      .bind(seeded.revisionId)
      .first<{ status: string }>()
    expect(revision?.status).toBe('sent')
    const sentQuote = (await getQuote(db, ORG, seeded.quoteId))!
    expect(sentQuote.status).toBe('sent')
    expect(sentQuote.sent_at).toBe(request.sent_at)
    expect(Date.parse(sentQuote.expires_at!) - Date.parse(sentQuote.sent_at!)).toBe(
      5 * 24 * 60 * 60 * 1000
    )
  })

  it('a second send while the first request is open is refused', async () => {
    stubFetch(() => jsonResponse(201, { id: 'sw-doc-1', status: 'pending', signers: [] }))
    const quote = (await getQuote(db, ORG, seeded.quoteId))!
    await authorizeAndSendSOW(sendArgs(db, storage, seeded, quote))
    await expect(
      authorizeAndSendSOW(sendArgs(db, storage, seeded, (await getQuote(db, ORG, seeded.quoteId))!))
    ).rejects.toThrow('SOW already sent for signature.')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('when SignWell refuses, a send_failed request row keeps the reason and the quote stays a draft', async () => {
    stubFetch(() => new Response('upstream broke', { status: 500 }))
    const quote = (await getQuote(db, ORG, seeded.quoteId))!
    await expect(authorizeAndSendSOW(sendArgs(db, storage, seeded, quote))).rejects.toThrow(
      'SignWell createSignatureRequest failed (500)'
    )
    const rows = await db
      .prepare('SELECT status, failure_reason, provider_request_id FROM signature_requests')
      .all<{ status: string; failure_reason: string | null; provider_request_id: string | null }>()
    expect(rows.results).toEqual([
      {
        status: 'send_failed',
        failure_reason: 'SignWell createSignatureRequest failed (500): upstream broke',
        provider_request_id: null,
      },
    ])
    expect((await getQuote(db, ORG, seeded.quoteId))?.status).toBe('draft')
  })

  it('refuses to send without a rendered revision, or with one rendered for an older quote version', async () => {
    stubFetch(() => jsonResponse(201, { id: 'sw-doc-1', status: 'pending', signers: [] }))
    await updateQuote(db, ORG, seeded.quoteId, { rate: 200 })
    const stale = (await getQuote(db, ORG, seeded.quoteId))!
    await expect(authorizeAndSendSOW(sendArgs(db, storage, seeded, stale))).rejects.toThrow(
      'The latest SOW revision is stale.'
    )
    await db.prepare('DELETE FROM sow_revisions').run()
    await expect(authorizeAndSendSOW(sendArgs(db, storage, seeded, stale))).rejects.toThrow(
      'Generate a SOW PDF first.'
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe('finalizeCompletedSOWSignature: the paths the happy-path suite does not cover', () => {
  let db: D1Database
  let storage: MemoryBucket
  let seeded: SeededQuote

  const payloadFor = (providerRequestId: string): SignWellWebhookPayload => ({
    event: { type: 'document_completed', time: 1, hash: 'h' },
    data: {
      object: { id: providerRequestId, name: 'SOW', status: 'completed', completed_at: null },
      account_id: 'acct',
    },
  })

  const finalize = (providerRequestId: string, bucket = storage.bucket) =>
    finalizeCompletedSOWSignature({
      db,
      storage: bucket,
      apiKey: API_KEY,
      resendApiKey: undefined,
      stripeApiKey: undefined,
      appBaseUrl: undefined,
      payload: payloadFor(providerRequestId),
    })

  beforeEach(async () => {
    db = await migratedDb()
    storage = memoryBucket()
    seeded = await seedSendableQuote(db, storage)
    stubFetch((url) =>
      url.endsWith('/completed_pdf')
        ? new Response(PDF_BYTES, { status: 200 })
        : jsonResponse(201, { id: 'sw-doc-1', status: 'pending', signers: [] })
    )
    await authorizeAndSendSOW(
      sendArgs(db, storage, seeded, (await getQuote(db, ORG, seeded.quoteId))!)
    )
  })

  it('a document SignWell knows and we do not is acknowledged (200) and changes nothing', async () => {
    const res = await finalize('sw-unknown')
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ ok: true })
    expect((await getQuote(db, ORG, seeded.quoteId))?.status).toBe('sent')
  })

  it('when the signed artifact cannot be persisted, the webhook gets a 500 so SignWell retries, and the quote is not accepted', async () => {
    const failing = {
      ...storage.bucket,
      get: storage.bucket.get.bind(storage.bucket),
      put: () => Promise.reject(new Error('R2 unavailable')),
    } as unknown as MemoryBucket['bucket']
    const res = await finalize('sw-doc-1', failing)
    expect(res.status).toBe(500)
    expect(await res.json()).toEqual({ error: 'INTERNAL_ERROR' })
    expect((await getQuote(db, ORG, seeded.quoteId))?.status).toBe('sent')
    expect(
      await db.prepare('SELECT COUNT(*) AS c FROM engagements').first<{ c: number }>()
    ).toEqual({
      c: 0,
    })
  })

  it('a completed signature is persisted at the revisioned signed key and the quote is accepted', async () => {
    const res = await finalize('sw-doc-1')
    expect(res.status).toBe(200)
    const signedKey = `orgs/${ORG}/quotes/${seeded.quoteId}/sow/${seeded.revisionId}/signed.pdf`
    expect(storage.store.has(signedKey)).toBe(true)
    expect((await getQuote(db, ORG, seeded.quoteId))?.status).toBe('accepted')
    const request = await db
      .prepare(
        'SELECT status, signed_storage_key FROM signature_requests WHERE provider_request_id = ?'
      )
      .bind('sw-doc-1')
      .first()
    expect(request).toEqual({ status: 'completed', signed_storage_key: signedKey })

    // A replay of the same webhook is acknowledged and does not run again.
    const replay = await finalize('sw-doc-1')
    expect(replay.status).toBe(200)
    expect(
      await db.prepare('SELECT COUNT(*) AS c FROM engagements').first<{ c: number }>()
    ).toEqual({
      c: 1,
    })
  })
})

// ---------------------------------------------------------------------------
// Webhook route dispatch
// ---------------------------------------------------------------------------

async function signWellHash(type: string, time: number, secret: string): Promise<string> {
  const enc = new TextEncoder()
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  )
  const mac = await crypto.subtle.sign('HMAC', key, enc.encode(`${type}@${time}`))
  return Array.from(new Uint8Array(mac))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

describe('POST /api/webhooks/signwell: dispatch after verification', () => {
  const callWebhook = async (type: string) => {
    const time = Math.floor(Date.now() / 1000)
    const body = {
      event: { type, time, hash: await signWellHash(type, time, SECRET) },
      data: {
        object: {
          id: 'sw-doc-1',
          name: 'SOW',
          status: type.replace('document_', ''),
          completed_at: null,
        },
        account_id: 'acct',
      },
    }
    return webhookRoute(
      routeContext({
        request: jsonRequest('https://smd.services/api/webhooks/signwell', body),
      }) as unknown as Parameters<typeof webhookRoute>[0]
    )
  }

  beforeEach(() => {
    handleDocumentCompleted.mockResolvedValue(new Response('{"ok":true}', { status: 200 }))
    bindEnv({
      SIGNWELL_WEBHOOK_SECRET: SECRET,
      SIGNWELL_API_KEY: API_KEY,
      DB: { fake: 'db' },
      STORAGE: { fake: 'r2' },
      RESEND_API_KEY: 're_test',
      STRIPE_API_KEY: 'sk_test',
      APP_BASE_URL: 'https://smd.services',
    })
  })

  it('acknowledges every event other than completion with 200 and does not touch the handler', async () => {
    for (const type of ['document_viewed', 'document_signed', 'document_declined']) {
      const res = await callWebhook(type)
      expect(res.status, type).toBe(200)
      expect(await res.json()).toEqual({ ok: true, event: type })
    }
    expect(handleDocumentCompleted).not.toHaveBeenCalled()
  })

  it('hands a verified document_completed event to the handler with the env bindings and the parsed payload', async () => {
    const res = await callWebhook('document_completed')
    expect(res.status).toBe(200)
    expect(handleDocumentCompleted).toHaveBeenCalledTimes(1)
    const [config, payload] = handleDocumentCompleted.mock.calls[0] as [
      Record<string, unknown>,
      SignWellWebhookPayload,
    ]
    expect(config).toEqual({
      db: { fake: 'db' },
      storage: { fake: 'r2' },
      apiKey: API_KEY,
      resendApiKey: 're_test',
      stripeApiKey: 'sk_test',
      appBaseUrl: 'https://smd.services',
    })
    expect(payload.event.type).toBe('document_completed')
    expect(payload.data.object.id).toBe('sw-doc-1')
  })

  it('a completion with no SignWell API key configured is a 500, not a silent ack', async () => {
    bindEnv({ SIGNWELL_WEBHOOK_SECRET: SECRET })
    const res = await callWebhook('document_completed')
    expect(res.status).toBe(500)
    expect(await res.json()).toMatchObject({ error: 'server_misconfigured' })
    expect(handleDocumentCompleted).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Admin send-for-signature route
// ---------------------------------------------------------------------------

describe('POST /api/admin/quotes/[id]/sign', () => {
  let db: D1Database
  let storage: MemoryBucket
  let seeded: SeededQuote

  const call = (
    quoteId: string,
    fields: Record<string, string>,
    session: ReturnType<typeof adminSession> | null = adminSession(ORG)
  ) =>
    signRoute(
      routeContext({
        request: formRequest(`http://test.local/api/admin/quotes/${quoteId}/sign`, fields),
        params: { id: quoteId },
        session,
      }) as unknown as Parameters<typeof signRoute>[0]
    )

  beforeEach(async () => {
    db = await migratedDb()
    storage = memoryBucket()
    seeded = await seedSendableQuote(db, storage)
    authorizeAndSendSOWMock.mockImplementation(async () => ({
      id: 'sig-1',
      sent_at: '2026-09-01T15:00:00.000Z',
    }))
    bindEnv({
      DB: db,
      STORAGE: storage.bucket,
      SIGNWELL_API_KEY: API_KEY,
      APP_BASE_URL: 'https://smd.services',
    })
  })

  it('answers 401 with no admin session; with no API key it bails to error=server', async () => {
    expect((await call(seeded.quoteId, { signer_contact_id: seeded.contactId }, null)).status).toBe(
      401
    )
    bindEnv({ DB: db, STORAGE: storage.bucket })
    expect(locationOf(await call(seeded.quoteId, { signer_contact_id: seeded.contactId }))).toBe(
      '/admin/entities?error=server'
    )
    expect(authorizeAndSendSOWMock).not.toHaveBeenCalled()
  })

  it('names each precondition: unknown quote, a quote past sending, no signer, a signer without an email', async () => {
    expect(locationOf(await call('nope', { signer_contact_id: seeded.contactId }))).toBe(
      '/admin/entities?error=not_found'
    )

    await db
      .prepare("UPDATE quotes SET status = 'accepted' WHERE id = ?")
      .bind(seeded.quoteId)
      .run()
    expect(
      locationQuery(await call(seeded.quoteId, { signer_contact_id: seeded.contactId })).get(
        'error'
      )
    ).toBe('invalid_transition')
    await db.prepare("UPDATE quotes SET status = 'draft' WHERE id = ?").bind(seeded.quoteId).run()

    expect(locationQuery(await call(seeded.quoteId, {})).get('error')).toBe('no_contact_email')
    const silent = await createContact(db, ORG, ENT, { name: 'No Email' })
    expect(
      locationQuery(await call(seeded.quoteId, { signer_contact_id: silent.id })).get('error')
    ).toBe('no_contact_email')
    expect(authorizeAndSendSOWMock).not.toHaveBeenCalled()
  })

  it('sends through the SOW service with the chosen signer, schedules the proposal cadence from sent_at, and returns to the quote', async () => {
    const res = await call(seeded.quoteId, { signer_contact_id: seeded.contactId })
    expect(locationOf(res)).toBe(`/admin/entities/${ENT}/quotes/${seeded.quoteId}?saved=1`)

    expect(authorizeAndSendSOWMock).toHaveBeenCalledTimes(1)
    const [args] = authorizeAndSendSOWMock.mock.calls[0] as [Record<string, unknown>]
    expect(args).toMatchObject({
      apiKey: API_KEY,
      orgId: ORG,
      actorId: `admin-${ORG}`,
      entityName: 'Alpha Plumbing',
      signer: {
        contactId: seeded.contactId,
        name: 'Dana Reyes',
        email: 'dana@example.com',
        title: 'Owner',
      },
    })
    expect((args.quote as { id: string }).id).toBe(seeded.quoteId)

    const cadence = await listFollowUps(db, ORG)
    expect(cadence.map((f) => [f.type, f.quote_id])).toEqual([
      ['proposal_day2', seeded.quoteId],
      ['proposal_day5', seeded.quoteId],
      ['proposal_day7', seeded.quoteId],
    ])
  })

  it('a service failure lands on the entity list with the reason, and schedules nothing', async () => {
    authorizeAndSendSOWMock.mockImplementation(async () => {
      throw new Error('SOW PDF not found in storage')
    })
    const res = await call(seeded.quoteId, { signer_contact_id: seeded.contactId })
    expect(locationQuery(res).get('error')).toBe('server')
    expect(locationQuery(res).get('detail')).toBe('SOW PDF not found in storage')
    expect(await listFollowUps(db, ORG)).toEqual([])
  })
})
