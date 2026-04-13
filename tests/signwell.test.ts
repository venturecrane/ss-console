import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('signwell: types', () => {
  const source = () => readFileSync(resolve('src/lib/signwell/types.ts'), 'utf-8')

  it('types.ts exists', () => {
    expect(existsSync(resolve('src/lib/signwell/types.ts'))).toBe(true)
  })

  it('exports SignWellCreateDocumentRequest type', () => {
    expect(source()).toContain('export interface SignWellCreateDocumentRequest')
  })

  it('exports SignWellDocument type', () => {
    expect(source()).toContain('export interface SignWellDocument')
  })

  it('exports SignWellWebhookPayload type', () => {
    expect(source()).toContain('export interface SignWellWebhookPayload')
  })

  it('exports SignWellSigner type', () => {
    expect(source()).toContain('export interface SignWellSigner')
  })

  it('SignWellCreateDocumentRequest includes file_base64 for PDF upload', () => {
    const code = source()
    expect(code).toContain('file_base64')
  })

  it('SignWellCreateDocumentRequest includes recipients array', () => {
    const code = source()
    expect(code).toContain('recipients')
  })

  it('SignWellCreateDocumentRequest includes callback_url for webhooks', () => {
    const code = source()
    expect(code).toContain('callback_url')
  })

  it('SignWellCreateDocumentRequest includes fields array', () => {
    const code = source()
    expect(code).toContain('fields')
  })

  it('SignWellWebhookPayload includes document_completed event', () => {
    const code = source()
    expect(code).toContain('document_completed')
  })

  it('SignWellSigner includes signed_at field', () => {
    const code = source()
    expect(code).toContain('signed_at')
  })
})

describe('signwell: API client', () => {
  const source = () => readFileSync(resolve('src/lib/signwell/client.ts'), 'utf-8')

  it('client.ts exists', () => {
    expect(existsSync(resolve('src/lib/signwell/client.ts'))).toBe(true)
  })

  it('exports createSignatureRequest function', () => {
    expect(source()).toContain('export async function createSignatureRequest')
  })

  it('exports getDocument function', () => {
    expect(source()).toContain('export async function getDocument')
  })

  it('exports getSignedPdf function', () => {
    expect(source()).toContain('export async function getSignedPdf')
  })

  it('uses correct API base URL', () => {
    expect(source()).toContain('https://www.signwell.com/api/v1')
  })

  it('createSignatureRequest calls POST /documents', () => {
    const code = source()
    expect(code).toContain('/documents')
    expect(code).toContain("method: 'POST'")
  })

  it('getDocument calls GET /documents/:id', () => {
    const code = source()
    expect(code).toContain('`${SIGNWELL_API_BASE}/documents/${docId}`')
    expect(code).toContain("method: 'GET'")
  })

  it('getSignedPdf calls GET /documents/:id/completed_pdf', () => {
    expect(source()).toContain('/completed_pdf')
  })

  it('sends API key via X-Api-Key header', () => {
    const code = source()
    expect(code).toContain("'X-Api-Key': apiKey")
  })

  it('sends Content-Type: application/json for POST requests', () => {
    expect(source()).toContain("'Content-Type': 'application/json'")
  })

  it('throws descriptive errors on non-ok responses', () => {
    const code = source()
    expect(code).toContain('response.ok')
    expect(code).toContain('throw new Error')
  })

  it('uses raw fetch — no SDK dependency', () => {
    const code = source()
    expect(code).toContain('await fetch(')
    expect(code).not.toContain('import { SignWell')
    expect(code).not.toContain("from 'signwell'")
  })
})

describe('signwell: webhook handler', () => {
  const source = () => readFileSync(resolve('src/lib/webhooks/signwell-handler.ts'), 'utf-8')

  it('signwell-handler.ts exists', () => {
    expect(existsSync(resolve('src/lib/webhooks/signwell-handler.ts'))).toBe(true)
  })

  it('exports handleDocumentCompleted function', () => {
    expect(source()).toContain('export async function handleDocumentCompleted')
  })

  it('delegates completion handling to the SOW finalization service', () => {
    const code = source()
    expect(code).toContain('finalizeCompletedSOWSignature')
    expect(code).toContain('return finalizeCompletedSOWSignature')
  })

  it('keeps the wrapper free of persistence logic', () => {
    const code = source()
    expect(code).not.toContain('await db.batch(')
    expect(code).not.toContain('INSERT INTO engagements')
  })
})

