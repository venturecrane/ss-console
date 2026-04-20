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
  'inline-flex items-center px-2 py-1 rounded-[var(--radius-badge)] ' +
  'font-mono text-label uppercase tracking-[var(--text-label--letter-spacing)] font-semibold whitespace-nowrap'

const TONE: Record<string, string> = {
  // Engagement lifecycle
  scheduled: 'bg-[color:var(--color-primary)] text-white',
  active: 'bg-[color:var(--color-complete)] text-white',
  completed: 'bg-[color:var(--color-complete)] text-white',
  handoff: 'bg-[color:var(--color-primary)] text-white',
  safety_net: 'bg-[color:var(--color-attention)] text-white',
  cancelled: 'bg-[color:var(--color-border)] text-[color:var(--color-text-secondary)]',

  // Lead lifecycle
  disqualified: 'bg-[color:var(--color-error)] text-white',
  converted: 'bg-[color:var(--color-complete)] text-white',

  // Quote lifecycle
  draft: 'bg-[color:var(--color-border)] text-[color:var(--color-text-secondary)]',
  sent: 'bg-[color:var(--color-primary)] text-white',
  accepted: 'bg-[color:var(--color-complete)] text-white',
  declined: 'bg-[color:var(--color-error)] text-white',
  expired: 'bg-[color:var(--color-attention)] text-white',
  superseded: 'bg-[color:var(--color-border)] text-[color:var(--color-text-secondary)]',

  // Invoice lifecycle
  paid: 'bg-[color:var(--color-complete)] text-white',
  overdue: 'bg-[color:var(--color-error)] text-white',
  void: 'bg-[color:var(--color-border)] text-[color:var(--color-text-secondary)]',
}

const FALLBACK = 'bg-[color:var(--color-border)] text-[color:var(--color-text-secondary)]'

export function statusBadgeClass(status: string): string {
  return `${STRUCTURE} ${TONE[status] ?? FALLBACK}`
}
