import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

// NOTE (2026-06-12, code-review Wave 5): the source-mirror describe blocks
// that previously lived here ('invoices: data layer', 'invoices: stripe
// client', 'invoices: stripe webhook handler', 'invoices: stripe webhook
// route', and 'invoices: signwell handler creates deposit invoice') were
// readFileSync + toContain assertions that passed even if every function
// were a stub. They are replaced by behavioral tests against a real D1:
//   - src/lib/db/invoices.test.ts          (DAL: scoping, state machine, updates)
//   - src/lib/stripe/client.test.ts        (mocked-fetch request/response shapes)
//   - src/lib/webhooks/stripe-handler.test.ts (two-phase paid batch + route dispatch)
//   - tests/webhooks/stripe-verify.test.ts (webhook signature verification)
//   - src/lib/webhooks/signwell-handler.test.ts (deposit invoice creation at signing)
// The blocks kept below guard rendered surfaces (.astro contracts) and
// configuration that the behavioral suites do not exercise.

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

describe('invoices: portal view', () => {
  const source = () => readFileSync(resolve('src/pages/portal/billing/index.astro'), 'utf-8')

  it('portal invoice page exists', () => {
    expect(existsSync(resolve('src/pages/portal/billing/index.astro'))).toBe(true)
  })

  it('uses listInvoicesForEntity for entity-scoped access', () => {
    expect(source()).toContain('listInvoicesForEntity')
  })

  it('resolves entity via getPortalClient (Clerk-aware signature)', () => {
    // After PR #906 the portal session resolver takes Astro.locals
    // (which carries Clerk's locals.auth() and locals.currentUser())
    // instead of (userId, orgId). Magic-link's session.userId is gone
    // from portal pages.
    const code = source()
    expect(code).toContain('getPortalClient(env.DB, Astro.locals)')
  })

  it('passes amount to PortalListItem as cents', () => {
    // After R7 registry: the page defers rendering to PortalListItem +
    // MoneyDisplay. `inv.amount` is dollars; the page converts to cents.
    const code = source()
    expect(code).toContain('amountCents={Math.round(inv.amount * 100)}')
  })

  it('resolves status tone + stamp label via shared helpers (R7 registry)', () => {
    const code = source()
    expect(code).toContain('resolveInvoiceTone')
    // Post-Plainspoken (2026-04-23, PR B) the pill renders the stamp
    // vocabulary via `resolveInvoiceStampLabel`. The descriptive-label
    // resolver (`resolveInvoiceLabel`) still exists for detail-page
    // prose but list rows use the stamp form.
    expect(code).toMatch(/resolveInvoiceStampLabel|resolveInvoiceLabel/)
  })

  it('links each row to the invoice detail page', () => {
    const code = source()
    expect(code).toContain('/portal/billing/invoices/${inv.id}')
  })

  it('signals unpaid/overdue state via tone, not a separate Pay button', () => {
    const code = source()
    // UI-PATTERNS R2 (redundancy): the card is the link; no standalone
    // Pay affordance that would duplicate the card-level navigation.
    // The tone (`info` for sent, `danger` for overdue) surfaces action-
    // required state; detail page owns the actual pay CTA.
    expect(code).toContain('resolveInvoiceTone')
    expect(code).not.toMatch(/\bPay\b/)
    expect(code).not.toContain('Payment link pending')
  })

  it('surfaces paid / due dates via shared formatter', () => {
    const code = source()
    expect(code).toContain('formatShortDate')
    // Post-Plainspoken (PR B) the page composes the date cell inline via
    // `resolveDateLabel` + `resolveDateValue` helpers instead of a single
    // `resolveMetaCaption`. Both patterns satisfy the R7 contract: dates
    // come from the shared formatter, not a local string template.
    expect(code).toMatch(/resolveMetaCaption|resolveDateValue/)
  })

  it('handles empty state when no invoices exist', () => {
    expect(source()).toContain('Nothing on the ledger yet')
  })
})