describe('signwell: sow lifecycle service', () => {
  const source = () => readFileSync(resolve('src/lib/sow/service.ts'), 'utf-8')

  it('looks up requests by provider_request_id', () => {
    const code = source()
    expect(code).toContain('getSignatureRequestByProviderRequestId')
    expect(code).toContain('providerRequestId')
  })

  it('acknowledges unknown provider request ids as non-retryable', () => {
    const code = source()
    expect(code).toContain('unknownDocumentResponse')
    expect(code).toContain('Unknown SignWell document')
  })

  it('claims sent requests before artifact persistence to avoid duplicate finalization', () => {
    const code = source()
    expect(code).toContain("status = 'completed_pending_artifact'")
    expect(code).toContain("status = 'sent'")
    expect(code).toContain('claimResult.meta?.changes')
  })

  it('generates UUIDs for engagement and invoice BEFORE the batch', () => {
    const code = source()
    const engagementIdIdx = code.indexOf('const engagementId = crypto.randomUUID()')
    const invoiceIdIdx = code.indexOf('const invoiceId = crypto.randomUUID()')
    const batchIdx = code.indexOf('await db.batch([', engagementIdIdx)
    expect(engagementIdIdx).toBeGreaterThan(-1)
    expect(invoiceIdIdx).toBeGreaterThan(-1)
    expect(batchIdx).toBeGreaterThan(-1)
    expect(engagementIdIdx).toBeLessThan(batchIdx)
    expect(invoiceIdIdx).toBeLessThan(batchIdx)
  })

  it('uses db.batch() for atomic multi-table writes', () => {
    expect(source()).toContain('await db.batch(')
  })

  it('batch updates quote status to accepted', () => {
    const code = source()
    expect(code).toContain("status = 'accepted'")
    expect(code).toContain('accepted_at')
  })

  it('batch updates entity stage to engaged', () => {
    const code = source()
    expect(code).toContain('UPDATE entities')
    expect(code).toContain("SET stage = 'engaged'")
  })

  it('batch creates engagement record', () => {
    const code = source()
    expect(code).toContain('INSERT INTO engagements')
    expect(code).toContain('engagementId')
  })

  it('batch creates deposit invoice with draft status', () => {
    const code = source()
    expect(code).toContain('INSERT INTO invoices')
    expect(code).toContain("'deposit'")
    expect(code).toContain("'draft'")
  })

  it('persists the signed artifact before batch finalization and runs outbox jobs afterward', () => {
    const code = source()
    expect(code).toContain('uploadSignedSowRevisionPdf')
    expect(code).toContain('processOutboxJobsForSignatureRequest')
  })

  it('returns 500 when artifact persistence or finalization fails', () => {
    const code = source()
    expect(code).toContain('Failed to persist signed artifact')
    expect(code).toContain('Finalization batch failed')
    expect(code).toContain('status: 500')
  })

  it('uploads signed PDF to a revisioned signed artifact key', () => {
    const code = source()
    expect(code).toContain('getSowRevisionSignedKey')
    expect(code).toContain('uploadSignedSowRevisionPdf')
  })

  it('persists signed artifact state on lifecycle tables during finalization', () => {
    const code = source()
    expect(code).toContain("SET status = 'completed', signed_storage_key = ?")
    expect(code).toContain("SET status = 'signed', signed_storage_key = ?")
    expect(code).toContain("SET status = 'accepted'")
  })

  it('enqueues outbox jobs for signed email and deposit invoice', () => {
    const code = source()
    expect(code).toContain('send_sow_signed_email')
    expect(code).toContain('send_deposit_invoice')
  })

  it('uses parameterized queries with .bind()', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // No template literal injection in SQL
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('builds SignWell requests with recipients, coordinates, and webhook callback', () => {
    const code = source()
    expect(code).toContain('recipients')
    expect(code).toContain('fields')
    expect(code).toContain('getSowSigningFields')
    expect(code).toContain('client_signature')
    expect(code).toContain('client_date')
    expect(code).toContain('callback_url')
  })

  it('records send authorization and signature request before updating quote send state', () => {
    const code = source()
    expect(code).toContain('createSOWSendAuthorization')
    expect(code).toContain('createSignatureRequest')
    expect(code).toContain('sent_at')
    expect(code).toContain('expires_at')
  })
})

describe('signwell: webhook route', () => {
  const source = () => readFileSync(resolve('src/pages/api/webhooks/signwell.ts'), 'utf-8')

  it('signwell.ts webhook route exists', () => {
    expect(existsSync(resolve('src/pages/api/webhooks/signwell.ts'))).toBe(true)
  })

  it('exports POST handler', () => {
    expect(source()).toContain('export const POST')
  })

  it('implements HMAC-SHA256 event hash verification', () => {
    const code = source()
    expect(code).toContain('verifyEventHash')
    expect(code).toContain('HMAC')
    expect(code).toContain('SHA-256')
  })

  it('verifies hash from event.hash in payload body', () => {
    expect(source()).toContain('event.hash')
  })

  it('returns 401 for invalid signatures', () => {
    const code = source()
    expect(code).toContain('Invalid signature')
    expect(code).toContain('status: 401')
  })

  it('checks for SIGNWELL_WEBHOOK_SECRET configuration', () => {
    expect(source()).toContain('SIGNWELL_WEBHOOK_SECRET')
  })

  it('checks for SIGNWELL_API_KEY configuration', () => {
    expect(source()).toContain('SIGNWELL_API_KEY')
  })

  it('dispatches document_completed events to handler', () => {
    const code = source()
    expect(code).toContain("payload.event.type === 'document_completed'")
    expect(code).toContain('handleDocumentCompleted')
  })

  it('acknowledges non-completed events with 200', () => {
    const code = source()
    expect(code).toContain('payload.event.type')
    expect(code).toContain('status: 200')
  })

  it('uses constant-time comparison for hash check', () => {
    const code = source()
    expect(code).toContain('mismatch |=')
    expect(code).toContain('charCodeAt')
  })

  it('implements timestamp freshness check for replay protection', () => {
    const code = source()
    expect(code).toContain('event.time')
    expect(code).toContain('MAX_WEBHOOK_AGE_SECONDS')
    expect(code).toContain('Stale webhook')
  })

  it('does NOT use auth middleware (webhooks are unauthenticated)', () => {
    const code = source()
    expect(code).not.toContain("session.role !== 'admin'")
    expect(code).not.toContain('locals.session')
  })

  it('parses raw body for both signature verification and JSON parsing', () => {
    const code = source()
    expect(code).toContain('request.text()')
    expect(code).toContain('JSON.parse(rawBody)')
  })
})

describe('signwell: send-for-signature route', () => {
  const source = () => readFileSync(resolve('src/pages/api/admin/quotes/[id]/sign.ts'), 'utf-8')

  it('sign.ts route exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/quotes/[id]/sign.ts'))).toBe(true)
  })

  it('exports POST handler', () => {
    expect(source()).toContain('export const POST')
  })

  it('verifies admin session', () => {
    const code = source()
    expect(code).toContain("session.role !== 'admin'")
  })

  it('checks quote status is draft or sent', () => {
    const code = source()
    expect(code).toContain("quote.status !== 'draft'")
    expect(code).toContain("quote.status !== 'sent'")
  })

  it('requires an explicit signer_contact_id', () => {
    expect(source()).toContain('signer_contact_id')
  })

  it('loads the selected signer via getContact', () => {
    const code = source()
    expect(code).toContain('getContact')
    expect(code).toContain('signerContact')
  })

  it('delegates send orchestration to authorizeAndSendSOW', () => {
    const code = source()
    expect(code).toContain('authorizeAndSendSOW')
    expect(code).toContain('env.STORAGE')
    expect(code).toContain('callbackBaseEnv: env')
  })

  it('redirects back to quote detail page on success', () => {
    const code = source()
    expect(code).toContain('/admin/entities/')
    expect(code).toContain('saved=1')
  })

  it('keeps SQL out of the route layer', () => {
    const code = source()
    expect(code).not.toContain('.prepare(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })
})

describe('signwell: env.d.ts bindings', () => {
  const source = () => readFileSync(resolve('src/env.d.ts'), 'utf-8')

  it('declares SIGNWELL_API_KEY in CfEnv', () => {
    expect(source()).toContain('SIGNWELL_API_KEY')
  })

  it('declares SIGNWELL_WEBHOOK_SECRET in CfEnv', () => {
    expect(source()).toContain('SIGNWELL_WEBHOOK_SECRET')
  })

  it('SignWell bindings are optional (using ?)', () => {
    const code = source()
    expect(code).toContain('SIGNWELL_API_KEY?: string')
    expect(code).toContain('SIGNWELL_WEBHOOK_SECRET?: string')
  })
})
