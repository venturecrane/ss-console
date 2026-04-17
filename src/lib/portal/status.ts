/**
 * Portal status resolution — per-entity tone + label + class lookups.
 *
 * Source of truth for status rendering on all portal surfaces. Admin uses
 * src/lib/ui/status-badge.ts (raw Tailwind hex-family classes); portal uses
 * this module (semantic var(--color-*) tokens). They remain separate by
 * design — the portal is the drift surface and stays tone-based.
 *
 * Contract (UI-PATTERNS.md R7):
 *   - Portal surfaces: import from src/lib/portal/status.ts
 *   - Admin surfaces: import from src/lib/ui/status-badge.ts
 *   - Shared/cross-surface: new helper when first needed; don't mix.
 *
 * Status → tone → label tables (argue with these assignments, not with the
 * enum):
 *
 * Invoice:
 *   draft   → neutral → "Draft"    (internal only; never portal-visible)
 *   sent    → info    → "Sent"
 *   paid    → success → "Paid"
 *   overdue → danger  → "Overdue"
 *   void    → neutral → "Void"     (internal only; never portal-visible)
 *
 * Quote:
 *   draft      → neutral → "Draft"           (internal only)
 *   sent       → info    → "Pending Review"  (client-facing: action required)
 *   accepted   → success → "Accepted"
 *   declined   → danger  → "Declined"
 *   expired    → warning → "Expired"
 *   superseded → neutral → "Superseded"      (internal only)
 *
 * Engagement (portal progress surface — future use):
 *   scheduled  → info    → "Scheduled"
 *   active     → success → "Active"
 *   handoff    → info    → "Handoff"
 *   safety_net → warning → "Stabilization"
 *   completed  → success → "Completed"
 *   cancelled  → neutral → "Cancelled"
 */

export type Tone = 'info' | 'success' | 'danger' | 'warning' | 'neutral'

export type InvoiceStatus = 'draft' | 'sent' | 'paid' | 'overdue' | 'void'
export type QuoteStatus = 'draft' | 'sent' | 'accepted' | 'declined' | 'expired' | 'superseded'

const INVOICE_TONE: Record<InvoiceStatus, Tone> = {
  draft: 'neutral',
  sent: 'info',
  paid: 'success',
  overdue: 'danger',
  void: 'neutral',
}

const INVOICE_LABEL: Record<InvoiceStatus, string> = {
  draft: 'Draft',
  sent: 'Sent',
  paid: 'Paid',
  overdue: 'Overdue',
  void: 'Void',
}

const QUOTE_TONE: Record<QuoteStatus, Tone> = {
  draft: 'neutral',
  sent: 'info',
  accepted: 'success',
  declined: 'danger',
  expired: 'warning',
  superseded: 'neutral',
}

const QUOTE_LABEL: Record<QuoteStatus, string> = {
  draft: 'Draft',
  sent: 'Pending Review',
  accepted: 'Accepted',
  declined: 'Declined',
  expired: 'Expired',
  superseded: 'Superseded',
}

export function resolveInvoiceTone(status: string): Tone {
  return INVOICE_TONE[status as InvoiceStatus] ?? 'neutral'
}

export function resolveInvoiceLabel(status: string): string {
  return INVOICE_LABEL[status as InvoiceStatus] ?? status
}

export function resolveQuoteTone(status: string): Tone {
  return QUOTE_TONE[status as QuoteStatus] ?? 'neutral'
}

export function resolveQuoteLabel(status: string): string {
  return QUOTE_LABEL[status as QuoteStatus] ?? status
}

/**
 * Tone → class map, consumed by StatusPill. Uses semantic tokens only.
 * Not exported as part of the public API — consumers pass a Tone; only
 * StatusPill renders the pill.
 *
 * The alpha-channel backgrounds (e.g., `bg-[color:var(--color-primary)]/10`)
 * give the pill a tinted surface without hard-coding a separate color
 * variable per tone. Text color uses the same semantic role at full
 * saturation for AA contrast against the tinted background.
 */
export const TONE_CLASS: Record<Tone, string> = {
  info: 'bg-[color:var(--color-primary)]/10 text-[color:var(--color-primary)]',
  success: 'bg-[color:var(--color-complete)]/10 text-[color:var(--color-complete)]',
  danger: 'bg-[color:var(--color-error)]/10 text-[color:var(--color-error)]',
  warning: 'bg-[color:var(--color-attention)]/10 text-[color:var(--color-attention)]',
  neutral: 'bg-[color:var(--color-surface-muted,#f1f5f9)] text-[color:var(--color-text-secondary)]',
}