describe('invoices: portal detail view', () => {
  const source = () =>
    readFileSync(resolve('src/pages/portal/billing/invoices/[id].astro'), 'utf-8') +
    '\n' +
    readFileSync(resolve('src/lib/portal/invoice-detail.ts'), 'utf-8')

  it('portal invoice detail page exists', () => {
    expect(existsSync(resolve('src/pages/portal/billing/invoices/[id].astro'))).toBe(true)
  })

  it('gates the pay CTA on a real Stripe hosted URL', () => {
    const code = source()
    // The detail page must only link to Stripe when stripe_hosted_url is
    // present and not the dev-mode sentinel. A missing URL must render a
    // non-link "Payment link pending" state, not a link to a server route.
    expect(code).toContain('stripe_hosted_url')
    expect(code).toContain('isPayable')
    expect(code).toContain('Payment link pending')
  })

  it('does not link to a nonexistent /api/invoices/[id]/pay route (#419)', () => {
    const code = source()
    // The previous fallback rendered a clickable link to /api/invoices/[id]/pay,
    // which never existed. This regression guard ensures we never reintroduce it.
    expect(code).not.toMatch(/\/api\/invoices\/\$\{[^}]*\}\/pay/)
    expect(code).not.toContain('payHref')
  })

  it('renders invoice line items without any fabricated fallback (#398)', () => {
    const code = source()
    // The page must never invent invoice line-item copy. No "Engagement work",
    // no borrow from scope_summary, no fallback row synthesized from invoice.description.
    expect(code).not.toContain('Engagement work')
    expect(code).not.toContain('displayLineItems')
    expect(code).not.toMatch(/scope_summary\s*\?\?/)
  })

  it('loads invoice detail through the portal reader', () => {
    const code = source()
    expect(code).toContain('loadPortalInvoiceDetail')
    expect(code).toContain('getInvoiceForEntity')
    expect(code).toContain('listLineItemsForInvoice')
  })

  it('carries no "Payment details" section (removed 2026-09-09)', () => {
    // The section restated the due date already in the header and caption,
    // and described Stripe's payment methods on SMD's own page. Captain
    // direction: the page is the line items, the total, and the Pay card.
    const code = source()
    expect(code).not.toContain('Payment details')
    expect(code).not.toContain('InvoicePaymentDetails')
    expect(code).not.toContain('Card or bank transfer')
    expect(existsSync(resolve('src/components/portal/InvoicePaymentDetails.astro'))).toBe(false)
    const preview = readFileSync(resolve('src/components/portal/InvoiceDetail.astro'), 'utf-8')
    expect(preview).not.toContain('Payment details')
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
      expect(source()).toContain('requireAdminSession')
    })

    it('validates invoice type against the shared vocabulary', () => {
      expect(source()).toContain('isInvoiceType')
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
      expect(source()).toContain('requireAdminSession')
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

    it('handles reschedule action — re-issues in Stripe before voiding the original', () => {
      const code = source()
      expect(code).toContain("action === 'reschedule'")
      expect(code).toContain('dueDateToStripeTimestamp')
      // Replacement first, row repointed second, original voided last, so the
      // row never references a voided invoice with nothing payable behind it.
      const create = code.indexOf('createStripeInvoice(env.STRIPE_API_KEY, params)')
      const repoint = code.indexOf('stripe_invoice_id: created.id')
      const voidOld = code.indexOf('voidStripeInvoice(env.STRIPE_API_KEY, previousStripeId)')
      expect(create).toBeGreaterThan(-1)
      expect(repoint).toBeGreaterThan(create)
      expect(voidOld).toBeGreaterThan(repoint)
      expect(code).toContain('error=stale_stripe_invoice')
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
    // Portal IA rebuild: the pending-invoice action moved from the home
    // rail into the Billing offering card (home-cards.ts), which deep-links
    // the specific invoice. Keep users one tap away from payment.
    const cards = readFileSync(resolve('src/lib/portal/home-cards.ts'), 'utf-8')
    expect(cards).toContain("i.status === 'sent' || i.status === 'overdue'")
    expect(cards).toContain('/portal/billing/invoices/')
    expect(cards).toContain('Pay invoice')
    expect(source()).toContain('loadHomeCards')
  })

  it('links paid and sent invoices from the activity timeline', () => {
    const code = source()
    expect(code).toContain('/portal/billing/invoices/')
    expect(code).toMatch(/Invoice #/)
  })
})
