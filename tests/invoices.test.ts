import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('invoices: data layer', () => {
  const source = () => readFileSync(resolve('src/lib/db/invoices.ts'), 'utf-8')

  it('invoices.ts exists', () => {
    expect(existsSync(resolve('src/lib/db/invoices.ts'))).toBe(true)
  })

  it('exports Invoice interface', () => {
    expect(source()).toContain('export interface Invoice')
  })

  it('exports CreateInvoiceData interface', () => {
    expect(source()).toContain('export interface CreateInvoiceData')
  })

  it('exports UpdateInvoiceData interface', () => {
    expect(source()).toContain('export interface UpdateInvoiceData')
  })

  it('exports InvoiceType with all 5 types', () => {
    const code = source()
    expect(code).toContain('export type InvoiceType')
    expect(code).toContain("'deposit'")
    expect(code).toContain("'completion'")
    expect(code).toContain("'milestone'")
    expect(code).toContain("'assessment'")
    expect(code).toContain("'retainer'")
  })

  it('exports InvoiceStatus with all 5 statuses', () => {
    const code = source()
    expect(code).toContain('export type InvoiceStatus')
    expect(code).toContain("'draft'")
    expect(code).toContain("'sent'")
    expect(code).toContain("'paid'")
    expect(code).toContain("'overdue'")
    expect(code).toContain("'void'")
  })

  it('exports INVOICE_STATUSES constant with labels', () => {
    const code = source()
    expect(code).toContain('export const INVOICE_STATUSES')
    expect(code).toContain("label: 'Draft'")
    expect(code).toContain("label: 'Sent'")
    expect(code).toContain("label: 'Paid'")
    expect(code).toContain("label: 'Overdue'")
    expect(code).toContain("label: 'Void'")
  })

  it('exports VALID_TRANSITIONS with correct status machine', () => {
    const code = source()
    expect(code).toContain('export const VALID_TRANSITIONS')
    // draft -> sent, void
    expect(code).toContain("draft: ['sent', 'void']")
    // sent -> paid, overdue, void
    expect(code).toContain("sent: ['paid', 'overdue', 'void']")
    // overdue -> paid, void
    expect(code).toContain("overdue: ['paid', 'void']")
    // paid -> terminal
    expect(code).toContain('paid: []')
    // void -> terminal
    expect(code).toContain('void: []')
  })

  it('exports listInvoices function with org scoping', () => {
    const code = source()
    expect(code).toContain('export async function listInvoices')
    expect(code).toContain('org_id = ?')
  })

  it('listInvoices supports optional filters (entity, engagement, status)', () => {
    const code = source()
    expect(code).toContain('filters?.entityId')
    expect(code).toContain('filters?.engagementId')
    expect(code).toContain('filters?.status')
  })

  it('exports getInvoice function with org scoping', () => {
    const code = source()
    expect(code).toContain('export async function getInvoice')
    expect(code).toContain('WHERE id = ? AND org_id = ?')
  })

  it('exports createInvoice function with UUID generation', () => {
    const code = source()
    expect(code).toContain('export async function createInvoice')
    expect(code).toContain('crypto.randomUUID()')
  })

  it('exports updateInvoice function', () => {
    expect(source()).toContain('export async function updateInvoice')
  })

  it('exports updateInvoiceStatus function with status validation', () => {
    const code = source()
    expect(code).toContain('export async function updateInvoiceStatus')
    expect(code).toContain('VALID_TRANSITIONS')
    expect(code).toContain('Invalid status transition')
  })

  it('updateInvoiceStatus sets paid_at on payment', () => {
    const code = source()
    expect(code).toContain("newStatus === 'paid'")
    expect(code).toContain('paid_at')
  })

  it('updateInvoiceStatus sets sent_at on send', () => {
    const code = source()
    expect(code).toContain("newStatus === 'sent'")
    expect(code).toContain('sent_at')
  })

  it('exports listInvoicesForEntity for portal access', () => {
    const code = source()
    expect(code).toContain('export async function listInvoicesForEntity')
    expect(code).toContain('entity_id = ?')
  })

  it('portal-visible statuses exclude draft and void', () => {
    const code = source()
    expect(code).toContain('PORTAL_VISIBLE_STATUSES')
    expect(code).toContain("'sent'")
    expect(code).toContain("'paid'")
    expect(code).toContain("'overdue'")
  })

  it('uses parameterized queries with .bind()', () => {
    const code = source()
    expect(code).toContain('.bind(')
    // No template literal injection in SQL
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })
})

