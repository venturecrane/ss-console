/**
 * Portal status resolution — per-entity tone + label + class lookups.
 *
 * Source of truth for status rendering on all portal surfaces. Admin uses
 * src/lib/ui/status-badge.ts (raw Tailwind hex-family classes); portal uses
 * this module (semantic var(--ss-color-*) tokens). They remain separate by
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

export type Tone = 'info' | 'success' | 'danger' | 'warning' | 'neutral' | 'outline'

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
 * Plainspoken stamp-label resolvers.
 *
 * The Plainspoken Sign Shop identity renders status as a flat rectangular
 * stamp (bill-of-lading register). Stamps are terse, ALL CAPS, and drawn
 * from a closed 12-word vocabulary so the visual rhythm across list rows
 * and detail headers stays calm:
 *
 *   PAID · ACCEPTED · SIGNED · DUE · UNDERWAY · PENDING · COMPLETED ·
 *   ARCHIVED · IN PROG · DECLINED · EXPIRED · OVERDUE
 *
 * These are emitted in addition to (not replacing) the descriptive
 * `resolve*Label` functions above. Detail prose and subheaders still use
 * the descriptive labels ("Stabilization period", "Pending Review");
 * StatusPill stamps use the vocabulary via the resolvers below. Pill and
 * detail text can diverge safely — a round-trip test locks every enum
 * member to a vocabulary word so the pill never drifts.
 *
 * Stamp mapping rationale:
 *   - quote.sent → PENDING (client action required; "Pending Review"
 *     compresses to PENDING in stamp form).
 *   - quote.superseded / invoice.void / quote.draft / invoice.draft →
 *     ARCHIVED (internal states; if a stamp ever surfaces, ARCHIVED is
 *     the least-alarming catch-all).
 *   - engagement.safety_net → IN PROG (Decision #27 "stabilization" stays
 *     the detail-text label; the stamp reads IN PROG to keep the closed
 *     vocabulary).
 *   - milestone.skipped → ARCHIVED (rare; the skip is internal bookkeeping).
 */

export type StampLabel =
  | 'PAID'
  | 'ACCEPTED'
  | 'SIGNED'
  | 'DUE'
  | 'UNDERWAY'
  | 'PENDING'
  | 'COMPLETED'
  | 'ARCHIVED'
  | 'IN PROG'
  | 'DECLINED'
  | 'EXPIRED'
  | 'OVERDUE'

export const STAMP_VOCABULARY: readonly StampLabel[] = [
  'PAID',
  'ACCEPTED',
  'SIGNED',
  'DUE',
  'UNDERWAY',
  'PENDING',
  'COMPLETED',
  'ARCHIVED',
  'IN PROG',
  'DECLINED',
  'EXPIRED',
  'OVERDUE',
] as const

const QUOTE_STAMP: Record<QuoteStatus, StampLabel> = {
  draft: 'ARCHIVED',
  sent: 'PENDING',
  accepted: 'ACCEPTED',
  declined: 'DECLINED',
  expired: 'EXPIRED',
  superseded: 'ARCHIVED',
}

const INVOICE_STAMP: Record<InvoiceStatus, StampLabel> = {
  draft: 'ARCHIVED',
  sent: 'DUE',
  paid: 'PAID',
  overdue: 'OVERDUE',
  void: 'ARCHIVED',
}

const ENGAGEMENT_STAMP: Record<EngagementStatus, StampLabel> = {
  scheduled: 'PENDING',
  active: 'UNDERWAY',
  handoff: 'IN PROG',
  safety_net: 'IN PROG',
  completed: 'COMPLETED',
  cancelled: 'ARCHIVED',
}

export type MilestoneStatus = 'pending' | 'in_progress' | 'completed' | 'skipped'

const MILESTONE_STAMP: Record<MilestoneStatus, StampLabel> = {
  pending: 'PENDING',
  in_progress: 'IN PROG',
  completed: 'COMPLETED',
  skipped: 'ARCHIVED',
}

export function resolveQuoteStampLabel(status: string): StampLabel {
  return QUOTE_STAMP[status as QuoteStatus] ?? 'ARCHIVED'
}

export function resolveInvoiceStampLabel(status: string): StampLabel {
  return INVOICE_STAMP[status as InvoiceStatus] ?? 'ARCHIVED'
}

export function resolveEngagementStampLabel(status: string): StampLabel {
  return ENGAGEMENT_STAMP[status as EngagementStatus] ?? 'ARCHIVED'
}

export function resolveMilestoneStampLabel(status: string): StampLabel {
  return MILESTONE_STAMP[status as MilestoneStatus] ?? 'ARCHIVED'
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
  info: 'bg-[color:var(--ss-color-primary)] text-white',
  success: 'bg-[color:var(--ss-color-complete)] text-white',
  danger: 'bg-[color:var(--ss-color-error)] text-white',
  warning: 'bg-[color:var(--ss-color-attention)] text-white',
  neutral: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',
  // Plainspoken bill-of-lading register: box-outlined stamp with ink text,
  // transparent background. Visually distinguishes archived/expired states
  // from the filled "attention-needed" tones above.
  outline:
    'bg-transparent text-[color:var(--ss-color-text-primary)] border-2 border-[color:var(--ss-color-text-primary)]',
}
