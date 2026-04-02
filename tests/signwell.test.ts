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

  it('SignWellCreateDocumentRequest includes signers array', () => {
    const code = source()
    expect(code).toContain('signers')
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

  it('looks up quote by signwell_doc_id', () => {
    const code = source()
    expect(code).toContain('signwell_doc_id')
    expect(code).toContain('getQuoteBySignWellDocId')
  })

  it('implements idempotency check — returns early if already accepted', () => {
    const code = source()
    expect(code).toContain("quote.status === 'accepted'")
    expect(code).toContain('already accepted, skipping')
  })

  it('generates UUIDs for engagement and invoice BEFORE the batch', () => {
    const code = source()
    // engagementId and invoiceId must be generated before db.batch()
    const engagementIdIdx = code.indexOf('const engagementId = crypto.randomUUID()')
    const invoiceIdIdx = code.indexOf('const invoiceId = crypto.randomUUID()')
    const batchIdx = code.indexOf('await db.batch(')
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

  it('batch updates client status to active', () => {
    const code = source()
    expect(code).toContain("UPDATE clients SET status = 'active'")
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

  it('follows two-phase structure — returns 200 after Phase 1 even if Phase 2 fails', () => {
    const code = source()
    // Phase 2 side effects are in separate try/catch blocks after the batch
    expect(code).toContain('Phase 2: Side effects (best-effort)')
    expect(code).toContain('Non-fatal')
  })

  it('returns 500 on Phase 1 batch failure', () => {
    const code = source()
    expect(code).toContain('Phase 1 batch failed')
    expect(code).toContain('status: 500')
  })

  it('uploads signed PDF to R2 in Phase 2', () => {
    const code = source()
    expect(code).toContain('signed-sow.pdf')
    expect(code).toContain('storage.put')
  })

  it('updates quote signed_sow_path in Phase 2', () => {
    const code = source()
    expect(code).toContain('signed_sow_path')
    expect(code).toContain('signedSowPath')
  })

  it('sends confirmation email via Resend in Phase 2', () => {
    const code = source()
    expect(code).toContain('api.resend.com/emails')
    expect(code).toContain('SOW Signed')
  })

  it('uses parameterized queries with .bind()', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // No template literal injection in SQL
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })

  it('returns unknown document as 200 (non-retryable)', () => {
    const code = source()
    expect(code).toContain('Unknown SignWell document')
    // Should still return 200 for unknown documents
    const unknownBlock = code.substring(
      code.indexOf('Unknown SignWell document'),
      code.indexOf('Unknown SignWell document') + 200
    )
    expect(unknownBlock).toContain('status: 200')
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

  it('implements HMAC-SHA256 signature verification', () => {
    const code = source()
    expect(code).toContain('verifySignature')
    expect(code).toContain('HMAC')
    expect(code).toContain('SHA-256')
  })

  it('reads signature from x-signwell-signature header', () => {
    expect(source()).toContain('x-signwell-signature')
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
    expect(code).toContain("payload.event === 'document_completed'")
    expect(code).toContain('handleDocumentCompleted')
  })

  it('acknowledges non-completed events with 200', () => {
    const code = source()
    expect(code).toContain('payload.event')
    expect(code).toContain('status: 200')
  })

  it('uses constant-time comparison for signature check', () => {
    const code = source()
    expect(code).toContain('mismatch |=')
    expect(code).toContain('charCodeAt')
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

  it('verifies sow_path exists', () => {
    expect(source()).toContain('quote.sow_path')
  })

  it('prevents duplicate signwell sends (checks signwell_doc_id)', () => {
    expect(source()).toContain('quote.signwell_doc_id')
  })

  it('retrieves SOW PDF from R2', () => {
    const code = source()
    expect(code).toContain('getPdf')
    expect(code).toContain('env.STORAGE')
  })

  it('converts PDF to base64 for SignWell API', () => {
    expect(source()).toContain('pdfBase64')
  })

  it('gets client primary contact for signer details', () => {
    const code = source()
    expect(code).toContain('listContacts')
    expect(code).toContain('primaryContact')
  })

  it('calls createSignatureRequest with signer and field placements', () => {
    const code = source()
    expect(code).toContain('createSignatureRequest')
    expect(code).toContain('signers')
    expect(code).toContain('fields')
  })

  it('includes signature and date field placements', () => {
    const code = source()
    expect(code).toContain("type: 'signature'")
    expect(code).toContain("type: 'date'")
  })

  it('sets callback_url for webhook', () => {
    expect(source()).toContain('callback_url')
  })

  it('stores signwell_doc_id on the quote', () => {
    const code = source()
    expect(code).toContain('signwell_doc_id = ?')
    expect(code).toContain('signwellDoc.id')
  })

  it('transitions draft to sent when sending for signature', () => {
    const code = source()
    expect(code).toContain("status = 'sent'")
    expect(code).toContain('sent_at')
    expect(code).toContain('expires_at')
  })

  it('redirects back to quote detail page on success', () => {
    const code = source()
    expect(code).toContain('/admin/entities/')
    expect(code).toContain('saved=1')
  })

  it('uses parameterized queries with .bind()', () => {
    const code = source()
    expect(code).toContain('.bind(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })
})

describe('signwell: quote detail page integration', () => {
  const source = () =>
    readFileSync(resolve('src/pages/admin/clients/[id]/quotes/[quoteId].astro'), 'utf-8')

  it('includes Send for Signature button', () => {
    const code = source()
    expect(code).toContain('Send for Signature')
    expect(code).toContain('/sign')
  })

  it('shows Send for Signature when SOW exists and no signwell_doc_id', () => {
    const code = source()
    expect(code).toContain('canSendForSignature')
  })

  it('shows Awaiting Signature badge when signwell_doc_id exists and sent', () => {
    const code = source()
    expect(code).toContain('Awaiting Signature')
    expect(code).toContain('hasSignwell')
  })

  it('includes View Signed SOW link when signed_sow_path exists', () => {
    const code = source()
    expect(code).toContain('View Signed SOW')
    expect(code).toContain('hasSignedSow')
  })

  it('tracks signwell status with data attributes', () => {
    const code = source()
    expect(code).toContain('data-signwell-status="awaiting"')
    expect(code).toContain('data-signwell-status="signed"')
  })

  it('uses data-signwell-sign attribute on signature form', () => {
    expect(source()).toContain('data-signwell-sign')
  })

  it('uses data-signed-sow-link attribute on view link', () => {
    expect(source()).toContain('data-signed-sow-link')
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