describe('invoices: stripe types', () => {
  const source = () => readFileSync(resolve('src/lib/stripe/types.ts'), 'utf-8')

  it('types.ts exists', () => {
    expect(existsSync(resolve('src/lib/stripe/types.ts'))).toBe(true)
  })

  it('exports StripeInvoiceLineItem type', () => {
    expect(source()).toContain('export interface StripeInvoiceLineItem')
  })

  it('exports StripeCreateInvoiceParams type', () => {
    expect(source()).toContain('export interface StripeCreateInvoiceParams')
  })

  it('exports StripeInvoice type', () => {
    expect(source()).toContain('export interface StripeInvoice')
  })

  it('exports StripeWebhookEvent type', () => {
    expect(source()).toContain('export interface StripeWebhookEvent')
  })

  it('exports StripeInvoiceResult type', () => {
    expect(source()).toContain('export interface StripeInvoiceResult')
  })

  it('StripeCreateInvoiceParams includes customer_email', () => {
    expect(source()).toContain('customer_email')
  })

  it('StripeCreateInvoiceParams includes line_items', () => {
    expect(source()).toContain('line_items')
  })

  it('StripeCreateInvoiceParams includes collection_method', () => {
    expect(source()).toContain('collection_method')
  })

  it('StripeCreateInvoiceParams supports payment_method_types', () => {
    expect(source()).toContain('payment_method_types')
  })

  it('StripeInvoice includes hosted_invoice_url', () => {
    expect(source()).toContain('hosted_invoice_url')
  })

  it('StripeWebhookEvent includes data.object (StripeInvoice)', () => {
    const code = source()
    expect(code).toContain('data:')
    expect(code).toContain('object: StripeInvoice')
  })
})

describe('invoices: stripe client', () => {
  const source = () => readFileSync(resolve('src/lib/stripe/client.ts'), 'utf-8')

  it('client.ts exists', () => {
    expect(existsSync(resolve('src/lib/stripe/client.ts'))).toBe(true)
  })

  it('exports createStripeInvoice function', () => {
    expect(source()).toContain('export async function createStripeInvoice')
  })

  it('exports sendStripeInvoice function', () => {
    expect(source()).toContain('export async function sendStripeInvoice')
  })

  it('exports voidStripeInvoice function', () => {
    expect(source()).toContain('export async function voidStripeInvoice')
  })

  it('exports getStripeInvoice function', () => {
    expect(source()).toContain('export async function getStripeInvoice')
  })

  it('uses correct Stripe API base URL', () => {
    expect(source()).toContain('https://api.stripe.com/v1')
  })

  it('dev-mode: returns mock when no API key for createStripeInvoice', () => {
    const code = source()
    expect(code).toContain("'dev_inv_' + crypto.randomUUID()")
    expect(code).toContain("'#dev-mode'")
  })

  it('dev-mode: logs request details', () => {
    const code = source()
    expect(code).toContain('[DEV] Stripe: would create invoice')
  })

  it('dev-mode: sendStripeInvoice returns mock when no API key', () => {
    const code = source()
    expect(code).toContain('[DEV] Stripe: would send invoice')
  })

  it('dev-mode: voidStripeInvoice returns mock when no API key', () => {
    const code = source()
    expect(code).toContain('[DEV] Stripe: would void invoice')
  })

  it('dev-mode: getStripeInvoice returns mock when no API key', () => {
    const code = source()
    expect(code).toContain('[DEV] Stripe: would get invoice')
  })

  it('uses form-encoded bodies (not JSON) for Stripe API', () => {
    const code = source()
    expect(code).toContain("'Content-Type': 'application/x-www-form-urlencoded'")
    expect(code).toContain('URLSearchParams')
  })

  it('uses Bearer token auth', () => {
    expect(source()).toContain('Authorization: `Bearer ${apiKey}`')
  })

  it('sendStripeInvoice calls finalize then send', () => {
    const code = source()
    expect(code).toContain('/finalize')
    expect(code).toContain('/send')
  })

  it('voidStripeInvoice calls POST /invoices/:id/void', () => {
    expect(source()).toContain('/void')
  })

  it('uses raw fetch — no SDK dependency', () => {
    const code = source()
    expect(code).toContain('await fetch(')
    expect(code).not.toContain("from 'stripe'")
    expect(code).not.toContain('import Stripe')
  })
})

