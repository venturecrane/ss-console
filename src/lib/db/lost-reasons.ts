/**
 * Structured Lost-reason taxonomy for the admin pipeline.
 *
 * When an entity transitions to the `lost` stage, the admin picks one of
 * these codes. The code is stored in the `metadata` JSON on the
 * `stage_change` context entry produced by {@link transitionStage}:
 *
 *     metadata = {
 *       from: EntityStage,
 *       to: 'lost',
 *       reason: string,       // free-text (admin jargon)
 *       lost_reason: LostReasonCode,
 *       lost_detail?: string, // optional operator note
 *     }
 *
 * These codes are admin jargon — they power filtering on the Lost tab and
 * a future "why we lost" review. They are NEVER rendered to clients. Do
 * not reuse these strings in any portal / marketing surface.
 *
 * Flow B is in learning mode. Adding / renaming codes is fine — just
 * keep this file as the single source of truth and update the type.
 */

export type LostReasonCode =
  | 'not-a-fit'
  | 'no-budget'
  | 'no-response'
  | 'declined-quote'
  | 'unreachable'
  | 'wrong-contact'
  | 'other'

export interface LostReasonDef {
  value: LostReasonCode
  label: string
  /** Short prose we can surface next to the chip when helpful. */
  description: string
}

export const LOST_REASONS: LostReasonDef[] = [
  {
    value: 'not-a-fit',
    label: 'Not a fit',
    description: 'Outside our ICP or scope — wrong industry, size, or problem.',
  },
  {
    value: 'no-budget',
    label: 'No budget',
    description: 'Interested but cannot fund the engagement right now.',
  },
  {
    value: 'no-response',
    label: 'No response',
    description: 'Reached a live contact once, then went dark.',
  },
  {
    value: 'declined-quote',
    label: 'Declined quote',
    description: 'Received a proposal and declined.',
  },
  {
    value: 'unreachable',
    label: 'Unreachable',
    description: 'Never made contact — bad number, bounced email, etc.',
  },
  {
    value: 'wrong-contact',
    label: 'Wrong contact',
    description: 'Reached someone, but not the decision-maker, and could not route.',
  },
  {
    value: 'other',
    label: 'Other',
    description: 'Anything else — use the detail field to explain.',
  },
]

const VALID_CODES = new Set<LostReasonCode>(LOST_REASONS.map((r) => r.value))

export function isLostReasonCode(value: unknown): value is LostReasonCode {
  return typeof value === 'string' && VALID_CODES.has(value as LostReasonCode)
}

export function lostReasonLabel(code: string | null | undefined): string | null {
  if (!code) return null
  const match = LOST_REASONS.find((r) => r.value === code)
  return match ? match.label : null
}

/**
 * Admin-surface chip tone for each reason. Admin UI uses raw Tailwind
 * (see `docs/style/UI-PATTERNS.md` — portal uses StatusPill tokens; admin
 * stays on raw Tailwind per the source-of-truth contract). Tones reflect
 * recoverability: outreach issues (recoverable, bluish) vs.
 * disqualified (grey) vs. explicit no (red).
 */
export function lostReasonChipClass(code: string | null | undefined): string {
  switch (code) {
    case 'not-a-fit':
      return 'bg-slate-100 text-slate-700'
    case 'no-budget':
      return 'bg-amber-100 text-amber-700'
    case 'no-response':
      return 'bg-blue-100 text-blue-700'
    case 'declined-quote':
      return 'bg-red-100 text-red-700'
    case 'unreachable':
      return 'bg-indigo-100 text-indigo-700'
    case 'wrong-contact':
      return 'bg-purple-100 text-purple-700'
    case 'other':
      return 'bg-[color:var(--color-border-subtle)] text-[color:var(--color-text-secondary)]'
    default:
      return 'bg-[color:var(--color-border-subtle)] text-[color:var(--color-text-secondary)]'
  }
}
