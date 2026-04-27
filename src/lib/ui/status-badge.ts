/**
 * Returns the full Tailwind class list for an admin status tag.
 *
 * Architect's Studio identity: rectangular filled tag with white text,
 * mono caps, tracked letter-spacing, 2px radius. Matches the portal
 * StatusPill shape (src/components/portal/StatusPill.astro + TONE_CLASS
 * in src/lib/portal/status.ts) so admin and portal read as one product.
 *
 * Callers use the output directly — do not add padding, radius, or font
 * classes on top:
 *
 *   <span class={statusBadgeClass(status)}>{label}</span>
 */

const STRUCTURE =
  'inline-flex items-center px-2 py-1 rounded-[var(--ss-radius-badge)] ' +
  'font-mono text-label uppercase tracking-[var(--ss-text-letter-spacing-label)] font-semibold whitespace-nowrap'

const TONE: Record<string, string> = {
  // Engagement lifecycle
  scheduled: 'bg-[color:var(--ss-color-primary)] text-white',
  active: 'bg-[color:var(--ss-color-complete)] text-white',
  completed: 'bg-[color:var(--ss-color-complete)] text-white',
  handoff: 'bg-[color:var(--ss-color-primary)] text-white',
  safety_net: 'bg-[color:var(--ss-color-attention)] text-white',
  cancelled: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',

  // Lead lifecycle
  disqualified: 'bg-[color:var(--ss-color-error)] text-white',
  converted: 'bg-[color:var(--ss-color-complete)] text-white',

  // Quote lifecycle
  draft: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',
  sent: 'bg-[color:var(--ss-color-primary)] text-white',
  accepted: 'bg-[color:var(--ss-color-complete)] text-white',
  declined: 'bg-[color:var(--ss-color-error)] text-white',
  expired: 'bg-[color:var(--ss-color-attention)] text-white',
  superseded: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',

  // Invoice lifecycle
  paid: 'bg-[color:var(--ss-color-complete)] text-white',
  overdue: 'bg-[color:var(--ss-color-error)] text-white',
  void: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',

  // Milestone lifecycle (`completed` shared with engagement above)
  pending: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',
  in_progress: 'bg-[color:var(--ss-color-primary)] text-white',
  skipped: 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]',
}

const FALLBACK = 'bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]'

export function statusBadgeClass(status: string): string {
  return `${STRUCTURE} ${TONE[status] ?? FALLBACK}`
}
