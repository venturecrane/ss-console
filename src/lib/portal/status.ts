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
 * Engagement (portal progress surface):
 *   scheduled  → info    → "Starting Soon"
 *   active     → success → "Underway"
 *   handoff    → info    → "Wrapping Up"
 *   safety_net → warning → "Stabilization"
 *   completed  → success → "Complete"
 *   cancelled  → neutral → "Cancelled"
 *
 * "Stabilization" aligns with Decision #27 ("2-week async stabilization
 * period starting at handoff"). The DB enum stays `safety_net` — only
 * the client-visible label changes.
 */

export type Tone = 'info' | 'success' | 'danger' | 'warning' | 'neutral'

export type InvoiceStatus = 'draft' | 'sent' | 'paid' | 'overdue' | 'void'
export type QuoteStatus = 'draft' | 'sent' | 'accepted' | 'declined' | 'expired' | 'superseded'
export type EngagementStatus =
  | 'scheduled'
  | 'active'
  | 'handoff'
  | 'safety_net'
  | 'completed'
  | 'cancelled'

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
 * Engagement status tone + label — client-facing.
 *
 * The database column `engagements.status` uses internal enum values
 * (`safety_net`, etc.) that read as jargon on portal surfaces. This
 * resolver is the single source of truth for the client-friendly
 * labels and matching pill tones. Admin UI keeps the raw enum via
 * ENGAGEMENT_STATUSES in src/lib/db/engagements.ts — don't swap admin
 * labels through this module.
 *
 * Label choices:
 *   - "Stabilization" for `safety_net` — aligns with Decision #27
 *     ("2-week async stabilization period starting at handoff").
 *   - Other labels mirror the internal enum in title case; they are
 *     already plain English and don't read as jargon.
 */
const ENGAGEMENT_TONE: Record<EngagementStatus, Tone> = {
  scheduled: 'info',
  active: 'success',
  handoff: 'info',
  safety_net: 'warning',
  completed: 'success',
  cancelled: 'neutral',
}

const ENGAGEMENT_LABEL: Record<EngagementStatus, string> = {
  scheduled: 'Starting Soon',
  active: 'Underway',
  handoff: 'Wrapping Up',
  safety_net: 'Stabilization',
  completed: 'Complete',
  cancelled: 'Cancelled',
}

export function resolveEngagementTone(status: string): Tone {
  return ENGAGEMENT_TONE[status as EngagementStatus] ?? 'neutral'
}

export function resolveEngagementLabel(status: string): string {
  return ENGAGEMENT_LABEL[status as EngagementStatus] ?? status
}

/**
 * Tone → class map, consumed by StatusPill. Uses semantic tokens only.
 * Not exported as part of the public API — consumers pass a Tone; only
 * StatusPill renders the pill.
 *
 * Architect's Studio identity: filled rectangular tag with white text, not
 * a tinted pill. The high-contrast solid block reads as a status indicator
 * in a technical document, not a marketing badge. `neutral` is the one
 * subtle variant — it pairs border gray with secondary text for muted
 * internal states (draft, superseded) that rarely surface to the client.
 */
export const TONE_CLASS: Record<Tone, string> = {
  info: 'bg-[color:var(--color-primary)] text-white',
  success: 'bg-[color:var(--color-complete)] text-white',
  danger: 'bg-[color:var(--color-error)] text-white',
  warning: 'bg-[color:var(--color-attention)] text-white',
  neutral: 'bg-[color:var(--color-border)] text-[color:var(--color-text-secondary)]',
}