describe('invoices: stripe webhook handler', () => {
  const source = () => readFileSync(resolve('src/lib/webhooks/stripe-handler.ts'), 'utf-8')

  it('stripe-handler.ts exists', () => {
    expect(existsSync(resolve('src/lib/webhooks/stripe-handler.ts'))).toBe(true)
  })

  it('exports handleInvoicePaid function', () => {
    expect(source()).toContain('export async function handleInvoicePaid')
  })

  it('exports handleInvoicePaymentFailed function', () => {
    expect(source()).toContain('export async function handleInvoicePaymentFailed')
  })

  it('looks up invoice by stripe_invoice_id', () => {
    const code = source()
    expect(code).toContain('stripe_invoice_id')
    expect(code).toContain('getInvoiceByStripeId')
  })

  it('implements idempotency check — returns early if already paid', () => {
    const code = source()
    expect(code).toContain("invoice.status === 'paid'")
    expect(code).toContain('already paid, skipping')
  })

  it('uses db.batch() for atomic multi-table writes (two-phase pattern)', () => {
    expect(source()).toContain('await db.batch(')
  })

  it('Phase 1: updates invoice status to paid with paid_at', () => {
    const code = source()
    expect(code).toContain("status = 'paid'")
    expect(code).toContain('paid_at')
  })

  it('Phase 1: activates engagement for deposit invoices', () => {
    const code = source()
    expect(code).toContain("invoice.type === 'deposit'")
    expect(code).toContain("status = 'active'")
    expect(code).toContain('start_date')
  })

  it('returns 500 on Phase 1 batch failure', () => {
    const code = source()
    expect(code).toContain('Phase 1 batch failed')
    expect(code).toContain('status: 500')
  })

  it('Phase 2: sends payment confirmation email (best-effort)', () => {
    const code = source()
    expect(code).toContain('sendEmail')
    expect(code).toContain('paymentConfirmationEmailHtml')
  })

  it('returns 200 for unknown Stripe invoice (non-retryable)', () => {
    const code = source()
    expect(code).toContain('Unknown Stripe invoice')
    const unknownBlock = code.substring(
      code.indexOf('Unknown Stripe invoice'),
      code.indexOf('Unknown Stripe invoice') + 200
    )
    expect(unknownBlock).toContain('status: 200')
  })

  it('handleInvoicePaymentFailed logs failure but does not change status', () => {
    const code = source()
    expect(code).toContain('Payment failed for invoice')
    // Should not contain UPDATE invoices in the payment_failed handler
    const paymentFailedFn = code.substring(code.indexOf('handleInvoicePaymentFailed'))
    expect(paymentFailedFn).not.toContain('UPDATE invoices SET status')
  })

  it('uses parameterized queries with .bind()', () => {
    const code = source()
    expect(code).toContain('.bind(')
    expect(code).not.toMatch(/prepare\(`[^`]*\$\{/)
  })
})

describe('invoices: stripe webhook route', () => {
  const source = () => readFileSync(resolve('src/pages/api/webhooks/stripe.ts'), 'utf-8')

  it('stripe.ts webhook route exists', () => {
    expect(existsSync(resolve('src/pages/api/webhooks/stripe.ts'))).toBe(true)
  })

  it('exports POST handler', () => {
    expect(source()).toContain('export const POST')
  })

  it('implements Stripe signature verification', () => {
    const code = source()
    expect(code).toContain('verifyStripeSignature')
    expect(code).toContain('HMAC')
    expect(code).toContain('SHA-256')
  })

  it('reads signature from stripe-signature header', () => {
    expect(source()).toContain('stripe-signature')
  })

  it('parses Stripe signature format (t=timestamp,v1=signature)', () => {
    const code = source()
    expect(code).toContain("elements['t']")
    expect(code).toContain("elements['v1']")
  })

  it('validates timestamp tolerance to prevent replay attacks', () => {
    const code = source()
    expect(code).toContain('300') // 5 minute tolerance
    expect(code).toContain('timestampSeconds')
  })

  it('returns 401 for invalid signatures', () => {
    const code = source()
    expect(code).toContain('Invalid signature')
    expect(code).toContain('status: 401')
  })

  it('checks for STRIPE_WEBHOOK_SECRET configuration', () => {
    expect(source()).toContain('STRIPE_WEBHOOK_SECRET')
  })

  it('dispatches invoice.paid events to handler', () => {
    const code = source()
    expect(code).toContain("event.type === 'invoice.paid'")
    expect(code).toContain('handleInvoicePaid')
  })

  it('dispatches invoice.payment_failed events to handler', () => {
    const code = source()
    expect(code).toContain("event.type === 'invoice.payment_failed'")
    expect(code).toContain('handleInvoicePaymentFailed')
  })

  it('acknowledges unhandled events with 200', () => {
    const code = source()
    expect(code).toContain('event.type')
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

describe('invoices: portal view', () => {
  const source = () => readFileSync(resolve('src/pages/portal/invoices/index.astro'), 'utf-8')

  it('portal invoice page exists', () => {
    expect(existsSync(resolve('src/pages/portal/invoices/index.astro'))).toBe(true)
  })

  it('uses listInvoicesForEntity for entity-scoped access', () => {
    expect(source()).toContain('listInvoicesForEntity')
  })

  it('resolves entity via getPortalClient', () => {
    const code = source()
    expect(code).toContain('getPortalClient')
    expect(code).toContain('session.userId')
  })

  it('displays invoice amount', () => {
    expect(source()).toContain('formatCurrency')
  })

  it('displays invoice status badges', () => {
    expect(source()).toContain('statusColorMap')
  })

  it('links each row to the invoice detail page', () => {
    const code = source()
    expect(code).toContain('/portal/invoices/${inv.id}')
  })

  it('shows a Pay affordance for unpaid invoices with a usable Stripe URL', () => {
    const code = source()
    // The Stripe URL is still considered when choosing what affordance to render.
    expect(code).toContain('stripe_hosted_url')
    expect(code).toMatch(/Pay\b/)
  })

  it('shows a pending-link indicator when Stripe is not yet configured', () => {
    expect(source()).toContain('Payment link pending')
  })

  it('shows paid date for paid invoices', () => {
    const code = source()
    expect(code).toContain('paid_at')
    expect(code).toContain('Paid')
  })

  it('handles empty state when no invoices exist', () => {
    expect(source()).toContain('No invoices yet')
  })
})

describe('invoices: portal detail view', () => {
  const source = () => readFileSync(resolve('src/pages/portal/invoices/[id].astro'), 'utf-8')

  it('portal invoice detail page exists', () => {
    expect(existsSync(resolve('src/pages/portal/invoices/[id].astro'))).toBe(true)
  })

  it('renders the Stripe hosted URL as the pay CTA when usable', () => {
    const code = source()
    expect(code).toContain('stripe_hosted_url')
    expect(code).toContain('payHref')
  })
})

describe('invoices: admin API routes', () => {
  it('POST /api/admin/invoices/index.ts exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/invoices/index.ts'))).toBe(true)
  })

  it('POST /api/admin/invoices/[id].ts exists', () => {
    expect(existsSync(resolve('src/pages/api/admin/invoices/[id].ts'))).toBe(true)
  })

  describe('create route (index.ts)', () => {
    const source = () => readFileSync(resolve('src/pages/api/admin/invoices/index.ts'), 'utf-8')

    it('exports POST handler', () => {
      expect(source()).toContain('export const POST')
    })

    it('verifies admin session', () => {
      expect(source()).toContain("session.role !== 'admin'")
    })

    it('validates invoice type', () => {
      expect(source()).toContain('VALID_TYPES')
    })

    it('validates amount is positive', () => {
      expect(source()).toContain('amount <= 0')
    })

    it('calls createInvoice from data layer', () => {
      expect(source()).toContain('createInvoice')
    })
  })

  describe('action route ([id].ts)', () => {
    const source = () => readFileSync(resolve('src/pages/api/admin/invoices/[id].ts'), 'utf-8')

    it('exports POST handler', () => {
      expect(source()).toContain('export const POST')
    })

    it('verifies admin session', () => {
      expect(source()).toContain("session.role !== 'admin'")
    })

    it('handles send action — creates in Stripe and sends', () => {
      const code = source()
      expect(code).toContain("action === 'send'")
      expect(code).toContain('createStripeInvoice')
      expect(code).toContain('sendStripeInvoice')
    })

    it('handles void action — voids in Stripe and locally', () => {
      const code = source()
      expect(code).toContain("action === 'void'")
      expect(code).toContain('voidStripeInvoice')
      expect(code).toContain('updateInvoiceStatus')
    })

    it('handles mark_paid action — manual override for offline payments', () => {
      const code = source()
      expect(code).toContain("action === 'mark_paid'")
      expect(code).toContain("payment_method = 'manual'")
    })

    it('mark_paid activates engagement for deposit invoices', () => {
      const code = source()
      expect(code).toContain("existing.type === 'deposit'")
      expect(code).toContain("status = 'active'")
    })

    it('sends notification email when invoice is sent', () => {
      const code = source()
      expect(code).toContain('invoiceSentEmailHtml')
      expect(code).toContain('sendEmail')
    })
  })
})

describe('invoices: email templates', () => {
  const source = () => readFileSync(resolve('src/lib/email/templates.ts'), 'utf-8')

  it('exports invoiceSentEmailHtml function', () => {
    expect(source()).toContain('export function invoiceSentEmailHtml')
  })

  it('invoiceSentEmailHtml includes clientName, amount, and portalUrl parameters', () => {
    const code = source()
    expect(code).toContain('invoiceSentEmailHtml(')
    expect(code).toContain('clientName: string')
    expect(code).toContain('amount: string')
    expect(code).toContain('portalUrl: string')
  })

  it('invoiceSentEmailHtml mentions invoice is ready', () => {
    expect(source()).toContain('invoice from ${BRAND_NAME}')
  })

  it('exports paymentConfirmationEmailHtml function', () => {
    expect(source()).toContain('export function paymentConfirmationEmailHtml')
  })

  it('paymentConfirmationEmailHtml includes clientName and amount parameters', () => {
    const code = source()
    expect(code).toContain('paymentConfirmationEmailHtml(clientName: string, amount: string)')
  })

  it('paymentConfirmationEmailHtml confirms payment received', () => {
    expect(source()).toContain('received your payment')
  })
})

describe('invoices: env.d.ts bindings', () => {
  const source = () => readFileSync(resolve('src/env.d.ts'), 'utf-8')

  it('declares STRIPE_API_KEY in CfEnv', () => {
    expect(source()).toContain('STRIPE_API_KEY')
  })

  it('declares STRIPE_WEBHOOK_SECRET in CfEnv', () => {
    expect(source()).toContain('STRIPE_WEBHOOK_SECRET')
  })

  it('Stripe bindings are optional (using ?)', () => {
    const code = source()
    expect(code).toContain('STRIPE_API_KEY?: string')
    expect(code).toContain('STRIPE_WEBHOOK_SECRET?: string')
  })
})

describe('invoices: portal dashboard integration', () => {
  const source = () => readFileSync(resolve('src/pages/portal/index.astro'), 'utf-8')

  it('surfaces the pending invoice as the dominant action', () => {
    const code = source()
    // C-hybrid replaces the Invoices quick link with an ActionCard that deep-links
    // to the specific invoice. Keep users one tap away from payment.
    expect(code).toContain('pendingInvoice')
    expect(code).toContain('/portal/invoices/')
    expect(code).toContain('Pay invoice')
  })

  it('links paid and sent invoices from the activity timeline', () => {
    const code = source()
    expect(code).toContain('/portal/invoices/')
    expect(code).toMatch(/Invoice #/)
  })
})

describe('invoices: signwell handler creates deposit invoice', () => {
  const source = () => readFileSync(resolve('src/lib/sow/service.ts'), 'utf-8')

  it('finalization creates the deposit invoice in the acceptance batch', () => {
    const code = source()
    expect(code).toContain('INSERT INTO invoices')
    expect(code).toContain("'deposit'")
    expect(code).toContain("'draft'")
  })

  it('deposit invoice uses raw SQL in db.batch for atomicity', () => {
    const code = source()
    expect(code).toContain('db.batch([')
    expect(code).toContain('INSERT INTO invoices')
  })
})
