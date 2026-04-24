/**
 * Admin action-button class system. Implements `docs/style/UI-PATTERNS.md`
 * Rule 3 (one primary per view) by collapsing the four ad-hoc colour
 * choices that had drifted across the entity detail toolbar into three
 * named variants:
 *
 *   primary     — the stage's forward action (Promote, Mark as Proposing, …).
 *                 Solid brand colour. Should appear at most once per view.
 *   destructive — Lost / Dismiss. Subtle by default so it doesn't compete
 *                 with the primary; click reveals the structured picker.
 *   ghost       — every secondary action (Re-enrich, Log reply, Add note,
 *                 New quote, Send booking link from a meetings-stage row).
 *                 Bordered, dark text, white background. Indicates "you
 *                 *can* do this" without competing with the primary.
 *
 * Returns the full Tailwind class list — callers spread directly onto
 * `<button>` / `<summary>` and never add padding, radius, or hover state
 * on top.
 */

export type AdminActionVariant = 'primary' | 'destructive' | 'ghost'

const STRUCTURE =
  'inline-flex items-center text-xs px-3 py-1.5 rounded-[var(--radius-card)] transition-colors cursor-pointer select-none'

const TONE: Record<AdminActionVariant, string> = {
  primary: 'bg-[color:var(--color-primary)] text-white hover:bg-[color:var(--color-primary-hover)]',
  destructive:
    'bg-[color:var(--color-border-subtle)] text-[color:var(--color-text-secondary)] hover:bg-red-50 hover:text-[color:var(--color-error)]',
  ghost:
    'bg-white border border-[color:var(--color-border)] text-[color:var(--color-text-primary)] hover:bg-[color:var(--color-background)]',
}

export function adminActionButtonClass(variant: AdminActionVariant): string {
  return `${STRUCTURE} ${TONE[variant]}`
}
